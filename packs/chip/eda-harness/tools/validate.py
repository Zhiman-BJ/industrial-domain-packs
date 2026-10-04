"""Host interface: one RTL task, one isolated ephemeral seven-tool container."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(64, f"{self.prog}: {message}\n")


def fail(message, code):
    print(message, file=sys.stderr)
    return code


def main(argv=None):
    parser = Parser(
        prog="./validate.sh",
        description="Validate seven-tool interoperability on a small RTL design; not signoff",
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--image", default="eda-harness-tools:dev")
    parser.add_argument("--top", default="counter")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", args.top) or args.timeout < 1:
        parser.error("top must be an HDL identifier and timeout must be positive")
    if not args.input.is_file() or not os.access(args.input, os.R_OK):
        return fail("Input file is missing or unreadable", 66)
    if args.input.suffix not in (".v", ".sv"):
        return fail("Expected a single .v or .sv source file", 65)
    source = args.input.resolve()
    if any(c in str(source) for c in (",", "\n", "\r")):
        parser.error("Input path contains unsupported mount characters")
    try:
        if args.output:
            if args.output.is_symlink() or (
                args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir()))
            ):
                return fail("Output must be a new or empty directory", 73)
            output = args.output.resolve()
            if any(c in str(output) for c in (",", "\n", "\r")):
                return fail("Output path contains unsupported mount characters", 73)
            output.mkdir(parents=True, exist_ok=True)
        else:
            output = Path(tempfile.mkdtemp(prefix=f"eda-{source.stem}-"))
    except OSError as error:
        return fail(str(error), 73)
    print(f"Output directory: {output}", flush=True)
    log = (output / "workload.log").open("w") if args.output else None
    start, code, image_id = time.monotonic(), 1, None
    name = "eda-validate-" + uuid.uuid4().hex
    process = None

    def message(text):
        print(text, end="", flush=True)
        if log:
            log.write(text)
            log.flush()

    try:
        inspection = subprocess.run(
            ["docker", "image", "inspect", args.image, "--format", "{{json .}}"],
            text=True,
            capture_output=True,
            timeout=30,
        )
        if inspection.returncode:
            raise ValueError(f"Image unavailable: {args.image}; build Dockerfile.tools first")
        meta = json.loads(inspection.stdout)
        if (meta["Os"], meta["Architecture"]) != ("linux", "amd64"):
            raise ValueError("Tool validation requires linux/amd64")
        image_id = meta["Id"]
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--cpus",
            "2",
            "--memory",
            "4g",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=128m",
            "--mount",
            f"type=bind,src={source},dst=/input/design.sv,readonly",
            "--mount",
            f"type=bind,src={output},dst=/output",
            "--workdir",
            "/output",
            "--entrypoint",
            "python3",
            image_id,
            "/opt/eda-tools/smoke.py",
            "--input",
            "/input/design.sv",
            "--top",
            args.top,
            "--output",
            "/output",
        ]
        message(f"Image: {image_id}; platform: linux/amd64\n")
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
        )

        def stream():
            for line in process.stdout:
                message(line)

        reader = threading.Thread(target=stream, daemon=True)
        reader.start()
        try:
            code = process.wait(timeout=args.timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            code = 124 if isinstance(error, subprocess.TimeoutExpired) else 130
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=20)
            process.kill()
            process.wait()
            message("Validation timed out or was interrupted\n")
        reader.join(timeout=5)
        if code == 0:
            for rel in ("routed.gds", "netlist.json", "placed.odb", "streamout-checks.json"):
                path = output / rel
                if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                    raise ValueError(f"Missing/unsafe output: {rel}")
            if args.top not in json.loads((output / "netlist.json").read_text())["modules"]:
                raise ValueError("Netlist top was not preserved")
            checks = json.loads((output / "streamout-checks.json").read_text())["checks"]
            if not checks or not all(c.get("passed") is True for c in checks):
                raise ValueError("Downstream GDS check failed")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        code = 1
        message(f"Validation failed: {error}\n")
    finally:
        if log:
            log.close()
        result = {
            "schema": "zhiman.eval/software-validation-result/1.0",
            "status": "succeeded" if code == 0 else "failed",
            "exit_code": code,
            "software": "eda-harness-tools",
            "version": image_id or args.image,
            "platform": "linux/amd64",
            "duration_ms": round((time.monotonic() - start) * 1000),
            "artifact": "routed.gds" if code == 0 else None,
            "log": "workload.log" if args.output else None,
            "checks": ["lint", "mapped synthesis", "placement", "GDS round-trip"] if code == 0 else [],
        }
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
