const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const owner = require("../lib/index.cjs");

const packDirectory = owner.sourceDirectory("pcb-pack");
const snapshot = JSON.parse(
  fs.readFileSync(path.join(packDirectory, "runtime/bench-upstream.json"), "utf8"),
);
const manifest = JSON.parse(
  fs.readFileSync(path.join(packDirectory, "harness-pack.json"), "utf8"),
);

const hash = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");

test("bench snapshot pins a complete upstream identity", () => {
  assert.equal(snapshot.schemaVersion, 1);
  assert.match(snapshot.sourceCommit, /^[0-9a-f]{40}$/);
  assert.match(snapshot.imageId, /^sha256:[0-9a-f]{64}$/);
  assert.match(snapshot.toolSchemaSha256, /^[0-9a-f]{64}$/);
  assert.equal(snapshot.canonicalToolCount, 88);
  assert.equal(snapshot.sessionToolCount, 89);
  assert.equal(snapshot.tools.length, 89);
  assert.equal(snapshot.sourceCommit, manifest.provider.sourceCommit);
  assert.equal(snapshot.imageId, manifest.provider.imageId);
  assert.equal(snapshot.toolSchemaSha256, manifest.provider.toolSchemaSha256);
  assert.equal(Object.keys(snapshot.sourceFiles).length, 112);
  for (const [file, digest] of Object.entries(snapshot.sourceFiles))
    assert.match(digest, /^[0-9a-f]{64}$/, `${file} digest`);
});

test("bench tool registry is well formed and unique", () => {
  const ids = new Set();
  const names = new Set();
  for (const tool of snapshot.tools) {
    assert.match(tool.id, /^pcb\.bench\.[a-z0-9_]+$/);
    assert.ok(!ids.has(tool.id), `duplicate id ${tool.id}`);
    assert.ok(!names.has(tool.name), `duplicate name ${tool.name}`);
    ids.add(tool.id);
    names.add(tool.name);
    assert.ok(["read-only", "mutating"].includes(tool.risk), tool.id);
    assert.equal(typeof tool.summary, "string");
    assert.ok(tool.summary.length > 8, `${tool.id} summary is substantive`);
    if (tool.risk === "mutating") {
      assert.deepEqual(tool.verification, ["pcb.bench.receipts.v1"]);
    } else {
      assert.equal(tool.verification, undefined);
    }
  }
  assert.ok(names.has("run_python"));
  assert.equal(names.size - 1, snapshot.canonicalToolCount);
});

test("provider declares the bench surface verbatim plus the native profile", () => {
  const provider = manifest.provider;
  assert.equal(manifest.version, provider.version);
  const bench = provider.tools.filter((t) => t.id.startsWith("pcb.bench."));
  const native = provider.tools.filter((t) => !t.id.startsWith("pcb.bench."));
  assert.equal(bench.length, 89);
  assert.deepEqual(
    native.map((t) => t.id),
    ["pcb.kicad.edit", "pcb.kicad.verify"],
  );
  const byId = new Map(snapshot.tools.map((t) => [t.id, t]));
  for (const tool of bench)
    assert.deepEqual(tool, byId.get(tool.id), `${tool.id} matches snapshot`);
});

test("seven stage capabilities mirror the snapshot groups and provider tools", () => {
  const groups = manifest.capabilities.filter((c) =>
    c.id.startsWith("pcb.bench."),
  );
  assert.deepEqual(
    groups.map((c) => c.id),
    [
      "pcb.bench.core",
      "pcb.bench.components",
      "pcb.bench.schematic",
      "pcb.bench.pcb",
      "pcb.bench.verification",
      "pcb.bench.delivery",
      "pcb.bench.operate",
    ],
  );
  const byId = new Map(manifest.provider.tools.map((t) => [t.id, t]));
  const covered = new Set();
  for (const capability of groups) {
    assert.ok(capability.keywords.length > 0);
    assert.equal(capability.skills[0].id, "pcb.design.e2e");
    for (const tool of capability.tools) {
      assert.deepEqual(tool, byId.get(tool.id), `${capability.id} ${tool.id}`);
      covered.add(tool.id);
    }
  }
  assert.deepEqual([...covered].sort(), [...new Set(covered)].sort());
  assert.equal(covered.size, 89);
  const native = manifest.capabilities.find((c) => c.id === "pcb.native.task");
  assert.deepEqual(
    native.skills.map((s) => s.id).sort(),
    ["pcb.design.e2e", "pcb.kicad.native", "pcb.layout.inspect"].sort(),
  );
});

test("vendored actor and skill files match the pinned upstream hashes", () => {
  const expected = Object.entries(snapshot.sourceFiles)
    .filter(([file]) => file.startsWith("skills/pcb-design-e2e/"))
    .map(([file]) => file.slice("skills/pcb-design-e2e/".length))
    .sort();
  assert.equal(expected.length, 11);
  assert.ok(expected.includes("SKILL.md"));
  assert.equal(expected.filter((n) => n.startsWith("references/")).length, 9);
  assert.equal(expected.filter((n) => n.startsWith("assets/")).length, 1);
  const actor = Object.keys(snapshot.sourceFiles).filter((file) =>
    file.startsWith("pcb-agent/"),
  );
  assert.equal(actor.length, 101);
  for (const file of Object.keys(snapshot.sourceFiles)) {
    const bytes = fs.readFileSync(path.join(packDirectory, file));
    assert.equal(hash(bytes), snapshot.sourceFiles[file], file);
  }
  const nativeSkill = fs.readFileSync(
    path.join(packDirectory, "skills/pcb-kicad-native/SKILL.md"),
    "utf8",
  );
  assert.match(nativeSkill, /^name: pcb-kicad-native$/m);
});

test("consumer metadata routes the new skill and version", () => {
  const metadata = owner.consumerMetadata();
  const domain = metadata.domains.find((d) => d.id === "pcb");
  assert.equal(domain.version, manifest.version);
  for (const id of ["pcb.design.e2e", "pcb.kicad.native", "pcb.layout.inspect"]) {
    const skill = metadata.skills.find((s) => s.id === id);
    assert.ok(skill, id);
    assert.equal(skill.packId, "pcb-pack");
    assert.ok(
      fs.existsSync(path.join(packDirectory, skill.resourcePath, "SKILL.md")),
      id,
    );
  }
});

test("runtime inventory and digest stay exact", () => {
  const provider = manifest.provider;
  const files = {};
  for (const name of fs.readdirSync(path.join(packDirectory, "runtime")))
    files["runtime/" + name] = hash(
      fs.readFileSync(path.join(packDirectory, "runtime", name)),
    );
  assert.deepEqual(provider.sourceFiles, files);
  const compact = JSON.stringify(
    Object.fromEntries(Object.keys(files).sort().map((k) => [k, files[k]])),
  );
  assert.equal(provider.sourceSha256, hash(Buffer.from(compact)));
});

test("runtime plugin exposes placeholder bench tools that fail with setup guidance", async () => {
  const { createRuntimePlugin } = require(path.join(
    packDirectory,
    "runtime/index.cjs",
  ));
  const plugin = createRuntimePlugin({
    runtimeApi: { executeTask: () => {}, runtimeFiles: () => {} },
  });
  const bench = plugin.tools.filter((t) =>
    t.descriptor.id.startsWith("pcb.bench."),
  );
  assert.equal(bench.length, 89);
  assert.equal(plugin.tools.length, 91);
  const status = bench.find(
    (t) => t.descriptor.id === "pcb.bench.project_status",
  );
  assert.equal(status.descriptor.risk, "read-only");
  const runPython = bench.find(
    (t) => t.descriptor.id === "pcb.bench.run_python",
  );
  assert.equal(runPython.descriptor.risk, "read-only");
  const addTrack = bench.find((t) => t.descriptor.id === "pcb.bench.add_track");
  assert.equal(addTrack.descriptor.risk, "mutating");
  assert.equal(addTrack.descriptor.effect, "inputs");
  assert.deepEqual(addTrack.descriptor.verification, ["pcb.bench.receipts.v1"]);
  assert.equal(
    typeof plugin.verifiers["pcb.bench.receipts.v1"],
    "function",
    "declared verifier must be registered",
  );
  const assessed = plugin.verifiers["pcb.bench.receipts.v1"]();
  assert.equal(assessed.status, "not_run");
  assert.ok(addTrack.guide.description.includes("Dockerfile.local-dev"));
  const { createRuntimePlugin: freshPlugin } = require("../packs/pcb/runtime/index.cjs");
  const bare = freshPlugin({
    environment: { INDUSTRIAL_HARNESS_PCB_GATEWAY_PYTHON: "/nonexistent/python" },
    runtimeApi: { executeTask: () => {}, runtimeFiles: () => {} },
  });
  const bareTrack = bare.tools.find(
    (t) => t.descriptor.id === "pcb.bench.add_track",
  );
  await assert.rejects(
    bareTrack.execute({ projectDir: "/tmp", inputs: {}, action: {}, signal: new AbortController().signal }),
    /uv sync --frozen --no-dev/,
  );
});

test("published file whitelist carries the vendored skill tree and actor", () => {
  const pkg = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"),
  );
  const expected = [
    "packs/pcb/runtime/bench-upstream.json",
    "packs/pcb/skills/pcb-kicad-native/SKILL.md",
    "packs/pcb/skills/pcb-design-e2e/assets/constraints.example.yaml",
    "packs/pcb/skills/pcb-design-e2e/references/tools.md",
    "packs/pcb/skills/pcb-design-e2e/references/verification.md",
    "packs/pcb/pcb-agent/tools/workspace.py",
    "packs/pcb/pcb-agent/tools/agent_session.py",
    "packs/pcb/pcb-agent/tools/kimi_mcp.py",
  ];
  for (const entry of expected) assert.ok(pkg.files.includes(entry), entry);
  const vendored = pkg.files.filter((entry) => entry.startsWith("packs/pcb/pcb-agent/"));
  assert.equal(vendored.length, 101);
});
