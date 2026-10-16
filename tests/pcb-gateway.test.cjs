const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const owner = require("../lib/index.cjs");

const packDirectory = owner.sourceDirectory("pcb-pack");
const snapshot = JSON.parse(
  fs.readFileSync(path.join(packDirectory, "runtime/upstream-snapshot.json"), "utf8"),
);
const {
  bridgeProvider,
  gatewaySession,
  sessionDirectory,
  GatewaySession,
  GATEWAY_MCP_VERSION,
} = require("../packs/pcb/runtime/gateway.cjs");

const manifestProvider = JSON.parse(
  fs.readFileSync(path.join(packDirectory, "harness-pack.json"), "utf8"),
).provider;

test("bridge provider view carries the pinned upstream inventory", () => {
  const provider = bridgeProvider(manifestProvider, snapshot);
  assert.equal(provider.installedDirectory, path.join(packDirectory));
  assert.equal(provider.directoryEnv, "INDUSTRIAL_HARNESS_PCB_UPSTREAM_DIR");
  assert.equal(provider.pythonEnv, "INDUSTRIAL_HARNESS_PCB_GATEWAY_PYTHON");
  assert.equal(provider.mcpVersion, GATEWAY_MCP_VERSION);
  assert.deepEqual(provider.resourceRoots, ["pcb-agent", "skills/pcb-design-e2e"]);
  assert.equal(provider.sourceCommit, snapshot.sourceCommit);
  assert.equal(provider.imageId, snapshot.imageId);
  assert.equal(provider.toolSchemaSha256, snapshot.toolSchemaSha256);
  assert.equal(provider.tools.length, 89);
  assert.deepEqual(
    provider.allowedToolIds,
    snapshot.tools.map((tool) => tool.id),
  );
  // The bridge validates the checkout tree against this exact digest.
  const compact = JSON.stringify(
    Object.fromEntries(
      Object.keys(snapshot.sourceFiles)
        .sort()
        .map((key) => [key, snapshot.sourceFiles[key]]),
    ),
  );
  assert.equal(
    provider.sourceSha256,
    crypto.createHash("sha256").update(compact).digest("hex"),
  );
});

test("session directory is private and stable per project", () => {
  const first = sessionDirectory("/tmp");
  const second = sessionDirectory("/tmp");
  assert.equal(first, second);
  const stats = fs.statSync(first);
  assert.ok(stats.isDirectory());
  assert.equal(stats.mode & 0o777, 0o700);
  assert.ok(first.startsWith(path.join(os.tmpdir(), "industrial-pcb-gateway")));
});

async function fakeSession() {
  const session = new GatewaySession({
    command: process.execPath,
    args: [path.join(__dirname, "fixtures/pcb-gateway-fake.cjs")],
    env: {},
  });
  await session.start();
  return session;
}

test("gateway session handshakes and returns observation payloads", async () => {
  const session = await fakeSession();
  const payload = await session.callTool("domain_tool_call", {
    toolId: "pcb.design.project_status",
    arguments: {},
  });
  assert.equal(payload.toolId, "pcb.design.project_status");
  assert.equal(payload.result.status, "ok");
  session.close();
});

test("paged responses are reassembled into one document", async () => {
  const session = await fakeSession();
  const payload = await session.callTool("domain_tool_call", {
    toolId: "pcb.design.inspect_board",
    arguments: { collection: "tracks" },
  });
  assert.equal(payload.toolId, "pcb.design.inspect_board");
  assert.deepEqual(payload.result.collection, ["x".repeat(40), "y".repeat(40)]);
  assert.equal(payload.truncated, undefined);
  session.close();
});

test("gateway errors surface as rejections with their detail", async () => {
  const session = await fakeSession();
  await assert.rejects(
    session.callTool("domain_tool_call", {
      toolId: "pcb.design.add_track",
      arguments: {},
    }),
    /outside scope/,
  );
  session.close();
});

test("plugin dispatch rejects with setup guidance before any gateway start", async () => {
  const { createRuntimePlugin } = require("../packs/pcb/runtime/index.cjs");
  // Force a deterministic prerequisite failure: the gateway venv is resolved
  // from this (bogus) env var before anything spawns.
  const plugin = createRuntimePlugin({
    environment: { INDUSTRIAL_HARNESS_PCB_GATEWAY_PYTHON: "/nonexistent/python" },
    runtimeApi: { executeTask: () => {}, runtimeFiles: () => {} },
  });
  const status = plugin.tools.find(
    (tool) => tool.descriptor.id === "pcb.design.project_status",
  );
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "pcb-design-dispatch-"));
  try {
    await assert.rejects(
      status.execute({
        projectDir: scratch,
        inputs: {},
        action: {},
        signal: new AbortController().signal,
      }),
      /uv sync --frozen --no-dev/,
    );
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});

// Full container path: set INDUSTRIAL_HARNESS_PCB_UPSTREAM_DIR (authorized
// upstream checkout), prepare the gateway venv (uv sync --frozen --no-dev)
// and Docker with the pinned image, then this exercises real dispatch.
test("real container dispatch when the container environment is provided", async (t) => {
  const configured = process.env.INDUSTRIAL_HARNESS_PCB_UPSTREAM_DIR;
  if (!configured) return t.skip("INDUSTRIAL_HARNESS_PCB_UPSTREAM_DIR is not configured");
  const { createRuntimePlugin } = require("../packs/pcb/runtime/index.cjs");
  const plugin = createRuntimePlugin({
    environment: process.env,
    runtimeApi: { executeTask: () => {}, runtimeFiles: () => {} },
  });
  const status = plugin.tools.find(
    (tool) => tool.descriptor.id === "pcb.design.project_status",
  );
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "pcb-design-live-"));
  let session;
  try {
    session = await gatewaySession({ projectDir: scratch, environment: process.env });
    const result = await status.execute({
      projectDir: scratch,
      inputs: {},
      action: {},
      signal: new AbortController().signal,
    });
    assert.equal(result.executionSucceeded, true);
    const payload = JSON.parse(result.diagnostics[0]);
    assert.equal(payload.toolId, "pcb.design.project_status");
  } finally {
    session?.close();
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});
