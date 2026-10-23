const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const prefix = `
import importlib.util, json, os, stat, sys, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location('controller', 'packs/pcb/bridge/pcb-controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
policy = {'kicadVersion':'10.0.6', 'kicadPackageVersion':'10.0.6-test-package'}
packages = {name:policy['kicadPackageVersion'] for name in ('kicad','kicad-symbols','kicad-footprints','kicad-packages3d')}
def publish(receipt, version='10.0.6', installed=None, native='10.0.6+fixture'):
    return controller.verified_version_receipt(policy, version, packages if installed is None else installed, native, receipt)
def rejects(callback):
    try: callback()
    except (RuntimeError, ValueError): return
    raise AssertionError('Unverified runtime identity was accepted')
`;
function python(code) {
  return execFileSync('python3', ['-B', '-c', prefix + code], {
    cwd: root,
    encoding: 'utf8',
  }).trim();
}

test('verified controller receipt corrects the unchanged upstream historical fallback', () => {
  assert.equal(python(`
with tempfile.TemporaryDirectory() as directory:
    receipt = Path(directory)/'kicad-version.json'
    sys.path.insert(0, 'packs/pcb/pcb-agent')
    from tools import library
    library.Path = lambda value: receipt if str(value)=='/opt/kicad-version.json' else Path(value)
    assert library.installed_revision() == '9.0.9'
    receipt.write_text(json.dumps({'schema_version':1,'kicad_version':'10.0.6','kicad_package_version':policy['kicadPackageVersion'],'source':'pinned-runtime-policy'}))
    receipt.chmod(0o444)
    value = publish(receipt)
    assert library.installed_revision() == '10.0.6'
    assert value['kicad_version'] == '10.0.6'
    assert value['kicad_package_version'] == policy['kicadPackageVersion']
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o444
    original = receipt.read_bytes()
    assert publish(receipt) == value
    assert receipt.read_bytes() == original
print('verified')
`), 'verified');
});

test('mismatched probes, conflicting receipts and links cannot advertise a native version', () => {
  assert.equal(python(`
with tempfile.TemporaryDirectory() as directory:
    receipt = Path(directory)/'kicad-version.json'
    rejects(lambda: publish(receipt, version='9.0.9'))
    rejects(lambda: publish(receipt, native='9.0.9'))
    rejects(lambda: publish(receipt, installed={}))
    rejects(lambda: publish(receipt, installed={**packages, 'kicad-footprints':'9.0.9'}))
    assert not receipt.exists()
    rejects(lambda: publish(receipt))
    receipt.write_text('{"kicad_version":"9.0.9"}')
    original = receipt.read_bytes()
    rejects(lambda: publish(receipt))
    assert receipt.read_bytes() == original
    receipt.unlink()
    target = Path(directory)/'foreign-receipt.json'
    target.write_text('{"kicad_version":"10.0.6"}')
    receipt.symlink_to(target)
    rejects(lambda: publish(receipt))
    assert target.read_text() == '{"kicad_version":"10.0.6"}'
    receipt.unlink()
    receipt.mkdir()
    rejects(lambda: publish(receipt))
print('rejected')
`), 'rejected');
});
