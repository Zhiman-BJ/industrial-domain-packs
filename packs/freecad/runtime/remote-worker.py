#!/usr/bin/env python3
"""Bounded FreeCAD Linux worker for the qualification-pending profile.

The worker accepts only the four pack tool ids and executes the checked-in
``native.py`` bridge.  It never evaluates caller supplied Python, shell
commands, paths outside the staged snapshot, or FreeCAD expressions.
"""

import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path


NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
OPS = {
    "box": ("length", "width", "height"),
    "cylinder": ("radius", "height"),
    "sketch_pad": ("profile", "length", "width", "radius", "height"),
    "cut": ("base", "tool"),
    "fuse": ("base", "tool"),
    "common": ("base", "tool"),
    "hole": ("base", "radius", "height"),
}
TOOL_OPERATION = {
    "cad.freecad.build": "build",
    "cad.freecad.inspect": "inspect",
    "cad.freecad.export": "export",
    "cad.freecad.edit": "edit",
}
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_RECIPE_BYTES = 256 * 1024
FREECAD = os.environ.get("FREECADCMD", "/opt/freecad/usr/bin/freecadcmd")


def fail(message):
    raise ValueError(message)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def object_keys(value, allowed):
    if not isinstance(value, dict) or any(key not in allowed for key in value):
        fail("Unexpected or missing CAD fields.")


def validate_recipe(recipe):
    object_keys(recipe, {"parameters", "features", "result"})
    parameters = recipe.get("parameters") or {}
    if not isinstance(parameters, dict) or len(parameters) > 100:
        fail("CAD parameter limit exceeded.")
    for name, value in parameters.items():
        if not NAME.fullmatch(name) or not finite_number(value) or abs(value) > 10000:
            fail("CAD parameters must be named finite dimensions in millimetres.")

    def dimension(value):
        resolved = parameters.get(value) if isinstance(value, str) else value
        if not finite_number(resolved) or resolved <= 0 or resolved > 10000:
            fail("CAD dimensions must be positive, at most 10000 mm; parameter references must exist.")
        return resolved

    features = recipe.get("features")
    if not isinstance(features, list) or not 1 <= len(features) <= 50:
        fail("CAD recipe needs 1-50 features.")
    names = set()
    normalized = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("op") not in OPS:
            fail("Unsupported CAD operation.")
        allowed = {"id", "op", "origin", *OPS[feature["op"]]}
        object_keys(feature, allowed)
        feature_id = feature.get("id")
        if (
            not isinstance(feature_id, str)
            or not NAME.fullmatch(feature_id)
            or feature_id in {"Parameters", "HarnessResult"}
            or feature_id in names
        ):
            fail("CAD feature IDs must be unique safe names.")
        origin = feature.get("origin")
        if origin is not None and (
            not isinstance(origin, list)
            or len(origin) != 3
            or any(not finite_number(value) or abs(value) > 10000 for value in origin)
        ):
            fail("CAD origins need three bounded numeric coordinates.")
        for key in ("base", "tool"):
            if key in OPS[feature["op"]] and feature.get(key) not in names:
                fail("CAD operands must reference earlier features.")
        if feature["op"] == "sketch_pad":
            profile = feature.get("profile")
            dimensions = (
                ("length", "width", "height")
                if profile == "rectangle"
                else ("radius", "height")
                if profile == "circle"
                else None
            )
            if dimensions is None:
                fail("Sketch profile must be rectangle or circle.")
        else:
            dimensions = tuple(key for key in OPS[feature["op"]] if key not in {"base", "tool"})
        resolved = dict(feature)
        for key in dimensions:
            resolved[key] = dimension(feature.get(key))
        normalized.append(resolved)
        names.add(feature_id)
    if recipe.get("result") not in names:
        fail("CAD result must name a feature.")
    return {"parameters": parameters, "features": normalized, "result": recipe["result"]}


def validate_expect(expect):
    if expect is None:
        return
    object_keys(expect, {"volume", "bounds", "solids", "tolerance"})
    for key in ("volume", "tolerance"):
        if key in expect and (not finite_number(expect[key]) or expect[key] <= 0):
            fail("CAD expectations need positive finite numbers.")
    if "solids" in expect and (
        not isinstance(expect["solids"], int) or isinstance(expect["solids"], bool) or not 1 <= expect["solids"] <= 50
    ):
        fail("Invalid expected solid count.")
    if "bounds" in expect and (
        not isinstance(expect["bounds"], list)
        or len(expect["bounds"]) != 3
        or any(not finite_number(value) or value <= 0 for value in expect["bounds"])
    ):
        fail("Expected bounds need three positive dimensions.")


def validate_input(operation, inputs):
    if not isinstance(inputs, dict):
        fail("CAD arguments must be an object.")
    allowed = {"recipe", "expect"} if operation == "build" else {"file", "expect"}
    if operation == "edit":
        allowed = {"file", "changes", "expect"}
    object_keys(inputs, allowed)
    validate_expect(inputs.get("expect"))
    if operation == "build":
        validate_recipe(inputs.get("recipe"))
        return inputs["recipe"]
    file = inputs.get("file")
    if not isinstance(file, str) or not file or len(file) > 1024:
        fail("CAD file must be a project-relative FCStd/STEP path.")
    if operation == "edit":
        changes = inputs.get("changes")
        if not isinstance(changes, dict) or not changes or any(key not in {"parameters", "features"} for key in changes):
            fail("CAD edit needs parameter or feature changes.")
    return None


def safe_input(input_dir, relative):
    if Path(relative).is_absolute() or ".." in Path(relative).parts or not re.search(r"\.(fcstd|step|stp)$", relative, re.I):
        fail("CAD input must be a project-relative FCStd/STEP file.")
    candidate = (input_dir / relative).resolve()
    root = input_dir.resolve()
    if root not in candidate.parents or not candidate.is_file() or candidate.is_symlink():
        fail("CAD input escaped the staged project.")
    if candidate.stat().st_size > MAX_FILE_BYTES:
        fail("CAD input exceeded 16 MiB.")
    return candidate


def read_json(path, limit=MAX_RECIPE_BYTES):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
        fail("CAD companion file is missing or exceeds its size bound.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        fail("CAD companion JSON is invalid: %s" % error)


def apply_changes(recipe, changes):
    validate_recipe(recipe)
    object_keys(changes, {"parameters", "features"})
    next_recipe = json.loads(json.dumps(recipe))
    if "parameters" in changes:
        parameters = changes["parameters"]
        if not isinstance(parameters, dict) or not parameters or len(parameters) > 100:
            fail("Invalid CAD parameter changes.")
        for name, value in parameters.items():
            if not NAME.fullmatch(name) or not finite_number(value) or abs(value) > 10000 or name not in next_recipe["parameters"]:
                fail("Unknown or invalid CAD parameter change.")
            next_recipe["parameters"][name] = value
    changed = set()
    for patch in changes.get("features", []):
        if not isinstance(patch, dict) or "id" not in patch or any(key not in {"id", "length", "width", "height", "radius", "profile", "origin"} for key in patch):
            fail("Invalid CAD feature patch.")
        feature_id = patch["id"]
        if feature_id in changed or not NAME.fullmatch(feature_id):
            fail("Unknown or duplicate CAD feature patch.")
        feature = next((item for item in next_recipe["features"] if item["id"] == feature_id), None)
        if feature is None:
            fail("Unknown CAD feature patch: " + feature_id)
        feature.update(patch)
        changed.add(feature_id)
    validate_recipe(next_recipe)
    return next_recipe


def copy_source(input_dir, work_dir, relative):
    source = safe_input(input_dir, relative)
    staged = work_dir / ("input" + source.suffix.lower())
    shutil.copyfile(source, staged)
    # Editing requires the hash-bound recipe and preview companions from the
    # same immutable snapshot. They are validated below before native.py runs.
    return source, staged


def run_native(work_dir, operation, payload):
    request = {"operation": operation, **payload}
    (work_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
    shutil.copyfile(Path(__file__).with_name("native.py"), work_dir / "native.py")
    home = work_dir / "home"
    temp = work_dir / "temp"
    home.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin:/opt/freecad/usr/bin",
        "HOME": str(home),
        "TMPDIR": str(temp),
        "FREECAD_USER_HOME": str(home),
        "FREECAD_USER_DATA": str(home / "data"),
        "FREECAD_USER_TEMP": str(temp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "QT_QPA_PLATFORM": "offscreen",
    }
    (home / "data").mkdir(exist_ok=True)
    try:
        process = subprocess.Popen(
            [FREECAD, "--keep-deprecated-paths", str(work_dir / "native.py")],
            cwd=work_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
        try:
            output, _ = process.communicate(timeout=300)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            output, _ = process.communicate()
        with (work_dir / "tool.log").open("a", encoding="utf-8", errors="replace") as log:
            log.write(output[-1024 * 1024 :])
            log.write("\n")
        return process.returncode == 0 and not timed_out
    except (OSError, ValueError) as error:
        with (work_dir / "tool.log").open("a", encoding="utf-8") as log:
            log.write("native launch failed: %s\n" % error)
        return False


def write_preview(work_dir, recipe):
    if recipe is None or not (work_dir / "readback.json").is_file():
        return
    names = ["model.FCStd", "model.step", "model.stl", "model.brep", "model.cad-sketches.json", "model.recipe.json"]
    hashes = {name: hashlib.sha256((work_dir / name).read_bytes()).hexdigest() for name in names if (work_dir / name).is_file()}
    (work_dir / "model.cad-preview.json").write_text(json.dumps({"schemaVersion": 1, "hashes": hashes, "readback": read_json(work_dir / "readback.json", 131072)}, indent=2), encoding="utf-8")


def main():
    if len(sys.argv) != 3:
        print("usage: remote-worker.py INPUT_DIR WORK_DIR", file=sys.stderr)
        return 2
    input_dir, work_dir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    try:
        request = read_json(work_dir / "request.json", 1024 * 1024)
        tool_id = request.get("toolId")
        operation = TOOL_OPERATION.get(tool_id)
        if operation is None:
            fail("tool is not in the FreeCAD Linux profile")
        arguments = request.get("arguments")
        recipe = validate_input(operation, arguments)
        source_hash = None
        if operation in {"inspect", "export", "edit"}:
            source, staged = copy_source(input_dir, work_dir, arguments["file"])
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            if operation == "edit":
                companion = source.with_suffix(".recipe.json")
                preview = source.with_suffix(".cad-preview.json")
                recipe_doc = read_json(companion)
                preview_doc = read_json(preview, 2 * MAX_RECIPE_BYTES)
                hashes = preview_doc.get("hashes", {})
                if (preview_doc.get("schemaVersion") != 1
                    or hashes.get(source.name) != source_hash
                    or hashes.get(companion.name) != hashlib.sha256(companion.read_bytes()).hexdigest()):
                    fail("CAD editable recipe hash does not match the staged model.")
                recipe = apply_changes(recipe_doc, arguments["changes"])
            native_payload = {"recipe": validate_recipe(recipe)} if operation == "edit" else {"file": str(staged)}
        else:
            native_payload = {"recipe": validate_recipe(recipe)}
            (work_dir / "model.recipe.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")
        first_ok = run_native(work_dir, operation, native_payload)
        second_ok = first_ok and run_native(work_dir, "verify", {})
        if second_ok and operation == "edit":
            (work_dir / "model.recipe.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")
        write_preview(work_dir, recipe)
        report = {
            "executionSucceeded": bool(first_ok and second_ok),
            "operation": operation,
            "inputUnchanged": True,
            "sourceSha256": source_hash,
            "expect": arguments.get("expect", {}),
            "nativeVerification": {"status": "PASS" if second_ok else "FAIL"},
        }
        (work_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0 if report["executionSucceeded"] else 1
    except Exception as error:  # report failures as evidence; never claim success
        (work_dir / "error.json").write_text(json.dumps({"error": str(error)}, indent=2), encoding="utf-8")
        (work_dir / "report.json").write_text(json.dumps({"executionSucceeded": False, "inputUnchanged": True, "diagnostic": str(error)}), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
