"""Real Magic extraction, Netgen positive/negative LVS, GTKWave GUI and FST readback."""

import json
import os
import shutil
import subprocess
from pathlib import Path


def run(command, cwd, env):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    (cwd / (Path(command[0]).name + ".log")).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise ValueError(f"{command}: {result.stdout[-1000:]} {result.stderr[-1000:]}")


def smoke(output, fixture=Path("/opt/eda-tools/verification")):
    output.mkdir(parents=True, exist_ok=True)
    source = output / "input"
    shutil.copytree(fixture, source)
    extract = output / "deps/layout.extract"
    extract.mkdir(parents=True)
    env = {
        **os.environ,
        "EDA_INPUT_DIR": str(source),
        "EDA_DEPS_DIR": str(output / "deps"),
        "HOME": str(output),
        "GSETTINGS_BACKEND": "memory",
    }
    run(["magic", "-dnull", "-noconsole", "-T", "scmos", str(source / "scripts/extract.tcl")], extract, env)
    spice = (extract / "extracted.spice").read_text()
    if "nfet w=4u l=2u" not in spice or ".subckt device D G S" not in spice:
        raise ValueError("Magic did not extract the expected transistor and ports")
    (extract / "netlist.extracted").write_text(spice)
    outcomes = {}
    reference = source / "reference/device.spice"
    original = reference.read_text()
    for name, content, expected in [
        ("match", original, True),
        ("wrong-width", original.replace("W=4u", "W=8u"), False),
        ("shorted-gate", original.replace("M1 D G S", "M1 D S S"), False),
    ]:
        reference.write_text(content)
        work = output / name
        work.mkdir()
        run(["netgen", "-batch", "source", str(source / "scripts/compare.tcl")], work, env)
        checks = json.loads((work / "checks.json").read_text())["checks"]
        actual = checks[0]["passed"]
        if actual is not expected:
            raise ValueError(f"Netgen {name}: expected {expected}, got {actual}")
        outcomes[name] = actual
    reference.write_text(original)
    wave = output / "wave"
    wave.mkdir()
    run(["/bin/sh", str(source / "scripts/wave.sh")], wave, env)
    checks = json.loads((wave / "wave-checks.json").read_text())["checks"]
    if not checks or not all(c["passed"] for c in checks):
        raise ValueError("GTKWave did not load signals")
    vcd = (wave / "roundtrip.vcd").read_text()
    if "count" not in vcd or "#15" not in vcd or "b0010" not in vcd:
        raise ValueError("FST round-trip lost waveform data")
    if "counter.count" not in (wave / "wave.gtkw").read_text():
        raise ValueError("GTKWave session did not save selected signals")
    run(["xvfb-run", "-a", "gtkwave", "--exit", "wave.fst", "wave.gtkw"], wave, env)
    result = {
        "magic_extraction": True,
        "netgen": outcomes,
        "gtkwave_load_save_reopen": True,
        "fst_roundtrip": True,
        "scope": "SCMOS fixture; not Nangate45 signoff",
    }
    (output / "verification-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(smoke(Path(sys.argv[1]).resolve()), indent=2))
