import importlib.util
from pathlib import Path

import pytest

from eda_harness.core.models import Resources
from eda_harness.runtimes import resources


@pytest.fixture
def budget(tmp_path, monkeypatch):
    monkeypatch.setenv("EDA_RESOURCE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("EDA_MAX_CONCURRENT_ACTIONS", raising=False)
    return {"kind": "docker", "daemon_id": "test-daemon", "daemon_cpu": 8, "daemon_memory_gb": 8}


def test_ram_checks_actual_daemon_capacity(budget):
    with pytest.raises(ValueError, match="Docker VM RAM"):
        resources.acquire("eda-big-0", Resources(memory_gb=16), budget)
    assert resources.acquire("eda-small-0", Resources(), budget)
    resources.release("eda-small-0")


def test_projects_share_capacity_until_cleanup_confirmed(budget):
    assert resources.acquire("eda-project-a-0", Resources(), budget)
    with pytest.raises(ValueError, match="EDA_RESOURCE_BUSY"):
        resources.acquire("eda-project-b-0", Resources(), budget)
    resources.release("eda-project-a-0")
    assert resources.acquire("eda-project-b-0", Resources(), budget)
    resources.release("eda-project-b-0")


def test_opt_in_parallelism_still_accounts_for_ram_and_cpus(budget, monkeypatch):
    monkeypatch.setenv("EDA_MAX_CONCURRENT_ACTIONS", "2")
    assert resources.acquire("eda-a-0", Resources(cpu=4, memory_gb=4), budget)
    with pytest.raises(ValueError, match="EDA_RESOURCE_MEMORY"):
        resources.acquire("eda-b-0", Resources(cpu=4, memory_gb=4), budget)
    with pytest.raises(ValueError, match="EDA_RESOURCE_CPU"):
        resources.acquire("eda-b-0", Resources(cpu=5, memory_gb=1), budget)
    resources.release("eda-a-0")


def test_compiler_workers_are_bounded_by_ram_and_cpu():
    with pytest.raises(ValueError, match="CPU allocation"):
        Resources(cpu=1, build_jobs=2)
    with pytest.raises(ValueError, match="2 GiB"):
        Resources(cpu=4, memory_gb=4, build_jobs=4)
    assert Resources(cpu=2, memory_gb=4, build_jobs=2).build_jobs == 2


@pytest.mark.parametrize("tool,args", [
    ("make", ["-j", "64", "all"]), ("make", ["--jobs=0", "all"]),
    ("make", ["-j", "all"]), ("make", ["MAKEFLAGS=-j64", "all"]),
    ("verilator", ["--binary", "-j64", "top.sv"]),
    ("verilator", ["--build", "--build-jobs", "0", "top.sv"]),
])
def test_image_wrappers_limit_project_script_parallelism(tool, args):
    path = Path(__file__).resolve().parents[1] / "tools/resource-wrapper.py"
    spec = importlib.util.spec_from_file_location("resource_wrapper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bounded = module.bounded_arguments(tool, args, 1)
    assert bounded[-2:] == ["-j", "1"]
    assert "64" not in bounded and "-j64" not in bounded and "--jobs=0" not in bounded


def test_orfs_thread_override_cannot_exceed_managed_budget(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "tools/resource-wrapper.py"
    spec = importlib.util.spec_from_file_location("resource_wrapper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("EDA_CPU_LIMIT", "2")
    args = module.bounded_arguments("make", ["-j64", "NUM_CORES=64", "route"], 1)
    assert "NUM_CORES=2" in args and "NUM_CORES=64" not in args
