#!/usr/bin/env node
// Maintainer-only generation/recheck. Consumers use the signed Pack declaration;
// opening a layout never runs this script or queries a package registry.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const directory = path.resolve(__dirname, "../resources/klayout-viewer");
const manifestFile = path.resolve(__dirname, "../harness-pack.json");

function buildEnvironment(assets, probe) {
  assert.equal(assets.schemaVersion, 1);
  assert.equal(assets.klayoutVersion, "0.30.12");
  assert.equal(assets.pythonVersion, "3.12.15");
  assert.equal(assets.pythonBuild, "20261013");
  assert.ok(Buffer.byteLength(probe, "utf8") > 0 && Buffer.byteLength(probe, "utf8") <= 16384);
  const platforms = {};
  for (const [platform, entry] of Object.entries(assets.platforms)) {
    const interpreter = entry.interpreter;
    const wheel = entry.wheel;
    platforms[platform] = {
      interpreter: {
        url: interpreter.url,
        sha256: interpreter.sha256,
        size: interpreter.size,
        executable: interpreter.executable,
        format: interpreter.format,
      },
      wheels: [{
        name: wheel.name,
        version: wheel.version,
        filename: wheel.filename,
        url: wheel.url,
        sha256: wheel.sha256,
        size: wheel.size,
      }],
      installedSize: entry.installedSize,
    };
  }
  return {
    id: "klayout-viewer",
    label: "KLayout layout viewer",
    version: assets.klayoutVersion,
    purpose: "viewer",
    viewer: "layout",
    environment: "INDUSTRIAL_HARNESS_KLAYOUT_PYTHON",
    pythonVersion: assets.pythonVersion,
    modules: ["klayout.db", "klayout.lay"],
    probe,
    platforms,
  };
}

async function verifyUpstream(assets, fetcher = fetch) {
  const json = async url => {
    const response = await fetcher(url, {
      headers: { "User-Agent": "industrial-domain-packs-fixed-asset-recheck" },
      signal: AbortSignal.timeout(60000),
    });
    if (!response.ok) throw Error(`Metadata HTTP ${response.status}: ${url}`);
    return response.json();
  };
  const [release, pypi] = await Promise.all([
    json(assets.sources.python.releaseApi),
    json(assets.sources.klayout.metadataUrl),
  ]);
  assert.equal(release.tag_name, assets.pythonBuild);
  assert.equal(pypi.info.version, assets.klayoutVersion);
  assert.equal(pypi.info.license, assets.sources.klayout.license.spdx);
  for (const entry of Object.values(assets.platforms)) {
    const python = release.assets.find(asset => asset.name === entry.interpreter.name);
    assert.ok(python, "Official Python release asset exists");
    assert.equal(python.browser_download_url, entry.interpreter.url);
    assert.equal(python.digest, "sha256:" + entry.interpreter.sha256);
    assert.equal(python.size, entry.interpreter.size);
    const wheel = pypi.urls.find(asset => asset.filename === entry.wheel.filename);
    assert.ok(wheel && !wheel.yanked, "Official KLayout wheel exists and is not yanked");
    assert.equal(wheel.url, entry.wheel.url);
    assert.equal(wheel.digests.sha256, entry.wheel.sha256);
    assert.equal(wheel.size, entry.wheel.size);
  }
}

async function main() {
  const assets = JSON.parse(fs.readFileSync(path.join(directory, "assets.json"), "utf8"));
  const probe = fs.readFileSync(path.join(directory, "probe.py"), "utf8");
  const environment = buildEnvironment(assets, probe);
  const manifest = JSON.parse(fs.readFileSync(manifestFile, "utf8"));
  if (process.argv.includes("--verify-upstream")) {
    await verifyUpstream(assets);
    console.log("Fixed official Python and KLayout metadata match.");
  }
  if (process.argv.includes("--check")) {
    assert.deepEqual(manifest.pythonEnvironments, [environment], "Regenerate the viewer environment declaration");
  } else {
    manifest.pythonEnvironments = [environment];
    fs.writeFileSync(manifestFile, JSON.stringify(manifest, null, 2) + "\n");
  }
}

module.exports = { buildEnvironment, verifyUpstream };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
