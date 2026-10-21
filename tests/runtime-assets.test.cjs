const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const owner = require("../lib/index.cjs");

test("installed professional Packs export the exact qualified native downloads and executable mappings", () => {
  for (const domain of ["pcb", "godot"]) {
    const manifest = owner.hostPacks().find((pack) => pack.domain === domain);
    const lock = require(`../packs/${domain}/runtime/native-dependency.json`);
    assert.equal(manifest.runtimeAssets.length, 1);
    const asset = manifest.runtimeAssets[0];
    for (const key of ["url", "sha256", "version"])
      assert.equal(asset[key], lock[key]);
    assert.equal(asset.archiveApp, lock.bundle);
    assert.equal(asset.environment, lock.variable);
    assert.equal(path.posix.join(asset.app, asset.executable), lock.command);
    assert.equal(asset.platform, "darwin-arm64");
    assert.deepEqual(asset.versionArgs, ["--version"]);
    assert.equal(
      asset.type,
      domain === "pcb" ? "macos-app-dmg" : "macos-app-zip",
    );
    assert.equal(asset.size, domain === "pcb" ? 1404303659 : 170622178);
    assert.equal(
      asset.installedSize,
      domain === "pcb" ? 5104942382 : 353682925,
    );
    assert.equal(manifest.version, manifest.provider.version);
    assert.equal(
      owner.consumerMetadata().domains.find((item) => item.id === domain)
        .version,
      owner.getPack(manifest.id).releaseVersion,
    );
  }
  const pcb = owner.hostPacks().find((pack) => pack.domain === "pcb")
    .runtimeAssets[0];
  assert.deepEqual(pcb.environmentExecutables, {
    INDUSTRIAL_HARNESS_KICAD_PYTHON:
      "Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9",
  });
});

test("dependency metadata does not qualify extra platforms, GPU services or complete EDA installs", () => {
  const domains = owner.consumerMetadata().domains;
  const host = owner.hostPacks();
  const cad = host.find((pack) => pack.domain === "cad");
  assert.equal(
    cad.runtimeAssets.length,
    1,
    "Remote Linux image inputs are not desktop install assets",
  );
  assert.equal(cad.runtimeAssets[0].installedSize, 2625114576);
  assert.equal(cad.runtimeAssets[0].size, 649760538);
  for (const id of ["cad", "pcb", "godot"]) {
    for (const asset of host.find((pack) => pack.domain === id).runtimeAssets) {
      assert.equal(asset.platform, "darwin-arm64");
      assert.ok(["macos-app-dmg", "macos-app-zip"].includes(asset.type));
      assert.ok(Number.isSafeInteger(asset.size) && asset.size > 0);
      assert.match(asset.environment, /^INDUSTRIAL_HARNESS_[A-Z0-9_]+$/);
    }
    assert.deepEqual(
      domains.find((d) => d.id === id).qualifiedBundlePlatforms,
      ["darwin-arm64"],
    );
    assert.equal(
      owner.getPack(host.find((p) => p.domain === id).id).remoteStatus,
      "not-qualified",
    );
  }
  assert.deepEqual(
    domains.find((d) => d.id === "cuda").qualifiedBundlePlatforms,
    [],
  );
  for (const id of ["chip", "cuda"])
    assert.equal(
      host.find((pack) => pack.domain === id).runtimeAssets,
      undefined,
    );
  assert.match(
    domains.find((d) => d.id === "chip").prerequisites.join(" "),
    /does not install the complete EDA environment/,
  );
  assert.match(
    domains.find((d) => d.id === "cuda").prerequisites.join(" "),
    /Remote clients need no local GPU/,
  );
  // A consumer may edit its display copy without changing the next install recipe.
  cad.runtimeAssets[0].installedSize = 1;
  assert.equal(
    owner.hostPacks().find((pack) => pack.domain === "cad").runtimeAssets[0]
      .installedSize,
    2625114576,
  );
});

test("removing remote image inputs from host assets preserves the pinned Linux execution profile", () => {
  const profile = owner
    .getPack("freecad-pack")
    .profiles.find((item) => item.id === "freecad-linux-cpu");
  assert.equal(profile.platform, "linux/amd64");
  const recipe = fs.readFileSync(
    path.join(__dirname, "..", profile.recipe),
    "utf8",
  );
  assert.match(recipe, /FreeCAD_1\.1\.4-Linux-x86_64-py311\.AppImage/);
  assert.match(
    recipe,
    /f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434/,
  );
});
