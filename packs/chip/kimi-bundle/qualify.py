"""Real packaged CLI/MCP/RTL/export qualification with a controlled model endpoint.

This checks software integration, not model design ability or foundry signoff.
"""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import zipfile


def execute(*args, env, cwd, timeout=180, expected_code=0):
    result = subprocess.run([str(arg) for arg in args], env=env, cwd=cwd,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode != expected_code:
        raise RuntimeError(f"Command failed ({result.returncode}): {args}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def text_content(message):
    value = message.get("content", "")
    if isinstance(value, str):
        return value
    return "\n".join(item.get("text", "") for item in value if isinstance(item, dict))


def qualify(bundle, image, output, existing_kimi=None):
    output.mkdir(parents=True)
    home = output / "native home"
    project = output / "RTL project"
    project.mkdir()
    bin_dir = output / "user bin"
    installed = output / "installed bundle"
    requests, errors = [], []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            try:
                messages = body["messages"]
                if any("CONTINUE_NATIVE_CHIP" in text_content(message) for message in messages if message["role"] == "user"):
                    call, answer = None, "CHIP_CONTINUED_OK"
                else:
                    names = [item["function"]["name"] for item in body.get("tools", [])]
                    assert "Skill" in names and len([name for name in names if name.startswith("mcp__chip__")]) == 25, names
                    assert "chip-design" in json.dumps(body), "Pack Skill was not discovered"
                    results = [message for message in messages if message["role"] == "tool"]
                    joined = "\n".join(text_content(message) for message in results)
                    index = len(results)
                    answer = "CHIP_TRAJECTORY_OK"
                    if index == 0:
                        call = ("Skill", {"skill": "chip-design"})
                    elif index == 1:
                        assert "current-input acceptance" in json.dumps(messages), "Native Skill body did not load"
                        call = ("mcp__chip__get_tool_guide", {})
                    elif index == 2:
                        call = ("mcp__chip__get_server_info", {})
                    elif index == 3:
                        call = ("mcp__chip__run_action", {"action": "rtl.simulate", "project_path": str(project)})
                    else:
                        match = re.search(r'"run_id"\s*:\s*"([^"\\]+)"', text_content(results[3]))
                        assert match, "Missing real native run ID: " + joined[-2000:]
                        last = text_content(results[-1])
                        status = re.search(r'"status"\s*:\s*"([^"\\]+)"', last)
                        assert status and status[1] not in {"FAILED", "TIMEOUT", "CANCELLED"}, last
                        if status[1] == "SUCCESS":
                            call = None
                        else:
                            time.sleep(5)
                            call = ("mcp__chip__get_run", {"run_id": match[1], "project_path": str(project)})
                identifier = f"qualification-{len(requests)}"
                calls = [{"id": identifier, "type": "function", "function": {"name": call[0], "arguments": json.dumps(call[1])}}] if call else []
                message = {"role": "assistant", "content": None if call else answer}
                if calls:
                    message["tool_calls"] = calls
                base = {"id": identifier, "created": 1, "model": body["model"]}
                usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    delta = {**message}
                    if calls:
                        delta["tool_calls"] = [{"index": 0, **calls[0]}]
                    chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                              {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if call else "stop"}], "usage": usage}]
                    for chunk in chunks:
                        self.wfile.write(("data: " + json.dumps({**base, "object": "chat.completion.chunk", **chunk}) + "\n\n").encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({**base, "object": "chat.completion", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if call else "stop"}], "usage": usage}).encode())
            except Exception as error:
                errors.append(str(error))
                (output / "protocol-errors.json").write_text(json.dumps(errors, indent=2))
                self.send_error(500, str(error)[:200])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "KIMI_CODE_HOME": str(home), "KIMI_CODE_NO_AUTO_UPDATE": "1"}
    try:
        install_command = ["bash", bundle / ("connect.sh" if existing_kimi else "install.sh"), "--prefix", installed,
                           "--image", image]
        install_command += ["--kimi-bin", existing_kimi] if existing_kimi else ["--bin-dir", bin_dir]
        install = execute(*install_command, env=env, cwd=output)
        (output / "install.log").write_text(install)
        config = f'''default_model = "controlled"
telemetry = false
auto_session_title = false
[loop_control]
max_attempts_per_step = 1
[providers.controlled]
type = "openai"
base_url = "http://127.0.0.1:{server.server_port}/v1"
api_key = "local-qualification-fixture"
[models.controlled]
provider = "controlled"
model = "controlled"
max_context_size = 262144
capabilities = ["tool_use"]
'''
        (home / "config.toml").write_text(config)
        # Reinstall must preserve native credentials and other MCP registrations.
        mcp = json.loads((home / "mcp.json").read_text())
        mcp["mcpServers"]["unrelated"] = {"command": "unused", "enabled": False}
        (home / "mcp.json").write_text(json.dumps(mcp))
        execute(*install_command, env=env, cwd=output)
        assert (home / "config.toml").read_text() == config
        assert "unrelated" in json.loads((home / "mcp.json").read_text())["mcpServers"]
        fixture = Path(__file__).resolve().parents[1] / "examples/rtl"
        for name in ("counter.sv", "counter_tb.sv", "eda.yaml"):
            shutil.copy2(fixture / name, project / name)
        with (project / "eda.yaml").open("a") as stream:
            stream.write("\ninputs: {rtl: [counter.sv, counter_tb.sv]}\nruntime:\n  kind: docker\n  require_native: true\n  image: " + json.dumps(image) + "\n  resources: {cpu: 1, memory_gb: 2, timeout_seconds: 120}\nrequired_verification: [rtl.simulate]\n")
            stream.write("workflow:\n  actions:\n    - id: rtl.simulate\n      tool: verilator\n      dependencies: []\n      input_types: [rtl]\n      artifact_types: [report.simulation]\n")
        cli = existing_kimi or bin_dir / "kimi-chip"
        eda = installed / "bin/eda-chip"
        turn = execute(cli, "-p", "COLLECT_CHIP_NATIVE: invoke chip-design and simulate this project.",
                       "--output-format", "stream-json", env=env, cwd=project)
        (output / "turn.jsonl").write_text(turn)
        assert "CHIP_TRAJECTORY_OK" in turn and not errors, errors
        tool_results = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
        run_id = re.search(r'"run_id"\s*:\s*"([^"\\]+)"', text_content(tool_results[3]))[1]
        successful = json.loads(execute(eda, "--project", project, "get-run", run_id, env=env, cwd=project))
        assert successful["status"] == "SUCCESS" and successful["steps"][-1]["verification"] == "PASS", successful
        sessions = json.loads(execute(cli, "session", "list", "--json", env=env, cwd=project))
        if isinstance(sessions, dict):
            sessions = sessions.get("sessions", sessions.get("items", []))
        assert len(sessions) == 1, sessions
        session_id = sessions[0].get("sessionId", sessions[0].get("id"))
        assert session_id, sessions
        continued = execute(cli, "--continue", "-p", "CONTINUE_NATIVE_CHIP: confirm the same session.",
                            "--output-format", "stream-json", env=env, cwd=project)
        (output / "continuation.jsonl").write_text(continued)
        assert "CHIP_CONTINUED_OK" in continued, continued[-2000:]
        exported = output / "trajectory.zip"
        execute(cli, "export", session_id, "-o", exported, "--no-include-global-log", env=env, cwd=project)
        wires = list((home / "sessions").rglob("wire.jsonl"))
        assert wires, "Native session event stream missing"
        wire = "\n".join(path.read_text() for path in wires)
        for token in ("COLLECT_CHIP_NATIVE", "CONTINUE_NATIVE_CHIP", "mcp__chip__run_action", "chip-design", "CHIP_TRAJECTORY_OK"):
            assert token in wire, "Native trajectory lost: " + token
        with zipfile.ZipFile(exported) as archive:
            assert any(name.endswith("wire.jsonl") for name in archive.namelist())
        # A real failing assertion must not become successful engineering evidence.
        testbench = project / "counter_tb.sv"
        testbench.write_text(testbench.read_text().replace("assert(count == 4)", "assert(count == 3)"))
        failed = json.loads(execute(eda, "--project", project, "run", "rtl.simulate", "--wait", env=env, cwd=project, expected_code=1))
        assert failed["status"] == "FAILED", failed
        failing_logs = []
        for artifact_id in failed["steps"][-1]["artifacts"]:
            artifact = json.loads(execute(eda, "--project", project, "read-artifact", artifact_id, env=env, cwd=project))
            if artifact["artifact"]["type"] == "log.tool":
                failing_logs.append(artifact.get("text", ""))
        assert "Assertion failed" in "\n".join(failing_logs) and "counter failed" in "\n".join(failing_logs)
        # Cancel real native work, then recover from a new CLI process and rerun.
        pending = json.loads(execute(eda, "--project", project, "run", "rtl.simulate", "--force", env=env, cwd=project))
        for _ in range(40):
            running = json.loads(execute(eda, "--project", project, "get-run", pending["run_id"], env=env, cwd=project))
            if running.get("container_name"):
                break
            time.sleep(0.25)
        assert running.get("container_name"), running
        execute(eda, "--project", project, "cancel", pending["run_id"], env=env, cwd=project)
        cancelled = json.loads(execute(eda, "--project", project, "wait", pending["run_id"], env=env, cwd=project, expected_code=1))
        assert cancelled["status"] == "CANCELLED" and not cancelled.get("cleanup_pending"), cancelled
        execute(eda, "--project", project, "recover", env=env, cwd=project)
        shutil.copy2(fixture / "counter_tb.sv", testbench)
        recovered = json.loads(execute(eda, "--project", project, "run", "rtl.simulate", "--force", "--wait", env=env, cwd=project))
        assert recovered["status"] == "SUCCESS" and recovered["steps"][-1]["verification"] == "PASS", recovered
        assert errors == [], errors
        manifest = json.loads((installed / "bundle.json").read_text())
        actual_binary = hashlib.sha256((existing_kimi or installed / "upstream/kimi").read_bytes()).hexdigest()
        assert actual_binary == manifest["upstream"]["kimi"]["binarySha256"]
        result = {"status": "PASS", "mode": "existing-native-kimi" if existing_kimi else "standalone-bundle",
                  "sourceCommit": manifest["sourceCommit"], "pristineUpstreamBinary": actual_binary,
                  "nativeCli": "2.1.1", "stdioToolCount": 25, "skillLoaded": "chip-design",
                  "realRtlSuccess": True, "realAssertionFailure": True, "nativeCancellationAndRecovery": True, "nativeContinuation": True,
                  "nativeWireAndZip": True, "reinstallPreservesConfig": True,
                  "model": "controlled local protocol fixture; no model inference qualification",
                  "imageId": json.loads((home / "skills/chip-design/runtime.json").read_text())["imageId"]}
        (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    finally:
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--existing-kimi", type=Path, help="Qualify connection to an already installed pristine native CLI")
    args = parser.parse_args()
    qualify(args.bundle.resolve(), args.image, args.output.resolve(), args.existing_kimi.resolve() if args.existing_kimi else None)
