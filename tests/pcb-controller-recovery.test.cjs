const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

test('restarted controller cannot serve actions before identity and interrupted-outcome reconciliation', () => {
  const output = execFileSync('python3', ['-B', '-c', `
import contextlib, importlib.util, json, os, sys, tempfile, types
from pathlib import Path
spec = importlib.util.spec_from_file_location('controller', 'packs/pcb/bridge/pcb-controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
cwd = Path.cwd()
for mode in ('recovered', 'unresolved', 'different-runtime'):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        policy = dict(sourceFiles={}, kicadVersion='10.0.6', kicadPackageVersion='test-package',
                      toolSchemaSha256='schema', sourceSha256='source', imageId='image')
        policy_file = root/'policy.json'
        policy_file.write_text(json.dumps(policy))
        calls = []
        leased = [False]
        @contextlib.contextmanager
        def lease(candidate, name):
            assert candidate == root and name == 'harness-controller'
            leased[0] = True
            try: yield 1
            finally: leased[0] = False
        def durable_write(file, value): file.write_text(json.dumps(value))
        def reconcile(candidate):
            assert candidate == root and leased[0]
            assert (root/'session/industrial-harness-pcb.json').is_file()
            calls.append('reconcile')
            if mode == 'unresolved': raise RuntimeError('Native candidate unreadable; pending retained')
            return dict(status='RECOVERED_FOR_LOCAL_REPAIR')
        tools = types.ModuleType('tools')
        tools.agent_session = types.SimpleNamespace(TOOLSET_SHA256='schema')
        tools.session_isolation = types.SimpleNamespace(prepare=lambda *a, **kw: None)
        tools.session_recovery = types.SimpleNamespace(lease=lease, durable_write=durable_write, reconcile=reconcile)
        tools.kimi_mcp = types.SimpleNamespace(main=lambda: calls.append('serve'))
        tools.task_contract = types.SimpleNamespace(binding=lambda: None)
        sys.modules['tools'] = tools
        sys.modules['pcbnew'] = types.SimpleNamespace(Version=lambda: '10.0.6')
        controller.Path = lambda value: root if str(value) == '/workspace' else Path(value)
        controller.sys.platform = 'linux'
        controller.os.geteuid = lambda: 0
        controller.subprocess.check_output = lambda args, **kw: '10.0.6' if args[0] == 'kicad-cli' else 'test-package'
        controller.verified_version_receipt = lambda *a: None
        controller.fcntl.flock = lambda *a: None
        controller.sys.argv = ['controller', str(policy_file)]
        if mode == 'different-runtime':
            (root/'session').mkdir()
            (root/'session/industrial-harness-pcb.json').write_text('{}')
        try:
            controller.main()
            assert mode == 'recovered'
        except RuntimeError:
            assert mode != 'recovered'
        finally:
            os.chdir(cwd)
        assert calls == ({'recovered':['reconcile','serve'], 'unresolved':['reconcile'], 'different-runtime':[]}[mode])
print('recovery-gated')
`], { cwd: path.resolve(__dirname, '..'), encoding: 'utf8' }).trim();
  assert.equal(output, 'recovery-gated');
});
