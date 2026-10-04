import json
import os
import subprocess

import pytest
import yaml

from eda_harness.core.models import ActionConfig
from eda_harness.core.service import Harness
from eda_harness.plugins.equivalence import observe, prepare


def parameters(**fields):
    return ActionConfig(parameters={
        "operation": "equivalence", "top": "top",
        "gold_sources": [{"path": "gold.sv"}], "gate_sources": [{"path": "gate.v"}],
        **fields,
    }).parameters


def test_memory_and_reset_normalization_and_eqy_partition_budget(tmp_path):
    def script(name, body):
        (tmp_path / name).write_text(body)
        return name
    result = prepare(parameters(), lambda r: "/inputs/" + r.path, script)
    body = (tmp_path / "equivalence.ys").read_text()
    assert "async2sync" in body and "dffunmap" in body and "memory_map" in body
    assert "equiv_status -assert" in body
    assert result.outputs == {"report.equivalence": "equivalence.log"}
    result = prepare(parameters(engine="eqy"), lambda r: "/inputs/" + r.path, script)
    body = (tmp_path / "equivalence.eqy").read_text()
    assert "insbuf off" in body and "memory_map" in body and "group *" in body
    assert "timeout 60" in body and result.commands[0][:3] == ["eqy", "-j", "1"]


def test_partial_empty_and_failed_proofs_never_pass():
    p = parameters()
    assert observe(p, "Of those cells 8 are proven and 0 are unproven.\nEquivalence successfully proven!")[0]
    assert not observe(p, "Of those cells 8 are proven and 1 are unproven.\nEquivalence successfully proven!")[0]
    assert not observe(p, "Of those cells 0 are proven and 0 are unproven.\nEquivalence successfully proven!")[0]
    p = parameters(engine="eqy")
    proof = "Proved equivalence of partition 'top.q' using strategy 'sat'\n"
    assert not observe(p, proof)[0]
    assert observe(p, proof + "Successfully proved designs equivalent")[0]
    assert not observe(p, proof + "Successfully proved equivalence!\nERROR: Failed to partition")[0]


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("backend", ["local-yosys", "docker-eqy-map"])
def test_native_memory_equivalence_accepts_real_gate_and_rejects_mutation(tmp_path, monkeypatch, changed, backend):
    import shutil
    if not shutil.which("yosys"):
        pytest.skip("native yosys unavailable")
    docker = backend.startswith("docker-")
    image = os.environ.get("EDA_EQY_TEST_IMAGE")
    if docker and not image:
        pytest.skip("Set EDA_EQY_TEST_IMAGE to an installed Yosys/EQY/SBY/Z3 image")
    monkeypatch.setenv("EDA_RESOURCE_STATE_DIR", str(tmp_path / "leases"))
    gold = """module top(input clk, input rst_n, input wr, input [1:0] addr,
    input [7:0] data, output reg [7:0] q, output reg [3:0] count);
    reg [7:0] mem[0:3];
    always @(posedge clk) begin
        if (wr) mem[addr] <= data;
        q <= mem[addr];
    end
    always @(posedge clk or negedge rst_n)
        if (!rst_n) count <= 0; else count <= count + 1'b1;
    endmodule
    """
    (tmp_path / "gold.sv").write_text(gold)
    (tmp_path / "synth-input.sv").write_text(gold.replace("<= data;", "<= data ^ 8'h01;") if changed else gold)
    synthesis = subprocess.run(["yosys", "-Q", "-T", "-p",
        "read_verilog -sv synth-input.sv; hierarchy -top top; proc; memory; opt; techmap; opt; write_verilog gate.v"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert synthesis.returncode == 0, synthesis.stderr
    config = {
        "name": "native-equiv", "top": "top",
        "inputs": {"rtl": ["gold.sv", "gate.v"]},
        "runtime": {"kind": "docker" if docker else "local", **({"image": image} if docker else {}),
                    "resources": {"cpu": 1, "memory_gb": 2, "timeout_seconds": 90}},
        "workflow": {"actions": [{
            "id": "verify.equivalence", "tool": "yosys", "dependencies": [],
            "input_types": ["rtl"], "artifact_types": ["report.equivalence"],
        }]},
        "actions": {"verify.equivalence": {"parameters": parameters(
            engine="eqy" if docker else "yosys",
            partition_timeout_seconds=20, depth=6,
        ).model_dump()}},
        "required_verification": ["verify.equivalence"],
    }
    (tmp_path / "eda.yaml").write_text(yaml.safe_dump(config))
    h = Harness(tmp_path)
    run = h.submit("verify.equivalence", background=False)
    if changed:
        assert run["status"] == "FAILED", json.dumps(run)
        assert h.state() is None
    else:
        assert run["status"] == "SUCCESS", json.dumps(run)
        assert run["design_status"] == "PASS"
        assert h.state()["results"]["verify.equivalence"]["verification"]["status"] == "PASS"
