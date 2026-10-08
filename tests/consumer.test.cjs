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
  assert.equal(freecad.version, "1.1.4-pack.4");
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
