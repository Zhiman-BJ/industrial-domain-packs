"""Exercise the real portable ORFS proof callback, including failures."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("case,passed", [("same", True), ("changed", False), ("missing_model", False), ("undriven", False)])
def test_native_orfs_lec_never_accepts_changed_or_unmodeled_outputs(tmp_path, case, passed):
    if not shutil.which("yosys"):
        pytest.skip("Native Yosys not installed")
    (tmp_path / "cells.lib").write_text('''library(test) {
      cell(BUF) { area: 1; pin(A) { direction: input; }
        pin(Y) { direction: output; function: "A"; } }
      cell(INV) { area: 1; pin(A) { direction: input; }
        pin(Y) { direction: output; function: "!A"; } }
      cell(UNMODELED) { area: 1; pin(A) { direction: input; }
        pin(Y) { direction: output; } }
    }''')
    (tmp_path / "gold.v").write_text("module top(input a, output q); BUF b(.A(a),.Y(q)); endmodule")
    cell = {"same": "BUF", "changed": "INV", "missing_model": "UNMODELED"}.get(case)
    body = f"{cell} b(.A(a),.Y(q));" if cell else ""
    (tmp_path / "gate.v").write_text("module top(input a, output q); " + body + " endmodule")
    (tmp_path / "lec.yml").write_text(yaml.safe_dump({
        "format": "verilog", "input_paths": ["gold.v", "gate.v"],
        "liberty_files": ["cells.lib"], "log_file": "proof.log",
    }))
    helper = Path(__file__).resolve().parents[1] / "tools/orfs-lec.py"
    run = subprocess.run([sys.executable, str(helper), "--config", "lec.yml"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=30)
    assert (run.returncode == 0) == passed, run.stdout + run.stderr
    assert (tmp_path / "proof.log").is_file()
    if passed:
        assert "Equivalence successfully proven!" in (tmp_path / "proof.log").read_text()
