"""Narrow pack worker: one shared HTTP wrapper for remote sandbox profiles.

The long-running entrypoint that adapts a pack's trusted one-shot batch
executor to the gateway's worker protocol: legacy blocking invoke and
recoverable execution submission/query, with one fixed JSON body carrying
the server-created invocation context. The wrapper never accepts commands,
paths, images, mounts, environments, or working directories from the
request; it verifies the materialized input snapshot against the
referenced manifest digest, runs the pack profile's executor once per
request in a fresh work directory, and maps the executor's evidence files
into the WorkerClient observation schema (logs, candidateState,
verificationInputs, artifacts) using the profile's pinned artifact
mapping and byte caps.

Everything profile-specific comes from the pack's pack.json entry named
by WORKER_PROFILE_ID: the executor script, the tool id allowlist, the
argument policy ("none" rejects any arguments; "object" passes the
bounded invocation arguments to the executor via work-dir request.json),
and the report field that marks a successful run. The snapshot bytes
reach the sandbox through the server-owned staging path (the fixed
read-only input directory); this wrapper only re-derives their canonical
manifest digest and refuses to run against anything that does not match
the referenced snapshot.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENDPOINT = "/v1/worker/invoke"
EXECUTIONS_ENDPOINT = "/v1/worker/executions"
PROTOCOL_VERSION = 1
MAX_BODY_BYTES = 1024 * 1024
MAX_LOG_LINES = 200

# Mirrors the canonical snapshot manifest contract (schemaVersion 0.1):
# 1..256 files, 8 MiB per file and in total.
MAX_SNAPSHOT_FILES = 256
MAX_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_TOTAL_BYTES = 8 * 1024 * 1024

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

REQUEST_KEYS = frozenset(
    {
        "protocolVersion",
        "requestId",
        "runtimeDigest",
        "snapshotRef",
        "expectedStateId",
        "toolScope",
        "toolId",
        "arguments",
    }
)

# Root of the pack tree (lib/ sits beside packs/ in the shipped layout).
ROOT = Path(__file__).resolve().parents[1]


def config():
    """Runtime configuration. Everything is image/server-owned state: a
    request has no channel into any of it."""
    pack_name = os.environ.get("WORKER_PACK")
    profile_id = os.environ.get("WORKER_PROFILE_ID")
    if not pack_name or not profile_id:
        print(
            "pack worker: WORKER_PACK and WORKER_PROFILE_ID are not configured",
            file=sys.stderr,
        )
        sys.exit(2)
    pack = json.loads((ROOT / "packs" / pack_name / "pack.json").read_text())
    profile = next(
        (p for p in pack["profiles"] if p["id"] == profile_id), None
    )
    worker = profile.get("worker") if profile else None
    if not worker or worker.get("arguments") not in ("none", "object"):
        print(
            "pack worker: profile %s has no valid worker block" % profile_id,
            file=sys.stderr,
        )
        sys.exit(2)
    digest = os.environ.get("WORKER_RUNTIME_DIGEST")
    if not digest:
        # Fail closed: an unpinned runtime must refuse every invocation, so
        # a misconfigured boot is loud instead of quietly permissive.
        print("pack worker: WORKER_RUNTIME_DIGEST is not configured", file=sys.stderr)
        sys.exit(2)
    executor = os.environ.get(
        "WORKER_EXECUTOR", str(ROOT / worker["executor"])
    )
    return {
        "bind": os.environ.get("WORKER_BIND", "0.0.0.0"),
        "port": int(os.environ.get("WORKER_PORT", "8080")),
        "input_dir": Path(os.environ.get("WORKER_INPUT_DIR", "/input")).resolve(),
        "work_root": Path(os.environ.get("WORKER_WORK_ROOT", "/work/requests")),
        "executor": executor,
        "access_token": os.environ.get("WORKER_ACCESS_TOKEN"),
        "runtime_digest": digest,
        "timeout_seconds": float(os.environ.get("WORKER_TIMEOUT_SECONDS", "900")),
        "client_timeout_seconds": float(
            os.environ.get("WORKER_CLIENT_TIMEOUT_SECONDS", "30")
        ),
        "max_work_dirs": int(os.environ.get("WORKER_MAX_WORK_DIRS", "8")),
        "arguments": worker["arguments"],
        "success_field": worker["successField"],
        "tool_ids": list(worker["toolIds"]),
        # [executor file, artifact kind, byte cap] from the pinned profile.
        "artifacts": [(f, k, c) for f, k, c in profile["artifacts"]],
    }


CONFIG = config()
# One execution at a time: a sandbox runs a single leased run; a concurrent
# second invocation gets a clean 409 rejection.
IN_FLIGHT = threading.Lock()
ACCEPT_LOCK = threading.Lock()


def canonical_manifest_bytes(files):
    return json.dumps(
        {"schemaVersion": "0.1", "files": files},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def verify_snapshot(input_dir, manifest_sha256):
    """Re-derives the canonical manifest digest over the materialized input
    tree. Symlinks, extra, missing, or modified content all fail: the tree
    must be exactly the referenced snapshot."""
    files = []
    total = 0
    for current, dirnames, filenames in os.walk(input_dir):
        for name in list(dirnames):
            if os.path.islink(os.path.join(current, name)):
                return False
        for name in filenames:
            full = os.path.join(current, name)
            if os.path.islink(full) or not os.path.isfile(full):
                return False
            size = os.path.getsize(full)
            if size > MAX_SNAPSHOT_FILE_BYTES:
                return False
            total += size
            if total > MAX_SNAPSHOT_TOTAL_BYTES:
                return False
            files.append(
                {
                    "path": os.path.relpath(full, input_dir).replace(os.sep, "/"),
                    "sha256": hashlib.sha256(Path(full).read_bytes()).hexdigest(),
                    "sizeBytes": size,
                }
            )
    if not 1 <= len(files) <= MAX_SNAPSHOT_FILES:
        return False
    # UTF-16 code-unit ordering matches the gateway's manifest sort exactly.
    files.sort(key=lambda entry: entry["path"].encode("utf-16-be"))
    try:
        digest = hashlib.sha256(canonical_manifest_bytes(files)).hexdigest()
    except UnicodeEncodeError:
        # A filename containing a lone surrogate cannot be represented by the
        # UTF-8 canonical manifest contract. Treat it as an untrusted tree so
        # the caller receives the normal 422 SNAPSHOT_MISMATCH response.
        return False
    return digest == manifest_sha256


def tail_logs(work_dir, extra_line=None):
    lines = []
    log_file = work_dir / "tool.log"
    if log_file.is_file():
        text = log_file.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()[-MAX_LOG_LINES:]
    if extra_line:
        lines.append(extra_line)
    return lines


def build_observation(work_dir, exit_code, timed_out, timeout_seconds):
    report = None
    report_file = work_dir / "report.json"
    if report_file.is_file():
        try:
            parsed = json.loads(report_file.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                report = parsed
        except ValueError:
            report = None
    if timed_out:
        diagnostic = "execution timed out after %gs and was terminated" % timeout_seconds
    elif report is None:
        diagnostic = "executor produced no readable report.json"
    else:
        diagnostic = None
    succeeded = bool(report and report.get(CONFIG["success_field"]) is True)
    ok = exit_code == 0 and not timed_out and report is not None and succeeded
    if report is not None:
        verification_inputs = dict(report)
        if timed_out:
            verification_inputs["diagnostic"] = diagnostic
    else:
        verification_inputs = {CONFIG["success_field"]: False}
        if diagnostic:
            verification_inputs["diagnostic"] = diagnostic
    logs = tail_logs(
        work_dir,
        "worker: " + diagnostic if diagnostic and (timed_out or report is None) else None,
    )
    artifacts = []
    for filename, kind, cap in CONFIG["artifacts"]:
        candidate = work_dir / filename
        if not candidate.is_file() or os.path.islink(candidate):
            continue
        body = candidate.read_bytes()
        if len(body) > cap:
            logs.append("worker: %s exceeds %d bytes and was not attached" % (filename, cap))
            continue
        artifacts.append(
            {
                "kind": kind,
                "bytes": base64.b64encode(body).decode("ascii"),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    return {
        "ok": ok,
        "logs": logs,
        "candidateState": None,
        "verificationInputs": verification_inputs,
        "artifacts": artifacts,
    }


def prune_work_dirs(work_root):
    if not work_root.is_dir():
        return
    entries = [e for e in work_root.iterdir() if e.is_dir() and not (e / "execution.json").exists()]
    if len(entries) <= CONFIG["max_work_dirs"]:
        return
    entries.sort(key=lambda entry: entry.stat().st_mtime)
    for stale in entries[: len(entries) - CONFIG["max_work_dirs"]]:
        shutil.rmtree(stale, ignore_errors=True)


def validate_request(body):
    """Strict mirror of the gateway's WORKER_REQUEST_SCHEMA. Returns an
    error message string, or None when the context is well-formed."""
    if not isinstance(body, dict) or frozenset(body) != REQUEST_KEYS:
        return "request must contain exactly the invocation context fields"
    version = body["protocolVersion"]
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        return "protocolVersion must be %d" % PROTOCOL_VERSION
    if not isinstance(body["requestId"], str) or not UUID_RE.match(body["requestId"]):
        return "requestId must be a uuid"
    if not isinstance(body["runtimeDigest"], str) or not body["runtimeDigest"]:
        return "runtimeDigest must be a non-empty string"
    snapshot = body["snapshotRef"]
    if not isinstance(snapshot, dict) or frozenset(snapshot) != frozenset(
        {"snapshotId", "manifestSha256"}
    ):
        return "snapshotRef must contain exactly snapshotId and manifestSha256"
    if not isinstance(snapshot["snapshotId"], str) or not UUID_RE.match(
        snapshot["snapshotId"]
    ):
        return "snapshotRef.snapshotId must be a uuid"
    if not isinstance(snapshot["manifestSha256"], str) or not SHA256_RE.match(
        snapshot["manifestSha256"]
    ):
        return "snapshotRef.manifestSha256 must be a sha256 hex digest"
    expected = body["expectedStateId"]
    if expected is not None and (
        not isinstance(expected, str) or not UUID_RE.match(expected)
    ):
        return "expectedStateId must be a uuid or null"
    scope = body["toolScope"]
    if not isinstance(scope, list) or not scope:
        return "toolScope must be a non-empty array"
    for item in scope:
        if not isinstance(item, str) or not item:
            return "toolScope entries must be non-empty strings"
    if not isinstance(body["toolId"], str) or not body["toolId"]:
        return "toolId must be a non-empty string"
    if not isinstance(body["arguments"], dict):
        return "arguments must be an object"
    return None


def run_execution(body, work_dir):
    started = time.monotonic()
    timed_out = False
    process = subprocess.Popen(
        [
            sys.executable,
            CONFIG["executor"],
            str(CONFIG["input_dir"]),
            str(work_dir),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": "/tmp",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CCACHE_DISABLE": "1",
        },
        # The executor trees out (python -> verilator -> g++/make); own
        # the whole tree as a session so a deadline kill reaches the
        # grandchildren too. Orphaned toolchain processes would otherwise
        # outlive the lease and burn a pooled instance's CPU.
        start_new_session=True,
    )
    try:
        exit_code = process.wait(timeout=CONFIG["timeout_seconds"])
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        exit_code = process.wait()
    observation = build_observation(
        work_dir, exit_code, timed_out, CONFIG["timeout_seconds"]
    )
    print(
        "pack worker: request %s finished in %.1fs (ok=%s)"
        % (body["requestId"], time.monotonic() - started, observation["ok"]),
        flush=True,
    )
    return observation


def write_execution(path, state):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as output:
        json.dump(state, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def execute_detached(body, work_dir, state):
    try:
        observation = run_execution(body, work_dir)
        state = dict(state, status="completed", observation=observation)
    except Exception:
        # Never relaunch after an uncertain executor failure.
        state = dict(state, status="unknown")
    finally:
        try:
            write_execution(work_dir / "execution.json", state)
        finally:
            IN_FLIGHT.release()


class WorkerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pack-worker/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(CONFIG["client_timeout_seconds"])

    def log_message(self, fmt, *args):
        print("pack worker: %s %s" % (self.address_string(), fmt % args), flush=True)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def reject_unknown_path(self):
        if self.path.split("?", 1)[0] not in (ENDPOINT, EXECUTIONS_ENDPOINT):
            self.send_json(404, {"code": "NOT_FOUND"})
            return True
        return False

    def do_GET(self):
        if self.path.startswith(EXECUTIONS_ENDPOINT + "/"):
            if not self.authorize():
                return
            request_id = self.path[len(EXECUTIONS_ENDPOINT) + 1:]
            if not UUID_RE.fullmatch(request_id):
                self.send_json(400, {"code": "WORKER_REQUEST_INVALID"})
                return
            path = CONFIG["work_root"] / request_id / "execution.json"
            if not path.is_file():
                self.send_json(404, {"code": "EXECUTION_NOT_FOUND"})
                return
            self.send_json(200, json.loads(path.read_text()))
            return
        if self.reject_unknown_path():
            return
        self.send_json(405, {"code": "METHOD_NOT_ALLOWED"})

    def reject_method(self):
        self.close_connection = True
        self.send_json(405, {"code": "METHOD_NOT_ALLOWED"})

    do_PUT = do_DELETE = do_PATCH = reject_method

    def do_POST(self):
        if self.reject_unknown_path():
            return
        try:
            self.handle_invoke()
        except Exception as error:  # pragma: no cover - last-resort honesty
            self.send_json(
                500,
                {"code": "WORKER_INTERNAL_ERROR", "message": str(error)[:256]},
            )

    def authorize(self):
        parts = self.headers.get("authorization", "").split(" ", 1)
        token = parts[1].strip() if parts[:1] == ["Bearer"] and len(parts) == 2 else ""
        if not token or (CONFIG["access_token"] and token != CONFIG["access_token"]):
            self.send_json(401, {"code": "UNAUTHORIZED"})
            return False
        return True

    def read_body(self):
        # The bounded body is consumed BEFORE any rejection is written, so a
        # keep-alive connection never desyncs on an unread request body. A
        # body over the bound is not read at all; that connection closes.
        length = self.headers.get("content-length")
        if length is None or not length.isdigit():
            self.close_connection = True
            self.send_json(400, {"code": "WORKER_REQUEST_INVALID", "message": "content-length is required"})
            return None
        size = int(length)
        if size > MAX_BODY_BYTES:
            self.close_connection = True
            self.send_json(413, {"code": "WORKER_REQUEST_INVALID", "message": "request body exceeds the size bound"})
            return None
        try:
            return self.rfile.read(size)
        except TimeoutError:
            self.close_connection = True
            self.send_json(
                408,
                {
                    "code": "WORKER_REQUEST_INVALID",
                    "message": "request body read timed out",
                },
            )
            return None

    def handle_invoke(self):
        raw = self.read_body()
        if raw is None:
            return
        if not self.authorize():
            return
        try:
            body = json.loads(raw)
        except ValueError:
            self.send_json(400, {"code": "WORKER_REQUEST_INVALID", "message": "request body is not valid JSON"})
            return
        invalid = validate_request(body)
        if invalid:
            self.send_json(
                400,
                {"code": "WORKER_REQUEST_INVALID", "message": invalid},
            )
            return
        if body["runtimeDigest"] != CONFIG["runtime_digest"]:
            self.send_json(
                400,
                {"code": "RUNTIME_DIGEST_MISMATCH", "message": "runtime digest is not the pinned image runtime"},
            )
            return
        if body["toolId"] not in CONFIG["tool_ids"] or body["toolId"] not in body["toolScope"]:
            self.send_json(
                400,
                {"code": "TOOL_REJECTED", "message": "tool is not in this profile's pinned tool catalog"},
            )
            return
        if CONFIG["arguments"] == "none" and body["arguments"]:
            self.send_json(
                400,
                {
                    "code": "TOOL_REJECTED",
                    "message": "this profile reads its declared inputs from the snapshot; arguments are not accepted",
                },
            )
            return
        if self.path == EXECUTIONS_ENDPOINT:
            self.accept_execution(body)
            return
        if not IN_FLIGHT.acquire(blocking=False):
            self.send_json(409, {"code": "INVOCATION_IN_FLIGHT"})
            return
        try:
            self.execute(body)
        finally:
            IN_FLIGHT.release()

    def accept_execution(self, body):
        # Serialize disk identity checks and admission; the executor holds
        # IN_FLIGHT independently of the connection that submitted it.
        with ACCEPT_LOCK:
            root = CONFIG["work_root"]
            work = root / body["requestId"]
            state_file = work / "execution.json"
            fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if state_file.is_file():
                state = json.loads(state_file.read_text())
                if state["inputDigest"] != fingerprint:
                    self.send_json(409, {"code": "REQUEST_ID_CONFLICT"})
                else:
                    self.send_json(200, state)
                return
            if work.exists():
                self.send_json(409, {"code": "DUPLICATE_REQUEST"})
                return
            root.mkdir(parents=True, exist_ok=True)
            if sum(1 for _ in root.glob("*/execution.json")) >= CONFIG["max_work_dirs"]:
                self.send_json(409, {"code": "RESULT_RETENTION_FULL"})
                return
            if not IN_FLIGHT.acquire(blocking=False):
                self.send_json(409, {"code": "INVOCATION_IN_FLIGHT"})
                return
            transferred = False
            try:
                if not verify_snapshot(CONFIG["input_dir"], body["snapshotRef"]["manifestSha256"]):
                    self.send_json(422, {"code": "SNAPSHOT_MISMATCH"})
                    return
                work.mkdir()
                (work / "request.json").write_text(json.dumps({"requestId": body["requestId"], "toolId": body["toolId"], "arguments": body["arguments"]}))
                state = {"requestId": body["requestId"], "runtimeDigest": body["runtimeDigest"], "snapshotRef": body["snapshotRef"], "inputDigest": fingerprint, "status": "working"}
                write_execution(state_file, state)
                thread = threading.Thread(target=execute_detached, args=(body, work, state), daemon=True)
                thread.start()
                transferred = True
                self.send_json(202, state)
            finally:
                if not transferred:
                    IN_FLIGHT.release()

    def execute(self, body):
        work_root = CONFIG["work_root"]
        work_root.mkdir(parents=True, exist_ok=True)
        work_dir = work_root / body["requestId"]
        if work_dir.exists():
            self.send_json(
                409,
                {"code": "DUPLICATE_REQUEST", "message": "requestId already ran in this sandbox"},
            )
            return
        if not verify_snapshot(CONFIG["input_dir"], body["snapshotRef"]["manifestSha256"]):
            self.send_json(
                422,
                {
                    "code": "SNAPSHOT_MISMATCH",
                    "message": "materialized input tree does not match the referenced snapshot manifest",
                },
            )
            return
        work_dir.mkdir(parents=True)
        # The bounded invocation arguments reach the trusted executor through
        # the work directory, never through the process environment.
        (work_dir / "request.json").write_text(
            json.dumps(
                {
                    "requestId": body["requestId"],
                    "toolId": body["toolId"],
                    "arguments": body["arguments"],
                }
            )
            + "\n"
        )
        observation = run_execution(body, work_dir)
        prune_work_dirs(work_root)
        self.send_json(200, observation)


def main():
    server = ThreadingHTTPServer((CONFIG["bind"], CONFIG["port"]), WorkerHandler)
    server.daemon_threads = True
    print(
        "pack worker listening on %s:%d (tools=%s input=%s executor=%s)"
        % (
            CONFIG["bind"],
            CONFIG["port"],
            ",".join(CONFIG["tool_ids"]),
            CONFIG["input_dir"],
            CONFIG["executor"],
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
