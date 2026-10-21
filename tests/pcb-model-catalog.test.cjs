const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const owner = require("../lib/index.cjs");

test("catalog generation rejects altered, incomplete and over-budget upstream data", () => {
  execFileSync("python3", [path.join(__dirname, "pcb-model-catalog.test.py")], { timeout: 30000 });
});

test("optional official KiCad data has a complete immutable catalog and independent identity", () => {
  const manifest = owner.hostPacks().find(pack => pack.id === "pcb-pack");
  assert.equal(manifest.dataLibraries.length, 1);
  const library = manifest.dataLibraries[0];
  const root = owner.sourceDirectory(manifest.id);
  const bytes = fs.readFileSync(path.join(root, library.catalog));
  assert.equal(crypto.createHash("sha256").update(bytes).digest("hex"), library.catalogSha256);
  const catalog = JSON.parse(bytes);
  assert.equal(catalog.schemaVersion, 2, "API transport requires an updated Harness consumer");
  assert.equal(catalog.id, "kicad-3d");
  assert.equal(catalog.version, "10.0.7");
  assert.equal(catalog.baseUrl, library.baseUrl);
  assert.equal(library.baseUrl, "https://gitlab.com/api/v4/");
  assert.deepEqual(library.download, {
    type: "gitlab-repository-files",
    project: "kicad/libraries/kicad-packages3D",
    ref: "8d4070daa0fa3f7a7f3c176f4ca7bffc4df392be",
  });
  assert.deepEqual(catalog.download, library.download);
  assert.equal(catalog.download.ref, catalog.source.revision);
  assert.equal(catalog.source.revision, "8d4070daa0fa3f7a7f3c176f4ca7bffc4df392be");
  assert.equal(catalog.files.length, 7252);
  assert.equal(new Set(catalog.files.map(file => file.path)).size, 7252);
  assert.equal(
    crypto.createHash("sha256").update(JSON.stringify(catalog.files)).digest("hex"),
    "5e2ea499ceaa96327b3756e45619ed9d06f21f825ea657d83f38324743d611ee",
    "changing download transport preserves every original path, byte count and checksum",
  );
  assert.equal(manifest.version, "0.3.3", "host Runtime identity is unchanged");
  assert.equal(manifest.provider.version, "0.3.3", "engineering provider identity is unchanged");
  for (const file of catalog.files) {
    assert.match(file.path, /^[^\\\0]+\.step$/);
    assert.ok(!file.path.split("/").includes("..") && !file.path.startsWith("/"));
    assert.ok(Number.isSafeInteger(file.size) && file.size > 0);
    assert.match(file.sha256, /^[a-f0-9]{64}$/);
  }
  assert.ok(catalog.files.some(file => file.size > 8 * 1024 * 1024), "catalog preserves large models; viewer admission is separate");
  assert.equal(catalog.license.spdx, "CC-BY-SA-4.0");
  assert.ok(fs.existsSync(path.resolve(root, path.dirname(library.catalog), catalog.license.notice)));
  assert.ok(library.referencePrefixes.includes("${KICAD10_3DMODEL_DIR}/"));
  assert.ok(library.referencePrefixes.includes("${KISYS3DMOD}/"));
  assert.equal(manifest.runtimeAssets.length, 1, "optional model data is separate from native app installation");
  assert.equal(manifest.runtimeAssets[0].version, "10.0.6");
  const packaged = require("../package.json").files;
  assert.ok(packaged.includes("packs/pcb/" + library.catalog));
  assert.ok(packaged.includes("packs/pcb/resources/licenses/kicad-3d-LICENSE.md"));
});
