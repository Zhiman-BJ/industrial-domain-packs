"""Local image acceptance suite; run after building, not as hosted image CI."""

import argparse
import concurrent.futures
import json
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def invoke(*args, expected=0):
    run = subprocess.run([str(REPO / "validate.sh"), *map(str, args)], capture_output=True, text=True)
    assert run.returncode == expected, (run.returncode, run.stdout[-3000:], run.stderr[-3000:])
    return run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="eda-harness-tools:dev")
    args = parser.parse_args()
    source = REPO / "examples/counter/rtl/counter.sv"
    root = Path(tempfile.mkdtemp(prefix="eda-image-contract-"))
    invoke("--help")
    invoke(expected=64)
    invoke(root / "missing.sv", expected=66)
    invalid = root / "invalid.sv"
    invalid.write_text("not valid systemverilog!")
    unsupported = root / "input.txt"
    unsupported.write_text("test")
    invoke(unsupported, expected=65)
    occupied = root / "occupied"
    occupied.mkdir()
    (occupied / "keep").write_text("preserved")
    invoke(source, "--output", occupied, expected=73)
    bad = invoke(invalid, "--image", args.image, "--output", root / "bad", expected=1)
    assert "Error" in bad.stdout or "error" in bad.stdout
    assert json.loads((root / "bad/result.json").read_text())["exit_code"] == 1
    invoke(source, "--image", args.image, "--output", root / "explicit")
    result = json.loads((root / "explicit/result.json").read_text())
    assert result["log"] == "workload.log" and result["status"] == "succeeded"
    assert (root / "explicit/workload.log").stat().st_size > 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: invoke(source, "--image", args.image), range(2)))
    paths = [Path(r.stdout.split("Output directory: ", 1)[1].splitlines()[0]) for r in runs]
    assert len(set(paths)) == 2
    for out in paths:
        result = json.loads((out / "result.json").read_text())
        assert result["log"] is None and not (out / "workload.log").exists()
        assert result["status"] == "succeeded" and (out / result["artifact"]).stat().st_size > 0
    print(
        json.dumps(
            {
                "status": "PASS",
                "concurrency": 2,
                "evidence": str(root),
                "concurrent_outputs": list(map(str, paths)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
