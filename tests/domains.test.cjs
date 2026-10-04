const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const packs = require("../lib/index.cjs");
const godot = require("../packs/godot/src/runtime.cjs");
const { validateRecipe } = require("../packs/freecad/runtime/recipe.cjs");
const { verifier } = require("../packs/chip/runtime/verifier.cjs");

test("catalog distinguishes maintained code, private PCB backend and unqualified platforms", () => {
  assert.deepEqual(
    packs.catalog.map((p) => p.id),
    ["chip-pack", "godot-pack", "pcb-pack", "freecad-pack", "cad-pack"],
  );
  assert.equal(
    packs.getPack("pcb-pack").remoteStatus,
    "not-qualified-private-backend",
  );
  assert.equal(
    packs.getPack("freecad-pack").qualification.linux,
    "not-qualified",
  );
  assert.throws(() => packs.getSandboxProfile("godot-pack", "rtl-cpu"));
});
test("migrated Godot reads real source, rejects traversal and retains observation status", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "domain-godot-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.writeFileSync(
    path.join(dir, "project.godot"),
    '[application]\nconfig/name="Sample"\nrun/main_scene="res://main.tscn"\n',
  );
  fs.writeFileSync(
    path.join(dir, "main.tscn"),
    '[gd_scene format=3]\n[node name="Root" type="Node2D"]\nposition = Vector2(1, 2)\n',
  );
  const result = godot.inspectSceneSource(dir, "main.tscn");
  assert.equal(result.sections[1].properties.position, "Vector2(1, 2)");
  assert.equal(result.verificationStatus, "not_run");
  assert.throws(() => godot.inspectSceneSource(dir, "../outside.tscn"));
  const provider = require("../packs/godot/harness-pack.json").provider;
  assert.equal(
    require("../lib/resources.cjs").validateResources(
      path.join(packs.root, "packs/godot"),
      provider,
    ),
    path.join(packs.root, "packs/godot"),
  );
});
test("migrated FreeCAD rejects script injection and invalid geometry recipes", () => {
  assert.throws(() =>
    validateRecipe({
      features: [{ id: "Part", op: "python", script: "anything" }],
      result: "Part",
    }),
  );
  assert.throws(() =>
    validateRecipe({
      features: [{ id: "Part", op: "box", length: -1, width: 2, height: 3 }],
      result: "Part",
    }),
  );
  assert.ok(
    validateRecipe({
      features: [{ id: "Part", op: "box", length: 1, width: 2, height: 3 }],
      result: "Part",
    }),
  );
});
test("one verifier accepts local/remote evidence and rejects assertion failures or missing native evidence", () => {
  function verify(
    report,
    log = "EDA_SIMULATION_FINISHED\n",
    native = { status: "PASS" },
  ) {
    const artifacts = [
      { kind: "log.tool", bytes: Buffer.from(log) },
      { kind: "report.simulation", bytes: Buffer.from(report) },
      {
        kind: "waveform.vcd",
        bytes: Buffer.from(
          "$var wire 1 ! x $end\n$enddefinitions $end\n#0\n0!\n",
        ),
      },
    ];
    return verifier({
      result: { nativeVerification: native },
      artifacts,
      readArtifact: (a) => a.bytes,
    });
  }
  assert.equal(verify("EDA_SIMULATION_FINISHED\n").status, "passed");
  assert.equal(
    verify(JSON.stringify({ completionMarker: "EDA_SIMULATION_FINISHED" }))
      .status,
    "passed",
  );
  assert.equal(
    verify("EDA_SIMULATION_FINISHED", "%Error: Assertion failed").status,
    "failed",
  );
  assert.equal(
    verify("EDA_SIMULATION_FINISHED", "", null).status,
    "insufficient_evidence",
  );
});
test("release integrity rejects changed execution source before use", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pack-integrity-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const lock = require("../content-lock.json");
  for (const file of Object.keys(lock.files)) {
    const target = path.join(dir, file);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(path.join(packs.root, file), target);
  }
  for (const file of ["package.json", "content-lock.json"])
    fs.copyFileSync(path.join(packs.root, file), path.join(dir, file));
  fs.appendFileSync(
    path.join(dir, "packs/chip/runtime/verifier.cjs"),
    "\n// changed\n",
  );
  assert.throws(
    () => require(path.join(dir, "lib/index.cjs")),
    /differs from its pinned release/,
  );
});
