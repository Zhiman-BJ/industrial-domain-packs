const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { inspect, observations, hash } = require('../../scripts/check-architecture-contract.cjs');

function fixture(t, role = 'harness') {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'architecture-contract-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const write = (file, text) => {
    const target = path.join(root, file);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, text);
  };
  write('package.json', JSON.stringify({ dependencies: {} }));
  const policy = {
    revision: 'IH-ARCH-001',
    role,
    canonical: 'industrial-agent-harness',
    frozen: {},
    frozenPrefixes: [],
    domainAtoms: {},
    adapterCalls: {},
    governanceHashes: {},
  };
  const check = (current = policy) => inspect(root, current, policy);
  return { root, write, policy, check };
}

test('both application adapters reject cross-imports and industrial process bypasses', t => {
  for (const [file, source] of [
    ['apps/cli/src/new.cjs', "require('../../desktop/electron/main.cjs');"],
    ['apps/desktop/electron/new.cjs', "import('../../cli/src/main.cjs');"],
    ['apps/cli/src/new.cjs', "require('@industrial-agent-harness/viewer-builtin');"],
    ['apps/desktop/electron/new.cjs', "spawn('freecadcmd', []);"],
  ]) {
    const f = fixture(t);
    f.write(file, source);
    assert.equal(f.check().ok, false, source);
  }
});

test('a new domain ID or agent dependency cannot enter generic core', t => {
  for (const source of [
    "const domain = 'cad';",
    "const tool = 'godot.scene.export';",
    "require('@industrial-agent-harness/agent-kimi');",
  ]) {
    const f = fixture(t);
    f.write('packages/harness-core/src/new.cjs', source);
    assert.equal(f.check().ok, false, source);
  }
});

test('manifest-only adapter and peer dependency violations are rejected before import', t => {
  for (const [file, item] of [
    ['apps/cli/package.json', { dependencies: { electron: '1.0.0' } }],
    [
      'apps/desktop/package.json',
      { dependencies: { '@industrial-agent-harness/cli': 'workspace:*' } },
    ],
    [
      'packages/harness-core/package.json',
      { peerDependencies: { '@moonshot-ai/kimi-code': '2.1.1' } },
    ],
  ]) {
    const f = fixture(t);
    f.write(file, JSON.stringify(item));
    assert.equal(f.check().ok, false, file);
  }
});

test('satellites cannot publish a competing canonical package', t => {
  const f = fixture(t, 'domain-packs');
  f.write(
    'packages/facts/package.json',
    JSON.stringify({ name: '@industrial-agent-harness/contracts' }),
  );
  assert.equal(f.check().ok, false);
});

test('legacy orchestration may shrink but cannot grow or move into another adapter', t => {
  const f = fixture(t);
  f.write('apps/cli/src/main.cjs', 'resolveProjectTask();');
  f.policy.adapterCalls = observations(f.root).calls;
  assert.equal(f.check().ok, true);
  f.write('apps/cli/src/main.cjs', 'resolveProjectTask(); resolveProjectTask();');
  assert.equal(f.check().ok, false);
  f.write('apps/cli/src/main.cjs', '');
  f.write('apps/desktop/electron/new.cjs', 'resolveProjectTask();');
  assert.equal(f.check().ok, false);
  fs.unlinkSync(path.join(f.root, 'apps/desktop/electron/new.cjs'));
  assert.equal(f.check({ ...f.policy, adapterCalls: {} }).ok, true);
});

test('frozen domain implementations cannot be edited, extended or rebaselined', t => {
  const f = fixture(t);
  const file = 'domain-packs/chip/runtime/index.cjs';
  f.write(file, 'old();');
  f.policy.frozenPrefixes = ['domain-packs'];
  f.policy.frozen[file] = hash('old();');
  assert.equal(f.check().ok, true);
  f.write(file, 'changed();');
  assert.equal(f.check({ ...f.policy, frozen: { [file]: hash('changed();') } }).ok, false);
  f.write(file, 'old();');
  f.write('domain-packs/another/index.cjs', 'newLogic();');
  assert.equal(f.check().ok, false);
  fs.unlinkSync(path.join(f.root, 'domain-packs/another/index.cjs'));
  fs.unlinkSync(path.join(f.root, file));
  assert.equal(f.check({ ...f.policy, frozen: {} }).ok, true);
});

test('a feature cannot widen its own exceptions or replace the trusted checker', t => {
  const f = fixture(t);
  f.write('scripts/check-architecture-contract.cjs', 'trusted checker');
  f.policy.governanceHashes['scripts/check-architecture-contract.cjs'] = hash('trusted checker');
  assert.equal(f.check().ok, true);
  assert.equal(
    f.check({ ...f.policy, domainAtoms: { 'packages/harness-core/src/new.cjs#cad': 1 } }).ok,
    false,
  );
  assert.equal(f.check({ ...f.policy, role: 'domain-packs' }).ok, false);
  f.write('scripts/check-architecture-contract.cjs', 'weakened checker');
  assert.equal(f.check().ok, false);
});

test('domain source owns tools but cannot grow an application or canonical core', t => {
  const f = fixture(t, 'domain-packs');
  f.write('packs/new/runtime/index.cjs', "const toolId = 'cad.new.tool';");
  assert.equal(f.check().ok, true);
  f.write('packs/new/runtime/index.cjs', "require('@industrial-agent-harness/agent-kimi');");
  assert.equal(f.check().ok, false);
  f.write('packs/new/runtime/index.cjs', '');
  f.write('lib/contracts.cjs', 'const Action = {};');
  assert.equal(f.check().ok, false);
});

function remoteFixture(t) {
  const f = fixture(t, 'remote-service');
  const name = '@zhiman-bj/industrial-domain-packs';
  const url = `https://codeload.github.com/Zhiman-BJ/industrial-domain-packs/tar.gz/${'a'.repeat(40)}`;
  f.write('package.json', JSON.stringify({ dependencies: { [name]: url } }));
  f.write(
    'package-lock.json',
    JSON.stringify({
      packages: {
        '': { dependencies: { [name]: url } },
        [`node_modules/${name}`]: { resolved: url, integrity: 'sha512-fixed-test' },
      },
    }),
  );
  return { ...f, name, url };
}

test('remote consumers require an exact Pack commit and matching locked integrity', t => {
  const f = remoteFixture(t);
  assert.equal(f.check().ok, true);
  f.write(
    'package.json',
    JSON.stringify({ dependencies: { [f.name]: f.url.replace(/a{40}$/, 'main') } }),
  );
  assert.equal(f.check().ok, false);
  f.write('package.json', JSON.stringify({ dependencies: { [f.name]: f.url } }));
  f.write('package-lock.json', JSON.stringify({ packages: {} }));
  assert.equal(f.check().ok, false);
});

test('remote domain verification and edits to canonical vendor snapshots are rejected', t => {
  const f = remoteFixture(t);
  const file = 'vendor/contracts/src/index.cjs';
  f.write(file, 'canonical upstream bytes');
  f.policy.frozen[file] = hash('canonical upstream bytes');
  assert.equal(f.check().ok, true);
  f.write(file, 'new competing facts');
  assert.equal(f.check().ok, false);
  f.write(file, 'canonical upstream bytes');
  f.write('packages/backend/verifier.cjs', 'module.exports = () => true;');
  assert.equal(f.check().ok, false);
});

test('source and policy paths cannot escape through symlinks or traversal', t => {
  const f = fixture(t);
  f.policy.frozen['../outside'] = hash('x');
  assert.throws(() => f.check(), /Unsafe policy path/);
  delete f.policy.frozen['../outside'];
  f.write('packages/core/src/file.cjs', '');
  fs.symlinkSync(
    path.join(f.root, 'package.json'),
    path.join(f.root, 'packages/core/src/link.cjs'),
  );
  assert.throws(() => f.check(), /symlink/);
});
