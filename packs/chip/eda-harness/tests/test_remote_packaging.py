"""The remote recipe must import and prepare RTL using only its copied files."""
import json
from pathlib import Path
import subprocess
import sys


def test_remote_context_is_self_contained_without_pack_content_lock(tmp_path):
    root = Path(__file__).resolve().parents[4]
    context = tmp_path / 'context'
    subprocess.run(['node', str(root / 'scripts/runtime-build.cjs'),
                    'packs/chip/images/Dockerfile.rtl-cpu', 'linux/amd64', str(context)],
                   cwd=root, check=True, capture_output=True)
    assert not (context / 'content-lock.json').exists()
    assert not (context / 'package.json').exists()
    script = '''
import json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
module = runpy.run_path(str(root / 'packs/chip/runtime/native-profile.py'))
(root / 'work').mkdir()
model, snapshot = module['project'](root / 'packs/chip/examples/rtl')
prepared = module['semantic'].prepare('rtl.simulate', model, snapshot,
    {'input': str(root / 'packs/chip/examples/rtl'), 'deps': str(root / 'deps'), 'work': str(root / 'work')}, root / 'work')
print(json.dumps({'commands': prepared.commands, 'outputs': prepared.outputs}))
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(context)],
                            cwd=tmp_path, check=True, capture_output=True, text=True)
    prepared = json.loads(result.stdout)
    assert prepared['commands']
    assert 'wave.vcd' in prepared['outputs'].values()
