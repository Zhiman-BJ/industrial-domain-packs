"""Revision and test-condition identities shared by solver and acceptance gates."""
import hashlib
import json
import re


def is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def conditions_hash(test):
    # Scheduling/record names do not change physics. Everything else, including
    # temperature, SPICE sources, terminal mapping and measurements, does.
    payload={k:v for k,v in test.items() if k not in ('id','timeout_s','bind_to_revision','require_evidence')}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def binding_issues(item, expected, require_evidence=True, require_board=True):
    issues=[]
    fields=('board_sha256','schematic_sha256','spec_sha256','conditions_sha256') if require_board else ('schematic_sha256','spec_sha256','conditions_sha256')
    for key in fields:
        if not is_sha256(expected.get(key)) or item.get(key)!=expected[key]:
            issues.append('Missing or mismatched '+key)
    if require_evidence:
        hashes=item.get('evidence_sha256')
        if not isinstance(hashes,dict) or not hashes or any(not isinstance(k,str) or not k or not is_sha256(h) for k,h in hashes.items()):
            issues.append('Retained evidence_sha256 paths and SHA-256 digests required')
    return issues
