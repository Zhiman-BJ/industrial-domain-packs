const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { updateHarnessPin } = require('../scripts/update-harness-pin.cjs');
const name = '@zhiman-bj/industrial-domain-packs';
const url = sha => `https://codeload.github.com/Zhiman-BJ/industrial-domain-packs/tar.gz/${sha.repeat(40)}`;
const options = { oldUrl: url('a'), newUrl: url('b'), version: '0.6.0' };
const manifests = ['package.json', 'apps/cli/package.json', 'apps/desktop/package.json'];
const peer = 'packages/domain-skills/package.json';
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'harness-pin-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const file of [...manifests, peer]) {
    fs.mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    fs.writeFileSync(path.join(root, file), JSON.stringify({
      name: file, private: true,
      ...(file === peer ? { peerDependencies: { [name]: '0.5.5' } } : { dependencies: { [name]: options.oldUrl, other: '1.2.3' } }),
    }));
  }
  return root;
}
const read = (root, file) => JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
const snapshot = root => [...manifests, peer].map(file => fs.readFileSync(path.join(root, file), 'utf8'));
test('repin aligns both adapters, the root and the exact peer version', t => {
  const root = fixture(t);
  updateHarnessPin(root, options);
  for (const file of manifests) assert.deepEqual(read(root, file), {
    name: file, private: true, dependencies: { [name]: options.newUrl, other: '1.2.3' },
  });
  assert.equal(read(root, peer).peerDependencies[name], '0.6.0');
});
test('an inconsistent adapter aborts before any manifest changes', t => {
  const root = fixture(t);
  const file = manifests[2];
  const pkg = read(root, file);
  pkg.dependencies[name] = url('c');
  fs.writeFileSync(path.join(root, file), JSON.stringify(pkg));
  const before = snapshot(root);
  assert.throws(() => updateHarnessPin(root, options), /Unexpected pin in apps\/desktop/);
  assert.deepEqual(snapshot(root), before);
});
test('mutable URLs and non-exact versions cannot enter a consumer pin', t => {
  const root = fixture(t);
  const before = snapshot(root);
  for (const invalid of [{ newUrl: options.newUrl.replace(/b{40}$/, 'main') }, { version: '^0.6.0' }, { version: undefined }]) {
    assert.throws(() => updateHarnessPin(root, { ...options, ...invalid }), /immutable|exact/);
  }
  assert.deepEqual(snapshot(root), before);
});
test('an unexpected peer constraint aborts before direct pins change', t => {
  const root = fixture(t);
  const pkg = read(root, peer);
  pkg.peerDependencies[name] = '*';
  fs.writeFileSync(path.join(root, peer), JSON.stringify(pkg));
  const before = snapshot(root);
  assert.throws(() => updateHarnessPin(root, options), /exact Domain Packs peer/);
  assert.deepEqual(snapshot(root), before);
});
