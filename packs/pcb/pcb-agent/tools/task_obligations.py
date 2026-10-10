"""Source-complete, externally curated requirement-to-evidence mapping.

This validates the mapping and executes its checks; it does not infer engineering
meaning from prose. The mapping belongs to the immutable public task contract.
"""
import hashlib
import re
from tools import validation as v


def uncompiled(text):
    """Surface each source clause when no reviewed mapping exists; infer no checks."""
    items=[];offset=0
    for line in text.splitlines(keepends=True):
        content=line.rstrip('\r\n')
        if content.strip():
            items.append(dict(id='source_'+str(len(items)+1).zfill(3),start=offset,end=offset+len(content),
                              kind='unresolved',checks=[],reason='No independently reviewed executable check bound to this source clause'))
        offset+=len(line)
    return dict(text=text,sha256=hashlib.sha256(text.encode()).hexdigest(),items=items)


def validate(coverage):
    if not isinstance(coverage, dict) or set(coverage) != {'text', 'sha256', 'items'}:
        raise ValueError('brief_coverage requires text, sha256 and items')
    text = coverage['text']
    if (not isinstance(text, str) or not text.strip() or
            hashlib.sha256(text.encode()).hexdigest() != coverage['sha256']):
        raise ValueError('Brief source text/hash mismatch')
    if not isinstance(coverage['items'], list) or not coverage['items']:
        raise ValueError('Nonempty requirement coverage items required')
    covered = bytearray(len(text)); ids = set()
    for item in coverage['items']:
        if not isinstance(item, dict) or set(item) != {'id', 'start', 'end', 'kind', 'checks', 'reason'}:
            raise ValueError('Coverage item requires id, start, end, kind, checks and reason')
        ident = item['id']
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', ident) or ident in ids:
            raise ValueError('Distinct safe requirement IDs required')
        ids.add(ident)
        a, b = item['start'], item['end']
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text) or not text[a:b].strip():
            raise ValueError(ident + ': invalid source character span')
        if any(covered[a:b]):raise ValueError(ident + ': overlapping source spans')
        covered[a:b] = b'\1' * (b-a)
        if item['kind'] not in ('requirement', 'context', 'unresolved'):
            raise ValueError(ident + ': unsupported coverage kind')
        if not isinstance(item['reason'], str) or not item['reason'].strip():
            raise ValueError(ident + ': mapping or context rationale required')
        checks = item['checks']
        if (not isinstance(checks, list) or
                any(not isinstance(p, str) or not p.startswith('/') or p == '/' for p in checks) or len(checks) != len(set(checks))):
            raise ValueError(ident + ': checks must be distinct absolute JSON pointers')
        if bool(checks) != (item['kind'] == 'requirement'):
            raise ValueError(ident + ': executable requirements need checks; context/unresolved cannot have checks')
    if any(not c.isspace() and not covered[i] for i,c in enumerate(text)):
        raise ValueError('Brief contains unaccounted source text')
    if not any(x['kind'] != 'context' for x in coverage['items']):
        raise ValueError('An entirely contextual brief cannot qualify a task')
    return coverage


def readiness(coverage):
    validate(coverage)
    unresolved = [x['id'] for x in coverage['items'] if x['kind'] == 'unresolved']
    return v.result('UNKNOWN' if unresolved else 'PASS', 'requirement mapping readiness',
                    ['Uncompiled requirement: ' + key for key in unresolved],
                    uncompiled=unresolved, source_sha256=coverage['sha256'],
                    note='Mapping completeness only; semantic review and positive/fault qualification are separate')


def evaluate(coverage, evidence, stage='final'):
    validate(coverage)
    if stage not in ('final','pre_export'):raise ValueError('Unsupported requirement evaluation stage')
    items = {}; counts = {'PASS': 0, 'FAIL': 0, 'UNKNOWN': 0, 'NOT_APPLICABLE': 0}
    for item in coverage['items']:
        if item['kind'] == 'context':continue
        checks = {}
        for pointer in item['checks']:
            node = evidence
            try:
                for token in pointer.split('/')[1:]:
                    token = token.replace('~1', '/').replace('~0', '~')
                    node = node[int(token)] if isinstance(node, list) else node[token]
                if (stage=='pre_export' and pointer=='/cad/checks/delivery' and isinstance(node,dict)
                        and node.get('status')=='NOT_APPLICABLE' and node.get('deferred') is True):
                    checks[pointer]=v.result('NOT_APPLICABLE','delivery deferred until export',[],deferred=True)
                    continue
                if not isinstance(node, dict) or node.get('status') not in ('PASS', 'FAIL', 'UNKNOWN'):
                    raise ValueError('Not an executed verifier result')
                checks[pointer] = v.result(node['status'], 'mapped requirement evidence', node.get('issues', []))
            except (KeyError, IndexError, TypeError, ValueError):
                checks[pointer] = v.result('UNKNOWN', 'mapped requirement evidence', ['Missing or inapplicable check: ' + pointer])
        result = v.aggregate(checks) if checks else v.result('UNKNOWN', 'uncompiled requirement', [item['reason']])
        if checks and all(x.get('deferred') for x in checks.values()):
            result=v.result('NOT_APPLICABLE','delivery deferred until export',[],deferred=True)
        counts[result['status']] += 1
        result.update(requirement_id=item['id'], source_text=coverage['text'][item['start']:item['end']],
                      source_span=[item['start'], item['end']], evidence_paths=item['checks'])
        items[item['id']] = result
    result = v.aggregate(items)
    result.update(counts=counts, total_requirements=len(items), source_sha256=coverage['sha256'],
                  scope='All mapped requirements must pass; one failed or uncompiled clause blocks task completion')
    return result
