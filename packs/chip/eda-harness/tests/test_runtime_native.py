"""Real container lifecycle tests; opt in with EDA_NATIVE_TEST_IMAGE."""
import os
import signal
import threading
import time
from uuid import uuid4

import pytest

from eda_harness.core.models import RuntimeConfig
from eda_harness.runtimes.execution import Runtime, identity, inspect_container


@pytest.fixture
def native_runtime(tmp_path, monkeypatch):
    image = os.environ.get("EDA_NATIVE_TEST_IMAGE")
    if not image:
        pytest.skip("Set EDA_NATIVE_TEST_IMAGE to an installed shell-capable image")
    monkeypatch.setenv("EDA_RESOURCE_STATE_DIR", str(tmp_path / "leases"))
    config = RuntimeConfig(kind="docker", image=image,
                           resources={"cpu": 1, "memory_gb": 1, "timeout_seconds": 30})
    info = identity(config, "sh")
    return Runtime(config, info, "eda-native-" + uuid4().hex[:12], tmp_path, tmp_path, tmp_path)


def test_real_docker_success_is_cleaned(native_runtime):
    rt = native_runtime
    result = rt.execute(["sh", "-c", "printf artifact > result.txt"], {}, rt.work / "log",
                        lambda: False, lambda *a: None)
    assert result.status == "SUCCESS"
    assert (rt.work / "result.txt").read_text() == "artifact"
    assert result.container_state["ExitCode"] == 0
    assert result.cleanup["confirmed"] and inspect_container(rt.token).get("absent")


def test_real_docker_timeout_kills_container_and_releases_budget(native_runtime):
    rt = native_runtime
    rt.config.resources.timeout_seconds = 1
    result = rt.execute(["sh", "-c", "sleep 60"], {}, rt.work / "log",
                        lambda: False, lambda *a: None)
    assert result.status == "TIMEOUT"
    assert result.cleanup["confirmed"] and inspect_container(rt.token).get("absent")
    rt.config.resources.timeout_seconds = 30
    assert rt.execute(["sh", "-c", "true"], {}, rt.work / "log", lambda: False, lambda *a: None).status == "SUCCESS"


def test_real_docker_client_crash_cannot_orphan_tool(native_runtime):
    rt = native_runtime
    marker = rt.work / "started"
    errors = []
    thread = None

    def started(pid, name):
        def crash():
            try:
                deadline = time.monotonic() + 15
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert marker.exists(), "container failed to start"
                os.kill(pid, signal.SIGKILL)
            except Exception as error:
                errors.append(error)
        nonlocal thread
        thread = threading.Thread(target=crash)
        thread.start()

    result = rt.execute(["sh", "-c", "echo ready > started; sleep 60"], {}, rt.work / "log",
                        lambda: False, started)
    thread.join(20)
    assert not errors
    assert result.status == "FAILED"
    assert result.container_state["Running"]
    assert result.cleanup["confirmed"] and inspect_container(rt.token).get("absent")


def test_real_docker_oom_evidence_is_preserved_before_removal(native_runtime):
    rt = native_runtime
    rt.config.resources.memory_gb = 0.0625
    # Stay below mawk's sprintf buffer limit so both mawk and BusyBox reach real OOM.
    result = rt.execute(["sh", "-c", "awk 'BEGIN { for (i=0;;i++) a[i]=sprintf(\"%8000s\", i) }'"],
                        {}, rt.work / "log", lambda: False, lambda *a: None)
    assert result.status == "FAILED"
    assert result.container_state["OOMKilled"]
    assert result.cleanup["confirmed"] and inspect_container(rt.token).get("absent")
