const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { buildEnvironment } = require("../packs/chip/scripts/update-viewer-environment.cjs");
const assets = require("../packs/chip/resources/klayout-viewer/assets.json");
const manifest = require("../packs/chip/harness-pack.json");
const source = fs.readFileSync(path.join(__dirname, "../packs/chip/resources/klayout-viewer/probe.py"), "utf8");

test("Chip declares one fixed private viewer environment and the exact readable PNG probe", () => {
  assert.deepEqual(manifest.pythonEnvironments, [buildEnvironment(assets, source)]);
  const entry = manifest.pythonEnvironments[0];
  assert.equal(entry.id, "klayout-viewer");
  assert.equal(entry.purpose, "viewer");
  assert.equal(entry.viewer, "layout");
  assert.equal(entry.version, "0.30.12");
  assert.equal(entry.pythonVersion, "3.12.15");
  assert.equal(entry.environment, "INDUSTRIAL_HARNESS_KLAYOUT_PYTHON");
  assert.deepEqual(entry.modules, ["klayout.db", "klayout.lay"]);
  assert.ok(Buffer.byteLength(entry.probe, "utf8") <= 16384);
  assert.match(entry.probe, /TemporaryDirectory/);
  assert.match(entry.probe, /save_image\(str\(png\), 64, 64\)/);
  assert.match(entry.probe, /header\[12:16\] != b"IHDR"/);
});

test("all four official artifacts pin one Python build and compatible CPython wheel identities", () => {
  const platforms = manifest.pythonEnvironments[0].platforms;
  assert.deepEqual(Object.keys(platforms), ["darwin-arm64", "linux-x64", "linux-arm64", "win32-x64"]);
  for (const [platform, entry] of Object.entries(platforms)) {
    const recorded = assets.platforms[platform];
    const interpreter = entry.interpreter;
    assert.equal(new URL(interpreter.url).hostname, "github.com");
    assert.match(decodeURIComponent(interpreter.url), /\/astral-sh\/python-build-standalone\/releases\/download\/20261013\/cpython-3\.12\.15\+20261013-.+-install_only\.tar\.gz$/);
    assert.match(interpreter.sha256, /^[a-f0-9]{64}$/);
    assert.equal(interpreter.format, "tar.gz");
    assert.equal(interpreter.executable, platform === "win32-x64" ? "python/python.exe" : "python/bin/python3.12");
    assert.equal(recorded.interpreter.pipVersion, "26.2.1");
    assert.equal(entry.wheels.length, 1);
    const wheel = entry.wheels[0];
    assert.equal(wheel.name, "klayout");
    assert.equal(wheel.version, "0.30.12");
    assert.match(wheel.filename, /^klayout-0\.30\.12-cp312-cp312-.+\.whl$/);
    assert.equal(new URL(wheel.url).hostname, "files.pythonhosted.org");
    assert.equal(path.posix.basename(new URL(wheel.url).pathname), wheel.filename);
    assert.match(wheel.sha256, /^[a-f0-9]{64}$/);
    for (const value of [interpreter.size, wheel.size, entry.installedSize]) assert.ok(Number.isSafeInteger(value) && value > 0);
    assert.equal(entry.installedSize, recorded.installedSizeBasis.interpreterPayloadBytes + recorded.installedSizeBasis.wheelPayloadBytes + 16 * 1024 * 1024);
  }
  assert.equal(assets.sources.klayout.license.spdx, "GPL-3.0-or-later");
  assert.equal(assets.sources.python.buildRepositoryLicense.spdx, "MPL-2.0");
  assert.equal(assets.sources.python.pythonLicense.spdx, "Python-2.0");
});

test("viewer declarations preserve Chip's existing execution versions and toolchain requirements", () => {
  const consumer = require("../lib/consumer.json").domains.find(item => item.id === "chip");
  const pack = require("../packs/chip/pack.json");
  assert.equal(manifest.provider.version, "0.6.1");
  assert.equal(pack.sourceVersion, "0.6.1");
  assert.equal(pack.runtime.toolVersion, "0.6.1-core.2");
  assert.equal(pack.releaseVersion, "0.6.3");
  assert.equal(consumer.version, pack.releaseVersion);
  assert.equal(manifest.provider.pythonEnv, "INDUSTRIAL_HARNESS_EDA_PYTHON");
  assert.match(consumer.prerequisites[0], /Python 3\.13, Verilator and a C\+\+ toolchain/);
  assert.match(consumer.prerequisites[1], /does not install the complete EDA environment/);
  assert.equal(pack.profiles.find(profile => profile.id === "rtl-cpu").status, "qualification-pending");
  assert.equal(manifest.runtimeAssets, undefined);
});

const python = process.env.INDUSTRIAL_HARNESS_KLAYOUT_PYTHON;
test("an explicitly prepared private interpreter renders real temporary GDS to PNG and cleans up", { skip: !python }, t => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "chip-viewer-probe-test-"));
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  const result = spawnSync(python, ["-I", "-c", manifest.pythonEnvironments[0].probe], {
    cwd: temporary,
    env: { ...process.env, TMPDIR: temporary, TMP: temporary, TEMP: temporary },
    encoding: "utf8",
    timeout: 30000,
    maxBuffer: 1024 * 1024,
  });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /KLayout viewer PNG probe passed \(0\.30\.12, 64x64\)/);
  assert.deepEqual(fs.readdirSync(temporary), []);
});
