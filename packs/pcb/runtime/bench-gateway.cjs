"use strict";
// Scoped MCP stdio client for the maintained PCB-bench container gateway.
//
// The gateway process (bridge/pcb-gateway.py) owns the pinned Linux
// container: it starts one docker run per gateway lifetime, keeps the
// upstream controller alive across calls and enforces the transport policy
// (argument caps, call timeout, schema pinning). This module only speaks
// JSON-RPC to that process and never invokes docker itself.
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pcbGatewayConfig } = require("../bridge/pcb-runtime.cjs");

const GATEWAY_MCP_VERSION = "1.29.1"; // packs/pcb/pyproject.toml lock
const STARTUP_TIMEOUT_MS = 150000; // cold amd64 image on an arm64 host
const CALL_TIMEOUT_MS = 60000; // gateway policy is 45 s; let its error land first
const IDLE_RECYCLE_MS = 15 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 15000;

// The bridge validates an external checkout against the pinned upstream
// inventory, so it consumes a snapshot-shaped provider view rather than the
// manifest provider (whose sourceFiles cover packs/pcb/runtime only). The
// gateway also compares the live container tool set with policy.tools by
// name, so the tool list must be exactly the 89 bench descriptors.
function bridgeProvider(provider, snapshot) {
  const files = snapshot.sourceFiles;
  const compact = JSON.stringify(
    Object.fromEntries(Object.keys(files).sort().map((key) => [key, files[key]])),
  );
  return {
    ...provider,
    tools: snapshot.tools,
    sourceCommit: snapshot.sourceCommit,
    toolSchemaSha256: snapshot.toolSchemaSha256,
    imageId: snapshot.imageId,
    sourceSha256: crypto.createHash("sha256").update(compact).digest("hex"),
    resourceRoots: ["pcb-agent", "skills/pcb-design-e2e"],
    sourceFiles: files,
    directoryEnv: "INDUSTRIAL_HARNESS_PCB_BENCH_DIR",
    pythonEnv: "INDUSTRIAL_HARNESS_PCB_GATEWAY_PYTHON",
    mcpVersion: GATEWAY_MCP_VERSION,
    allowedToolIds: snapshot.tools.map((tool) => tool.id),
  };
}

function sessionDirectory(project) {
  const identity = crypto
    .createHash("sha256")
    .update(project)
    .digest("hex")
    .slice(0, 24);
  const directory = path.join(
    os.tmpdir(),
    "industrial-pcb-gateway",
    identity,
  );
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  fs.chmodSync(path.dirname(directory), 0o700);
  fs.chmodSync(directory, 0o700);
  return directory;
}

class GatewaySession {
  constructor(config) {
    this.config = config;
    this.child = null;
    this.nextId = 1;
    this.pending = new Map();
    this.queue = Promise.resolve();
    this.stderr = "";
    this.closing = false;
    this.idle = null;
  }

  start() {
    this.child = spawn(this.config.command, this.config.args, {
      env: { ...process.env, ...this.config.env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child.stdout.setEncoding("utf8");
    this.child.stderr.setEncoding("utf8");
    let buffer = "";
    this.child.stdout.on("data", (chunk) => {
      buffer += chunk;
      let index;
      while ((index = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        if (!line.trim()) continue;
        let message;
        try {
          message = JSON.parse(line);
        } catch {
          continue;
        }
        const waiter = this.pending.get(message.id);
        if (!waiter) continue;
        this.pending.delete(message.id);
        if (message.error) waiter.reject(Error(message.error.message || "Gateway protocol error."));
        else waiter.resolve(message.result);
      }
    });
    this.child.stderr.on("data", (chunk) => {
      this.stderr = (this.stderr + chunk).slice(-8000);
    });
    this.child.on("exit", (code, signal) => {
      const failure = Error(
        `PCB gateway exited before replying (${signal || code}). ${this.stderr.slice(-2000)}`,
      );
      for (const waiter of this.pending.values()) waiter.reject(failure);
      this.pending.clear();
    });
    return this.handshake();
  }

  request(method, params, timeoutMs) {
    return new Promise((resolve, reject) => {
      if (!this.child || this.closing)
        return reject(Error("PCB gateway session is closed."));
      const id = this.nextId++;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(Error(`PCB gateway ${method} timed out after ${timeoutMs} ms.`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      this.child.stdin.write(
        JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n",
        (error) => {
          if (error) {
            clearTimeout(timer);
            this.pending.delete(id);
            reject(error);
          }
        },
      );
    });
  }

  async handshake() {
    const initialized = await this.request(
      "initialize",
      {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "industrial-agent-harness-pcb", version: "1" },
      },
      STARTUP_TIMEOUT_MS,
    );
    if (initialized?.serverInfo?.name !== "industrial-pcb-gateway")
      throw Error("Unexpected PCB gateway identity.");
    this.child.stdin.write(
      JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) +
        "\n",
    );
  }

  async callTool(name, arguments_, signal) {
    if (signal?.aborted) throw Error("Action was cancelled before the PCB gateway call.");
    // Serialize calls: the upstream controller locks the bound workspace, so
    // interleaved requests would only queue inside the container anyway.
    const run = this.queue.then(() =>
      this.request("tools/call", { name, arguments: arguments_ }, CALL_TIMEOUT_MS),
    );
    this.queue = run.catch(() => {});
    const result = await run;
    this.scheduleRecycle();
    if (signal?.aborted) throw Error("Action was cancelled; the bench mutation outcome may be unknown.");
    const block = (result.content || []).find((item) => item.type === "text");
    let payload = null;
    if (block) {
      try {
        payload = JSON.parse(block.text);
      } catch {
        payload = { text: block.text };
      }
    }
    if (payload?.paged) payload = await this.readPaged(payload);
    if (result.isError) {
      const detail = payload?.error ?? JSON.stringify(payload).slice(0, 2000);
      throw Error(`PCB gateway call failed: ${detail}`);
    }
    return payload;
  }

  // Reassemble a gateway-paged response (single JSON document split into
  // pages) up to a transport-side budget; larger tails stay in the gateway
  // cache with their receipts.
  async readPaged(payload, budget = 256 * 1024) {
    let text = "";
    let offset = 0;
    let nextOffset = 0;
    do {
      const page = await this.request(
        "tools/call",
        {
          name: "domain_tool_result_read",
          arguments: { responseId: payload.responseId, offset, limit: 4000 },
        },
        REQUEST_TIMEOUT_MS,
      );
      const block = (page.content || []).find((item) => item.type === "text");
      const data = block ? JSON.parse(block.text) : {};
      if (data.error) throw Error(`PCB gateway paging failed: ${data.error}`);
      text += data.text || "";
      nextOffset = data.nextOffset;
      offset = nextOffset;
    } while (nextOffset && text.length < budget);
    try {
      const value = JSON.parse(text);
      return nextOffset
        ? { ...value, truncated: true, nextOffset }
        : value;
    } catch {
      return { text, truncated: Boolean(nextOffset), nextOffset };
    }
  }

  scheduleRecycle() {
    clearTimeout(this.idle);
    this.idle = setTimeout(() => this.close(), IDLE_RECYCLE_MS);
    this.idle.unref?.();
  }

  close() {
    if (this.closing) return;
    this.closing = true;
    clearTimeout(this.idle);
    this.child?.stdin.end();
    this.child?.kill("SIGTERM");
  }
}

const sessions = new Map();

// One gateway process per bound project: the upstream controller holds an
// exclusive lock on the mounted workspace, and per-call restarts would lose
// the container session identity.
async function gatewaySession({ projectDir, environment }) {
  const project = fs.realpathSync(projectDir);
  const existing = sessions.get(project);
  if (existing && !existing.closing) return existing;
  const provider = bridgeProvider(
    require("../harness-pack.json").provider,
    require("./bench-upstream.json"),
  );
  const config = pcbGatewayConfig(
    sessionDirectory(project),
    provider,
    project,
    environment,
    { imageInput: environment.INDUSTRIAL_HARNESS_PCB_IMAGE_INPUT === "1" },
  );
  const session = new GatewaySession(config);
  sessions.set(project, session);
  try {
    await session.start();
  } catch (error) {
    sessions.delete(project);
    session.close();
    throw error;
  }
  session.scheduleRecycle();
  return session;
}

module.exports = {
  bridgeProvider,
  gatewaySession,
  sessionDirectory,
  GatewaySession,
  GATEWAY_MCP_VERSION,
};
