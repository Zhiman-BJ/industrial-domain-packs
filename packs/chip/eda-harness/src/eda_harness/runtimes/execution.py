import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from eda_harness.core.models import RuntimeConfig
from eda_harness.runtimes import resources


@dataclass
class Execution:
    status: str
    returncode: int | None
    elapsed_seconds: float
    argv: list[str]
    container_state: dict = field(default_factory=dict)
    cleanup: dict = field(default_factory=dict)
    resources: dict = field(default_factory=dict)
    tool_identity: dict = field(default_factory=dict)


def inspect_container(name):
    """Only a successful daemon response establishes container state/absence."""
    try:
        result = subprocess.run(
            ["docker", "container", "inspect", name, "--format", "{{json .State}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        if "No such container" in result.stderr or "No such object" in result.stderr:
            return {"absent": True}
        return {"inspection_error": (result.stderr or result.stdout)[-1000:]}
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {"inspection_error": str(error)[-1000:]}


def cleanup_container(name):
    """Bounded, idempotent cleanup. Never mistake a daemon timeout for deletion."""
    errors = []
    for attempt in range(1, 3):
        try:
            result = subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return {"confirmed": True, "attempts": attempt, "errors": errors}
            errors.append((result.stderr or result.stdout)[-1000:])
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(str(error)[-1000:])
        if inspect_container(name).get("absent"):
            return {"confirmed": True, "attempts": attempt, "errors": errors}
    return {"confirmed": False, "attempts": 2, "errors": errors}


def run_probe(argv, **kwargs):
    """Read-only probes also own and clean their container after client failure."""
    if argv[:2] != ["docker", "run"]:
        return subprocess.run(argv, **kwargs)
    name = "eda-probe-" + uuid.uuid4().hex
    try:
        return subprocess.run([*argv[:2], "--name", name, *argv[2:]], **kwargs)
    finally:
        cleanup = cleanup_container(name)
        if not cleanup["confirmed"]:
            raise ValueError(f"Docker probe cleanup pending: {name}; {cleanup['errors']}")


def stop_process(proc):
    # The session can have surviving descendants even after its leader exited.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS can report EPERM for a just-exited group. Reap before interpreting it.
        if proc.poll() is None:
            proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if proc.poll() is None:
            proc.kill()
    proc.wait(timeout=3)


def identity(config: RuntimeConfig, tool: str):
    if config.kind == "docker":
        proc = subprocess.run(
            ["docker", "image", "inspect", config.image, "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode:
            raise ValueError(f"Docker image unavailable: {config.image}. Pull/build it before submitting.")
        image = json.loads(proc.stdout)
        daemon = subprocess.run(
            ["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True, timeout=15,
        )
        if daemon.returncode:
            raise ValueError("Docker daemon unavailable; cannot verify runtime capacity")
        info = json.loads(daemon.stdout)
        arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(info["Architecture"], info["Architecture"])
        emulated = arch != image["Architecture"]
        if config.require_native and emulated:
            raise ValueError("Docker image requires CPU emulation; use a native daemon/image for physical design and formal acceptance")
        return {
            "kind": "docker", "image_id": image["Id"],
            "platform": f"{image['Os']}/{image['Architecture']}",
            "daemon_id": info["ID"], "daemon_arch": arch,
            "daemon_cpu": info["NCPU"], "daemon_memory_gb": info["MemTotal"] / 1024 ** 3,
            "emulated": emulated,
        }
    binary = shutil.which(tool)
    if not binary:
        raise ValueError(f"Tool not installed: {tool}")
    flags = {"yosys": "-V", "klayout": "-v", "openroad": "-version", "netgen": "-batch"}
    command = [binary, flags.get(tool, "--version")]
    if tool == "gtkwave" and not os.environ.get("DISPLAY"):
        command = ["xvfb-run", "-a", *command]
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "GSETTINGS_BACKEND": "memory"},
    )
    if proc.returncode:
        raise ValueError(f"Cannot determine {tool} version: {proc.stderr[:500]}")
    return {
        "kind": "local",
        "binary": binary,
        "sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        "version": (proc.stdout + proc.stderr).strip()[:2000],
    }


class Runtime:
    def __init__(self, config, tool_identity, token, source, deps, work):
        self.config = config
        self.identity = tool_identity
        self.token = token
        self.source, self.deps, self.work = source, deps, work
        docker = config.kind == "docker"
        self.paths = {
            "input": "/inputs" if docker else str(source),
            "deps": "/deps" if docker else str(deps),
            "work": "/work" if docker else str(work),
        }

    def execute(self, argv, env, log, cancelled, started):
        resource = self.config.resources
        argv = list(argv)
        if Path(argv[0]).name == "openroad":
            index = 1
            while index < len(argv):
                if argv[index] == "-threads" and index + 1 < len(argv):
                    del argv[index:index + 2]
                else:
                    index += 1
            argv[1:1] = ["-threads", str(resource.cpu)]
        actual = list(argv)
        docker = self.config.kind == "docker"
        if docker:
            actual = [
                "docker",
                "run",
                "--init",
                "--name",
                self.token,
                "--network",
                "none",
                "--cpus",
                str(resource.cpu),
                "--memory",
                f"{resource.memory_gb}g",
                "--pids-limit",
                str(resource.pids),
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=bind,src={self.source},dst=/inputs,readonly",
                "--mount",
                f"type=bind,src={self.deps},dst=/deps,readonly",
                "--mount",
                f"type=bind,src={self.work},dst=/work",
                "--workdir",
                "/work",
            ]
            # Native Linux bind mounts retain host ownership after capabilities
            # are dropped. Run as the project owner, including private workdirs.
            if sys.platform.startswith("linux"):
                actual.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
            actual.extend(["--entrypoint", argv[0], self.identity["image_id"], *argv[1:]])
        start = time.monotonic()
        container_state, cleanup = {}, {}
        # A CPU quota does not cap make/OpenMP/compiler worker counts.
        env = {
            **env,
            "EDA_CPU_LIMIT": str(resource.cpu),
            "EDA_MEMORY_GB": str(resource.memory_gb),
            "EDA_BUILD_JOBS": str(resource.build_jobs),
            "OMP_NUM_THREADS": str(resource.cpu),
            "NUM_CORES": str(resource.cpu),
            "OPENBLAS_NUM_THREADS": "1",
            "MAKEFLAGS": f"-j{resource.build_jobs}",
        }
        if docker:
            # Rebuild env arguments after deriving the execution resource policy.
            entrypoint = actual.index("--entrypoint")
            actual = actual[:entrypoint]
            for key, value in env.items():
                actual.extend(["--env", f"{key}={value}"])
            actual.extend(["--entrypoint", argv[0], self.identity["image_id"], *argv[1:]])
        with log.open("ab") as output:
            try:
                lease = resources.acquire(self.token, resource, self.identity)
            except ValueError as error:
                output.write((str(error) + "\n").encode())
                return Execution("FAILED", None, time.monotonic() - start, actual,
                                 resources=resource.model_dump(), tool_identity=self.identity)
            output.write(("argv: " + repr(actual) + "\n").encode())
            output.flush()
            try:
                proc = subprocess.Popen(
                    actual, cwd=self.work, env={**os.environ, **env}, stdout=output,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
            except BaseException:
                if lease:
                    resources.release(self.token)
                raise
            status = "RUNNING"
            try:
                started(proc.pid, self.token if docker else None)
                while proc.poll() is None:
                    if cancelled():
                        status = "CANCELLED"
                        break
                    if time.monotonic() - start >= resource.timeout_seconds:
                        status = "TIMEOUT"
                        break
                    time.sleep(0.1)
            finally:
                try:
                    if docker:
                        container_state = inspect_container(self.token)
                        cleanup = cleanup_container(self.token)
                finally:
                    try:
                        stop_process(proc)
                    finally:
                        if lease and (not docker or cleanup.get("confirmed")):
                            resources.release(self.token)
                if docker:
                    output.write(("container_state: " + json.dumps(container_state) + "\n").encode())
                    output.write(("cleanup: " + json.dumps(cleanup) + "\n").encode())
        if status == "RUNNING":
            status = "SUCCESS" if proc.returncode == 0 else "FAILED"
        if docker and (
            not cleanup.get("confirmed")
            or container_state.get("Running")
            or container_state.get("OOMKilled")
            or container_state.get("ExitCode", 0) != 0
        ):
            if status == "SUCCESS":
                status = "FAILED"
        return Execution(
            status, proc.returncode, time.monotonic() - start, actual,
            container_state, cleanup, resource.model_dump(), self.identity,
        )
