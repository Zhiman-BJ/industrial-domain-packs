const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const owner = require("../lib/index.cjs");
const { edit } = require("../packs/godot/runtime/edit.cjs");
const { validateExpect } = require("../packs/godot/runtime/index.cjs");
const { validate } = require("../packs/pcb/runtime/index.cjs");
test("public professional profiles disclose only Runtime Actions with independent versioned Verifiers", () => {
  for (const domain of ["pcb", "godot"]) {
    const pack = owner.hostPacks().find((p) => p.domain === domain);
    assert.equal(pack.provider.transport, "runtime");
    assert.ok(pack.provider.sourceFiles["runtime/verifier.cjs"]);
    assert.deepEqual(
      pack.provider.tools.map((t) => t.id),
      domain === "pcb"
        ? ["pcb.kicad.edit", "pcb.kicad.verify"]
        : ["godot.scene.edit", "godot.scene.verify"],
    );
    assert.ok(
      !pack.provider.tools.some(
        (t) => t.id.startsWith("pcb.bench.") || t.id.startsWith("godot.game."),
      ),
    );
    assert.deepEqual(
      owner.consumerMetadata().domains.find((d) => d.id === domain)
        .qualifiedBundlePlatforms,
      ["darwin-arm64"],
    );
  }
  assert.equal(
    owner.consumerMetadata().skills.find((s) => s.id === "pcb.design.e2e")
      .external,
    undefined,
  );
});
test("typed scene edits reject arbitrary expressions, unknown/duplicate sections and invalid vectors", () => {
  const scene = fs.readFileSync(
    path.join(
      owner.sourceDirectory("godot-pack"),
      "examples/structural/main.tscn",
    ),
    "utf8",
  );
  assert.throws(() =>
    edit(scene, [{ section: "node:Box", property: "script", value: "exec" }]),
  );
  assert.throws(() =>
    edit(scene, [
      {
        section: "node:Box",
        property: "position",
        value: { type: "Vector3", value: [1, 2, Infinity] },
      },
    ]),
  );
  assert.throws(() =>
    edit(scene, [
      { section: "node:Missing", property: "visible", value: false },
    ]),
  );
  const change = {
    section: "node:Box",
    property: "position",
    value: { type: "Vector3", value: [1, 2, 3] },
  };
  assert.throws(() => edit(scene, [change, change]));
  assert.throws(() =>
    validateExpect({ file: "main.tscn", frames: 1, expect: [] }),
  );
  assert.throws(() =>
    validateExpect({
      file: "main.tscn",
      frames: 181,
      expect: [{ node: ".", property: "visible", value: true }],
    }),
  );
});
test("KiCad typed requirements reject absent dimensions, invalid placements and arbitrary input fields", () => {
  assert.throws(() =>
    validate("verify", { file: "board.kicad_pcb", expect: {} }),
  );
  assert.throws(() =>
    validate("edit", {
      file: "board.kicad_pcb",
      expectedSha256: "a".repeat(64),
      rectangle: { origin: [0, 0], size: [-1, 5] },
    }),
  );
  assert.throws(() =>
    validate("edit", {
      file: "board.kicad_pcb",
      expectedSha256: "a".repeat(64),
      moves: [
        { reference: "H1", position: [1, 2] },
        { reference: "H1", position: [3, 4] },
      ],
    }),
  );
  assert.throws(() =>
    validate("verify", {
      file: "board.kicad_pcb",
      expect: { bounds: [0, 0, 4, 5] },
      command: "shell",
    }),
  );
});

test("only the exact offline Godot TLS bootstrap diagnostic is nonfatal; script, import and other native failures remain failures", () => {
  const { completed } = require("../packs/godot/runtime/diagnostics.cjs");
  const tls =
    'ERROR: Condition "ret != noErr" is true. Returning: ""\n   at: get_system_ca_certificates (platform/macos/os_macos.mm:1035)';
  assert.equal(completed({ status: "COMPLETED", log: tls }), true);
  for (const log of [
    tls + "\nSCRIPT ERROR: bad script",
    "ERROR: missing resource",
    "Parse Error: invalid scene",
    tls.replace("1035", "1036"),
  ])
    assert.equal(completed({ status: "COMPLETED", log }), false);
  assert.equal(completed({ status: "TIMEOUT", log: tls }), false);
});
