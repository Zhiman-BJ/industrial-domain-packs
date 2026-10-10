const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const packs = require("../lib/index.cjs");
const { validateResources } = require("../lib/resources.cjs");

test("host consumers resolve canonical Pack identities and verified maintained resources", () => {
  const metadata = packs.consumerMetadata();
  assert.equal(metadata.coreApi, 1);
  assert.deepEqual(metadata.domains.map((item) => item.id).sort(), [
    "cad",
    "chip",
    "cuda",
    "godot",
    "pcb",
  ]);
  const host = packs.hostPacks();
  assert.equal(new Set(host.map((item) => item.id)).size, host.length);
  for (const item of host) {
    assert.equal(packs.getPack(item.id).domain, item.domain);
    assert.equal(item.version, item.provider.version);
    if (
      item.provider.transport === "runtime" ||
      item.provider.backend === "godot-local"
    )
      validateResources(packs.sourceDirectory(item.id), item.provider);
  }
  const freecad = host.find((item) => item.id === "freecad-pack");
  const chip = host.find((item) => item.id === "chip-pack");
  const sourceProject = fs.readFileSync(
    path.join(packs.sourceDirectory(chip.id), "eda-harness/pyproject.toml"),
    "utf8",
  );
  assert.equal(
    chip.provider.version,
    sourceProject.match(/^version = "([^"]+)"/m)[1],
  );
  for (const [domain, packId, version] of [
    ["chip", "chip-pack", "0.6.3"],
    ["pcb", "pcb-pack", "0.1.0-kicad.3"],
    ["godot", "godot-pack", "0.2.3"],
    ["cad", "freecad-pack", "1.1.4-pack.8"],
  ]) {
    assert.equal(metadata.domains.find((item) => item.id === domain).version, version);
    assert.equal(packs.getPack(packId).releaseVersion, version);
    // Chip's gateway reports its native Python package identity instead.
    if (domain !== "chip")
      assert.equal(host.find((item) => item.id === packId).version, version);
  }
  assert.ok(freecad.provider.sourceFiles["runtime/presentation.cjs"]);
  assert.equal(freecad.runtimeAssets[0].installedSize, 2625114576);
  assert.ok(
    freecad.provider.tools.find((item) => item.id === "cad.freecad.edit"),
  );
  for (const skill of metadata.skills) {
    const resource = packs.skillResource(skill.id);
    assert.ok(fs.statSync(path.join(resource.directory, "SKILL.md")).isFile());
    assert.ok(packs.getPack(skill.packId));
    if (skill.external)
      assert.ok(host.some((item) => item.id === skill.external.providerPackId));
  }
});

test("consumer metadata cannot mutate the pinned registry through a caller reference", () => {
  const first = packs.consumerMetadata();
  first.domains[0].id = "changed";
  assert.equal(packs.consumerMetadata().domains[0].id, "chip");
  const host = packs.hostPacks();
  host[0].provider.tools.length = 0;
  assert.ok(packs.hostPacks()[0].provider.tools.length > 0);
  assert.throws(() => packs.sourceDirectory("unknown-pack"));
  assert.throws(() => packs.skillResource("unknown-skill"));
});

test("gateway transport implementations belong to the pinned owner release", () => {
  for (const pack of packs
    .hostPacks()
    .filter((item) => item.provider.transport === "gateway")) {
    const adapter = packs.gatewayAdapter(pack.provider);
    assert.equal(typeof adapter.providerRuntime, "function");
    assert.equal(typeof adapter.gatewayConfig, "function");
    if (adapter.sourceHash)
      assert.equal(
        adapter.sourceHash(
          require("node:path").join(
            packs.sourceDirectory(pack.id),
            "eda-harness",
          ),
        ),
        pack.provider.sourceSha256,
      );
  }
});
