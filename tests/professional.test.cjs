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
test("scene transform edits reject untyped values before any scene text is produced", () => {
  const scene = fs.readFileSync(
    path.join(
      owner.sourceDirectory("godot-pack"),
      "examples/structural/main.tscn",
    ),
    "utf8",
  );
  const at = (property, value) => [
    { section: "node:Box", property, value },
  ];
  assert.throws(() => edit(scene, at("position", false)));
  assert.throws(() => edit(scene, at("position", 7)));
  assert.throws(() => edit(scene, at("position", "Vector3(1, 2, 3)")));
  assert.throws(() => edit(scene, at("rotation_degrees", true)));
  assert.throws(() => edit(scene, at("scale", 2)));
  assert.throws(() =>
    edit(scene, at("position", { type: "Vector2", value: [1, 2] })),
  );
  assert.throws(() =>
    edit(scene, at("position", { type: "Vector3", value: [1, 2] })),
  );
  assert.throws(() =>
    edit(scene, at("position", { type: "Vector3", value: [1, 2, 3], unit: 1 })),
  );
});
test("Node2D-family scenes accept Vector2 transforms and reject 3D values and scalar rotation", () => {
  const scene = [
    "[gd_scene format=3]",
    "",
    '[node name="Main" type="Node2D"]',
    "",
    '[node name="Sprite" type="Sprite2D" parent="."]',
    "position = Vector2(1, 2)",
    "",
  ].join("\n");
  const updated = edit(scene, [
    {
      section: "node:Sprite",
      property: "position",
      value: { type: "Vector2", value: [3, 4] },
    },
    {
      section: "node:Sprite",
      property: "scale",
      value: { type: "Vector2", value: [2, 2] },
    },
  ]);
  assert.ok(updated.includes("position = Vector2(3, 4)"));
  assert.ok(updated.includes("scale = Vector2(2, 2)"));
  assert.throws(() =>
    edit(scene, [
      {
        section: "node:Sprite",
        property: "position",
        value: { type: "Vector3", value: [1, 2, 3] },
      },
    ]),
  );
  assert.throws(() =>
    edit(scene, [{ section: "node:Sprite", property: "rotation_degrees", value: 45 }]),
  );
  assert.throws(() =>
    edit(scene, [
      {
        section: "node:Sprite",
        property: "rotation_degrees",
        value: { type: "Vector2", value: [45, 0] },
      },
    ]),
  );
  const plain = scene.replace('type="Sprite2D"', 'type="Node"');
  assert.throws(() =>
    edit(plain, [
      {
        section: "node:Sprite",
        property: "position",
        value: { type: "Vector2", value: [1, 2] },
      },
    ]),
  );
});
test("godot.scene.edit rejects invalid changes without touching the source file", async t => {
  const crypto = require("node:crypto");
  const os = require("node:os");
  const { createRuntimePlugin } = require("../packs/godot/runtime/index.cjs");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "godot-edit-tool-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const projectDir = path.join(directory, "project");
  fs.mkdirSync(projectDir);
  const scenePath = path.join(projectDir, "main.tscn");
  const original = fs.readFileSync(
    path.join(
      owner.sourceDirectory("godot-pack"),
      "examples/structural/main.tscn",
    ),
  );
  fs.writeFileSync(scenePath, original);
  let applied = 0;
  const plugin = createRuntimePlugin({
    runtimeApi: {
      executeTask: () => {
        throw Error("scene edit must not spawn native processes");
      },
      runtimeFiles: {
        projectFile: (dir, relative) => {
          const file = path.resolve(dir, relative);
          if (file !== path.resolve(dir) && !file.startsWith(path.resolve(dir) + path.sep))
            throw Error("Project file escapes its directory.");
          return file;
        },
        readBytes: file => fs.readFileSync(file),
        runDirectory: (dir, id) => {
          const run = path.join(dir, ".harness-runs", id);
          fs.mkdirSync(run, { recursive: true });
          return run;
        },
        applyFiles: (dir, files) => {
          applied += 1;
          for (const entry of files)
            fs.writeFileSync(path.join(dir, entry.path), entry.content);
        },
      },
    },
  });
  const tool = plugin.tools.find(
    item => item.descriptor.id === "godot.scene.edit",
  );
  const inputs = {
    file: "main.tscn",
    expectedSha256: crypto.createHash("sha256").update(original).digest("hex"),
    changes: [{ section: "node:Box", property: "position", value: false }],
  };
  await assert.rejects(() =>
    tool.execute({ projectDir, inputs, action: { id: "reject-probe" } }),
  );
  assert.equal(applied, 0);
  assert.ok(fs.readFileSync(scenePath).equals(original));
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
