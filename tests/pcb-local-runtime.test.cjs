const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");
const verifier = require("../packs/pcb/runtime/verifier.cjs");
const entry = path.resolve(__dirname, "../packs/pcb/runtime/index.cjs");

// Execute the unchanged production entry on every test host. Only its platform
// view, macOS-only dependency root and external native-command boundary are
// fixtures: no KiCad runs, and the host need not contain /private/etc/ssl.
async function execute(t, options = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pcb-local-contract-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const project = path.join(dir, "project"),
    bin = path.join(dir, "dependency/bin");
  fs.mkdirSync(project);
  fs.mkdirSync(bin, { recursive: true });
  const executable = path.join(bin, "fixture");
  fs.writeFileSync(executable, "fixture", { mode: 0o700 });
  fs.writeFileSync(path.join(project, "board.kicad_pcb"), "SYNTHETIC BOARD");
  const inputs = {
    file: "board.kicad_pcb",
    expect: {
      bounds: [0, 0, 40, 30],
      footprints: [{ reference: "H1", position: [10, 10], rotation: 0 }],
      maxWarnings: 0,
    },
  };
  const expected = structuredClone(inputs.expect);
  const controller = new AbortController();
  const board = {
    schemaVersion: 1,
    version: "10.0.6",
    bounds: [0, 0, 40, 30],
    footprints: [{ reference: "H1", position: [10, 10], rotation: 0 }],
    ...options.board,
  };
  const drc = {
    kicad_version: "10.0.6",
    coordinate_units: "mm",
    violations: [],
    unconnected_items: [],
    schematic_parity: [],
    ...options.drc,
  };
  const fakeProcess = Object.create(process);
  Object.defineProperties(fakeProcess, {
    platform: { value: "darwin" },
    arch: { value: "arm64" },
  });
  const nativeRequire = createRequire(entry);
  const supportEntry = path.join(path.dirname(entry), "support.cjs");
  const supportModule = { exports: {} };
  let mappedSslRoots = 0;
  const fixtureFs = Object.create(fs);
  fixtureFs.realpathSync = (file, ...args) => {
    // This is the only platform-specific filesystem substitution. Always map
    // it, including on macOS, so every run exercises the Linux missing-root
    // case rather than accidentally depending on the host SSL directory.
    if (file === "/private/etc/ssl") {
      mappedSslRoots++;
      return fs.realpathSync(path.dirname(bin), ...args);
    }
    return fs.realpathSync(file, ...args);
  };
  vm.runInNewContext(fs.readFileSync(supportEntry, "utf8"), {
    require: (id) => (id === "node:fs" ? fixtureFs : nativeRequire(id)),
    module: supportModule,
    exports: supportModule.exports,
  });
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(entry, "utf8"), {
    require: (id) =>
      id === "./support.cjs" ? supportModule.exports : nativeRequire(id),
    module,
    exports: module.exports,
    __dirname: path.dirname(entry),
    process: fakeProcess,
    Buffer,
    structuredClone,
  });
  const plugin = module.exports.createRuntimePlugin({
    environment: {
      INDUSTRIAL_HARNESS_KICAD_CLI: executable,
      INDUSTRIAL_HARNESS_KICAD_PYTHON: executable,
    },
    runtimeApi: {
      runtimeFiles: {
        projectFile: (p, r) => path.join(p, r),
        readBytes: (f) => fs.readFileSync(f),
        runDirectory: (p, id) => {
          const d = path.join(p, ".harness-runs", id);
          fs.mkdirSync(d, { recursive: true });
          return d;
        },
      },
      executeTask: async ({ command }, ctx) => {
        if (options.mutateExpect) inputs.expect.bounds[2] = 999;
        if (options.cancelled) controller.abort();
        if (options.inputChanged)
          fs.writeFileSync(path.join(project, "board.kicad_pcb"), "CHANGED");
        if (ctx.actionId === "drc")
          fs.writeFileSync(
            command[command.indexOf("--output") + 1],
            JSON.stringify(drc),
          );
        else fs.writeFileSync(command.at(-1), JSON.stringify(board));
        return {
          status: options.nativeFailed ? "FAILED" : "COMPLETED",
          log: "SYNTHETIC native-command fixture",
        };
      },
    },
  });
  const result = await plugin.tools
    .find((x) => x.descriptor.id === "pcb.kicad.verify")
    .execute({
      projectDir: project,
      inputs,
      action: { id: "contract", inputs },
      signal: controller.signal,
    });
  assert.ok(mappedSslRoots > 0, "macOS SSL root used the portable fixture");
  return {
    result,
    expected,
    artifacts: result.artifacts,
    readArtifact: (a) => fs.readFileSync(a.file, "utf8"),
    verify: plugin.verifiers[verifier.ID],
  };
}

test("production local execute feeds its registered verifier without a CPU run report", async (t) => {
  const e = await execute(t);
  assert.equal(
    e.result.toolVersion,
    require("../packs/pcb/pack.json").releaseVersion,
  );
  assert.deepEqual(JSON.parse(JSON.stringify(e.result.expect)), e.expected);
  assert.equal(e.verify(e).status, "passed");
  assert.equal(e.verify(e).metrics.noExclusions, true);
  assert.equal(verifier.verify(e).status, "insufficient_evidence");
  assert.match(verifier.verify(e).reason, /report.pcb.run/);
});
test("validated expectation snapshot survives changes during native execution", async (t) => {
  const e = await execute(t, { mutateExpect: true });
  assert.deepEqual(JSON.parse(JSON.stringify(e.result.expect)), e.expected);
  assert.equal(e.verify(e).status, "passed");
});
for (const [name, options, metric] of [
  ["bounds", { board: { bounds: [0, 0, 41, 30] } }, "boardBounds"],
  [
    "placement",
    {
      board: {
        footprints: [{ reference: "H1", position: [11, 10], rotation: 0 }],
      },
    },
    "footprint0",
  ],
  [
    "rotation",
    {
      board: {
        footprints: [{ reference: "H1", position: [10, 10], rotation: 90 }],
      },
    },
    "footprint0",
  ],
  [
    "duplicate footprint",
    {
      board: {
        footprints: [
          { reference: "H1", position: [10, 10] },
          { reference: "H1", position: [10, 10] },
        ],
      },
    },
    "footprint0",
  ],
  ["warnings", { drc: { violations: [{ severity: "warning" }] } }, "warnings"],
  ["errors", { drc: { violations: [{ severity: "error" }] } }, "drcErrors"],
  ["unconnected", { drc: { unconnected_items: [{}] } }, "unconnected"],
  [
    "excluded severity",
    { drc: { violations: [{ severity: "excluded" }] } },
    "noExclusions",
  ],
  ["readback version", { board: { version: "9.0.0" } }, "nativeVersion"],
  ["DRC version", { drc: { kicad_version: "9.0.0" } }, "nativeVersion"],
])
  test("local rejects wrong " + name, async (t) => {
    const e = await execute(t, options);
    const v = e.verify(e);
    assert.equal(v.status, "failed");
    assert.equal(v.metrics[metric], false);
  });
for (const [name, options] of [
  ["changed inputs", { inputChanged: true }],
  ["failed native command", { nativeFailed: true }],
  ["cancelled native command", { cancelled: true }],
  ["bad readback schema", { board: { schemaVersion: 2 } }],
  ["bad DRC schema", { drc: { schematic_parity: null } }],
])
  test("local lacks sufficient evidence for " + name, async (t) => {
    const e = await execute(t, options);
    assert.equal(e.verify(e).status, "insufficient_evidence");
  });
test("local rejects missing, corrupt reports and missing expectations", async (t) => {
  const e = await execute(t);
  assert.equal(
    e.verify({
      ...e,
      artifacts: e.artifacts.filter((a) => a.kind !== "report.pcb.drc"),
    }).status,
    "insufficient_evidence",
  );
  assert.equal(
    e.verify({
      ...e,
      artifacts: e.artifacts.filter((a) => a.kind !== "report.pcb.readback"),
    }).status,
    "insufficient_evidence",
  );
  assert.equal(
    e.verify({ ...e, readArtifact: () => "{" }).status,
    "insufficient_evidence",
  );
  assert.equal(
    e.verify({ ...e, readArtifact: () => "null" }).status,
    "insufficient_evidence",
  );
  assert.equal(
    e.verify({ ...e, result: { ...e.result, expect: undefined } }).status,
    "insufficient_evidence",
  );
});
test("remote keeps CPU run/profile/schema/version requirements with no local fallback", async (t) => {
  const e = await execute(t),
    report = {
      schemaVersion: "1",
      profileId: "pcb-verify-cpu",
      executionSucceeded: true,
      inputUnchanged: true,
      toolVersion: "10.0.6",
    };
  const remote = (r) => ({
    ...e,
    artifacts: [...e.artifacts, { kind: "report.pcb.run" }],
    readArtifact: (a) =>
      a.kind === "report.pcb.run" ? JSON.stringify(r) : e.readArtifact(a),
  });
  assert.equal(verifier.verify(remote(report)).status, "passed");
  for (const patch of [
    { profileId: "pcb-verify-native" },
    { schemaVersion: 1 },
    { executionSucceeded: false },
    { inputUnchanged: false },
  ])
    assert.equal(
      verifier.verify(remote({ ...report, ...patch })).status,
      "insufficient_evidence",
    );
  assert.equal(
    verifier.verify(remote({ ...report, toolVersion: "0.3.1" })).status,
    "failed",
  );
  assert.equal(
    verifier.verify({
      ...remote(report),
      result: { ...e.result, expect: undefined },
    }).status,
    "insufficient_evidence",
  );
  assert.equal(
    verifier.verify({
      ...remote(report),
      readArtifact: (a) =>
        a.kind === "report.pcb.run" ? "{" : e.readArtifact(a),
    }).status,
    "insufficient_evidence",
  );
});
