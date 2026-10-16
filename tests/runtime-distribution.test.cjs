const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.join(__dirname, '..');
const lockPath = path.join(root, 'provenance', 'runtime-distribution.json');

test('release artifact verifier rejects invalid pins, corrupted bytes and manifest substitution', () => {
  const result = spawnSync('python3', [path.join(__dirname, 'verify-release-artifact.test.py')], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
});

test('distribution lock keeps a valid transport-agnostic schema', () => {
  const lock = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
  assert.equal(lock.schemaVersion, 1);
  assert.equal(lock.registry, 'ghcr.io');
  assert.match(lock.imageNamespace, /^[a-z0-9-]+$/);
  assert.ok(lock.releaseAssetUrlTemplate.includes('{tag}') && lock.releaseAssetUrlTemplate.includes('{archive}'));
  assert.ok(lock.registryManifestUrlTemplate.includes('{digest}'));
  const ids = new Set();
  for (const image of lock.images) {
    assert.match(image.id, /^industrial-[a-z0-9-]+$/);
    assert.ok(!ids.has(image.id));
    ids.add(image.id);
    assert.match(image.repository, /^[a-z0-9/_-]+$/);
    assert.ok(fs.existsSync(path.join(root, image.recipe)), `missing recipe ${image.recipe}`);
    assert.ok(fs.existsSync(path.join(root, image.context)), `missing context ${image.context}`);
    assert.equal(image.platform, 'linux/amd64');
    assert.match(image.tagPattern, /^(pcb|chip)-v\*$/);
    if (image.current) {
      assert.match(image.current.tag, /^(pcb|chip)-v[0-9]+\.[0-9]+\.[0-9]+/);
      assert.match(image.current.imageDigest, /^sha256:[0-9a-f]{64}$/);
      if (image.current.configDigest) assert.match(image.current.configDigest, /^sha256:[0-9a-f]{64}$/);
      assert.match(image.current.ociArchiveSha256, /^[0-9a-f]{64}$/);
      assert.ok(Number.isInteger(image.current.ociArchiveBytes) && image.current.ociArchiveBytes > 0);
      assert.match(image.current.releasedAt, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
      const assetUrl = lock.releaseAssetUrlTemplate
        .replace('{tag}', image.current.tag)
        .replace('{archive}', image.current.ociArchive);
      const manifestUrl = lock.registryManifestUrlTemplate
        .replace('{namespace}', lock.imageNamespace)
        .replace('{repository}', image.repository)
        .replace('{digest}', image.current.imageDigest);
      assert.ok(assetUrl.startsWith('https://'));
      assert.ok(manifestUrl.startsWith('https://ghcr.io/v2/'));
    }
  }
  for (const required of ['industrial-pcb-runtime', 'industrial-chip-tools', 'industrial-chip-rtl'])
    assert.ok(ids.has(required), `lock is missing ${required}`);
});

test('update-runtime-distribution records identities and rejects malformed ones', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'industrial-distribution-'));
  const staged = path.join(directory, 'runtime-distribution.json');
  // Stage the repo lock but reset release state: the untouched-entry assertion
  // below must not depend on whether the repository has already shipped a release.
  const fixture = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
  for (const image of fixture.images) image.current = null;
  fs.writeFileSync(staged, JSON.stringify(fixture, null, 2) + '\n');
  const update = path.join(root, 'scripts', 'update-runtime-distribution.py');
  const run = args => spawnSync('python3', [update, '--lock', staged, ...args], { encoding: 'utf8' });

  const digest = 'sha256:' + 'a'.repeat(64);
  const recorded = run([
    '--id', 'industrial-pcb-runtime',
    '--tag', 'pcb-v1.0.0',
    '--digest', digest,
    '--config-digest', 'sha256:' + 'b'.repeat(64),
    '--archive', 'industrial-pcb-runtime-pcb-v1.0.0.tar',
    '--archive-sha256', 'c'.repeat(64),
    '--archive-bytes', '123',
    '--commit', '0123456789abcdef',
  ]);
  assert.equal(recorded.status, 0, recorded.stderr);
  const lock = JSON.parse(fs.readFileSync(staged, 'utf8'));
  const entry = lock.images.find(image => image.id === 'industrial-pcb-runtime');
  assert.equal(entry.current.tag, 'pcb-v1.0.0');
  assert.equal(entry.current.imageDigest, digest);
  assert.equal(entry.current.configDigest, 'sha256:' + 'b'.repeat(64));
  assert.equal(entry.current.ociArchiveBytes, 123);
  assert.equal(entry.current.sourceCommit, '0123456789abcdef');
  // Other entries stay untouched.
  assert.equal(lock.images.find(image => image.id === 'industrial-chip-tools').current, null);

  assert.notEqual(run(['--id', 'industrial-pcb-runtime', '--tag', 'v9', '--digest', digest,
    '--archive', 'x.tar', '--archive-sha256', 'c'.repeat(64), '--archive-bytes', '5']).status, 0);
  assert.notEqual(run(['--id', 'industrial-pcb-runtime', '--tag', 'pcb-v1.0.0', '--digest', 'sha256:short',
    '--archive', 'x.tar', '--archive-sha256', 'c'.repeat(64), '--archive-bytes', '5']).status, 0);
  assert.notEqual(run(['--id', 'unknown-image', '--tag', 'pcb-v1.0.0', '--digest', digest,
    '--archive', 'x.tar', '--archive-sha256', 'c'.repeat(64), '--archive-bytes', '5']).status, 0);
  fs.rmSync(directory, { recursive: true, force: true });
});
