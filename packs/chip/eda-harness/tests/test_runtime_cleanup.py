import json
import subprocess
from unittest.mock import Mock

import pytest

from eda_harness.core.models import RuntimeConfig
from eda_harness.runtimes import execution as ex
from eda_harness.runtimes.diagnostics import diagnose


def runtime(tmp_path):
    return ex.Runtime(RuntimeConfig(kind="docker", image="audit:test"),
                      {"image_id": "sha256:test"}, "eda-audit-0", tmp_path, tmp_path, tmp_path)


def process(code):
    proc = Mock(pid=987654, returncode=code)
    proc.poll.return_value = code
    return proc


def test_client_exit_always_removes_surviving_container(tmp_path, monkeypatch):
    proc = process(1)
    monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(ex, "inspect_container", lambda name: {"Running": True})
    cleanup = Mock(return_value={"confirmed": True})
    stop = Mock()
    monkeypatch.setattr(ex, "cleanup_container", cleanup)
    monkeypatch.setattr(ex, "stop_process", stop)
    result = runtime(tmp_path).execute(["tool"], {}, tmp_path / "tool.log", lambda: False, lambda *a: None)
    assert result.status == "FAILED"
    cleanup.assert_called_once_with("eda-audit-0")
    stop.assert_called_once_with(proc)
    assert result.container_state["Running"]
    assert "cleanup:" in (tmp_path / "tool.log").read_text()


def test_cleanup_timeout_still_stops_local_client(tmp_path, monkeypatch):
    proc = process(None)
    monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(ex, "inspect_container", lambda name: {"inspection_error": "daemon offline"})
    def unavailable(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 5)
    monkeypatch.setattr(ex.subprocess, "run", unavailable)
    stop = Mock()
    monkeypatch.setattr(ex, "stop_process", stop)
    result = runtime(tmp_path).execute(["tool"], {}, tmp_path / "tool.log", lambda: True, lambda *a: None)
    assert result.status == "CANCELLED"
    assert not result.cleanup["confirmed"]
    assert result.cleanup["attempts"] == 2
    stop.assert_called_once_with(proc)


def test_started_callback_failure_cannot_leave_container(tmp_path, monkeypatch):
    proc = process(None)
    monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(ex, "inspect_container", lambda name: {"Running": True})
    cleanup = Mock(return_value={"confirmed": True})
    stop = Mock()
    monkeypatch.setattr(ex, "cleanup_container", cleanup)
    monkeypatch.setattr(ex, "stop_process", stop)
    def failed_callback(*args):
        raise OSError("run database unavailable")
    with pytest.raises(OSError, match="database"):
        runtime(tmp_path).execute(["tool"], {}, tmp_path / "tool.log", lambda: False, failed_callback)
    assert cleanup.called and stop.called


def test_successful_client_with_running_container_never_means_success(tmp_path, monkeypatch):
    monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: process(0))
    monkeypatch.setattr(ex, "inspect_container", lambda name: {"Running": True})
    monkeypatch.setattr(ex, "cleanup_container", lambda name: {"confirmed": True})
    monkeypatch.setattr(ex, "stop_process", lambda proc: None)
    result = runtime(tmp_path).execute(["tool"], {}, tmp_path / "tool.log", lambda: False, lambda *a: None)
    assert result.status == "FAILED"


def test_docker_absence_needs_daemon_evidence(monkeypatch):
    monkeypatch.setattr(ex.subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, 1, "", "cannot connect"))
    assert not ex.cleanup_container("eda-audit-0")["confirmed"]
    monkeypatch.setattr(ex.subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, 1, "", "No such container: eda-audit-0"))
    assert ex.cleanup_container("eda-audit-0")["confirmed"]


def test_probe_timeout_always_cleans_its_named_container(monkeypatch):
    calls = []
    def timeout(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    cleanup = Mock(return_value={"confirmed": True})
    monkeypatch.setattr(ex.subprocess, "run", timeout)
    monkeypatch.setattr(ex, "cleanup_container", cleanup)
    with pytest.raises(subprocess.TimeoutExpired):
        ex.run_probe(["docker", "run", "image", "sleep", "60"], timeout=1)
    cleanup.assert_called_once_with(calls[0][3])
    assert calls[0][2] == "--name"


def test_probe_cleanup_failure_is_reported_with_container_name(monkeypatch):
    monkeypatch.setattr(ex.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0))
    monkeypatch.setattr(ex, "cleanup_container", lambda name: {"confirmed": False, "errors": ["daemon offline"]})
    with pytest.raises(ValueError, match="cleanup pending: eda-probe-"):
        ex.run_probe(["docker", "run", "image", "true"], timeout=1)


def test_pending_cleanup_blocks_new_runs_and_can_be_recovered(project, monkeypatch):
    run = project.submit("lint", background=False)
    run.update(status="FAILED", cleanup_pending=True, container_name="eda-audit-0")
    project.store.put("run", run["id"], run, mutable=True)
    with pytest.raises(ValueError, match="active run"):
        project.submit("lint", background=False)
    monkeypatch.setattr("eda_harness.core.service.cleanup_container", lambda name: {"confirmed": False})
    assert project.recover_runs()["cleanup_pending"] == [run["id"]]
    assert project.get_run(run["id"])["container_name"] == "eda-audit-0"
    monkeypatch.setattr("eda_harness.core.service.cleanup_container", lambda name: {"confirmed": True})
    assert project.recover_runs()["recovered"] == [run["id"]]
    assert not project.get_run(run["id"])["cleanup_pending"]


def test_exit_247_is_not_invented_oom():
    result = ex.Execution("FAILED", 2, 1, ["openroad"])
    findings = diagnose(result, "make: *** [do-5_1_grt] Error 247", "physical.route")
    assert [d["details"]["code"] for d in findings] == ["ORFS_EXIT_247"]
    result.container_state = {"OOMKilled": True, "ExitCode": 137}
    assert "CONTAINER_OOM" in [d["details"]["code"] for d in diagnose(result, "", "physical.route")]


def test_failure_evidence_is_visible_in_operational_context(project, monkeypatch):
    def fail(self, *args):
        args[2].write_text("g++: internal compiler error: Segmentation fault\n")
        return ex.Execution("FAILED", 4, 0.1, ["compiler"])
    monkeypatch.setattr(ex.Runtime, "execute", fail)
    run = project.submit("lint", background=False)
    step = run["steps"][0]
    assert step["verification"]["status"] == "UNKNOWN"
    assert len(step["artifacts"]) == 2
    report = next(project.store.get("artifact", a) for a in step["artifacts"] if project.store.get("artifact", a)["type"] == "report.execution")
    assert json.loads(project.read_artifact(report["id"])["text"])[0]["returncode"] == 4
    diagnostic = project.get_operational_context()["execution_diagnostics"][0]
    assert diagnostic["details"]["code"] == "COMPILER_CRASH"
    assert set(diagnostic["evidence"]) == set(step["artifacts"])
    assert project.state() is None
