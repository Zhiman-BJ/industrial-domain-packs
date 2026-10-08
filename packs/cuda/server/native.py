"""Trusted transport adapter; candidate code is never executed by this process.

Runs only inside administrator-allocated CPU compiler/evaluator containers.
The private upstream implementation and its installed helpers remain external.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    data = json.loads(sys.stdin.buffer.read(24 * 1024 * 1024))
    sys.path.insert(0, '/opt/cuda-agent')
    operation = data['operation']
    if operation == 'identity':
        print(json.dumps({filename: hashlib.sha256(Path(filename).read_bytes()).hexdigest()
                          for filename in data['paths']}))
        return
    client = load('candidate_client', '/opt/cuda-agent/candidate_client.py')
    if operation == 'attest':
        result = client.request('/run/cuda-agent/compiler.sock', {
            'command': 'attest', 'agent_uid': 10001, 'agent_gpu_device_count': 0,
        })
    elif operation == 'compile':
        compiler = load('candidate_compiler', '/opt/cuda-agent/candidate_compiler.py')
        with tempfile.TemporaryDirectory(prefix='cuda-mcp-source-') as directory:
            root = Path(directory)
            for name, encoded in data['files'].items():
                if name != 'model_new.py' and not name.startswith('kernels/'):
                    raise ValueError('invalid source path')
                target = root / name
                if '..' in target.relative_to(root).parts or target.is_absolute() and not target.is_relative_to(root):
                    raise ValueError('invalid source path')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(encoded, validate=True))
            archive = client.pack_candidate(root)
            if client.source_manifest_sha256_from_archive(archive) != data['sourceSha256']:
                raise ValueError('source manifest mismatch')
            result = compiler.Compiler()._compile({
                'archive': base64.b64encode(archive).decode('ascii'),
                'source_manifest_sha256': data['sourceSha256'], 'turn_number': data['sequence'],
                'evaluation_mode': 'verification', 'profile_iters': 1000,
            })
    elif operation == 'evaluate':
        result = client.request('/run/cuda-agent-private/evaluator.sock', data['submission'])
    elif operation == 'baseline':
        workspace = Path('/opt/cuda-agent-workspace')
        result = client.request('/run/cuda-agent-private/evaluator.sock', {
            'command': 'profile_native', 'turn_number': data['sequence'],
            'source_manifest_sha256': client.source_manifest_sha256(workspace),
        })
    elif operation == 'baseline_record':
        file = Path('/var/lib/cuda-agent/candidates/native-profile.json')
        result = json.loads(file.read_bytes()) if file.exists() else None
    elif operation == 'record':
        records = Path('/var/lib/cuda-agent/candidates/ledger.jsonl').read_text().splitlines()
        matching = [json.loads(line) for line in records if json.loads(line).get('candidate_id') == data['candidateId']]
        if len(matching) != 1:
            raise ValueError('candidate ledger identity is ambiguous')
        result = matching[0]
    else:
        raise ValueError('unknown native adapter operation')
    print(json.dumps(result, separators=(',', ':'), allow_nan=False))


if __name__ == '__main__':
    main()
