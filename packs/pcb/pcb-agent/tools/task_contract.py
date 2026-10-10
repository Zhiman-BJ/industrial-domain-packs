"""Immutable, public task requirements shared by interactive and final checks.

The launcher mounts the document outside the writable project and pins its
bytes. spec.json is model design intent; it cannot redefine this document.
This module contains no task names, reference circuits or hidden test answers.
"""
import copy
import hashlib
import json
import os
from pathlib import Path

PUBLIC_FIELDS = frozenset({
    'scope', 'requirements', 'name', 'required_files', 'required_checks',
    'custom_rules', 'preserve_items', 'require_3d', 'delivery_files',
    'verification_policy', 'require_supported_claims', 'brief_coverage',
})


def is_brief(document):
    """Uncompiled engineering briefs can be exercised without inventing scores."""
    return isinstance(document, dict) and document.get('schema_version') == 2


def public_document(contract):
    if not isinstance(contract, dict) or 'spec' in contract or 'requirements' not in contract:
        raise ValueError('Public tasks require declarative requirements; migrate exact reference specs before launch')
    return {'schema_version': 1, **{key: copy.deepcopy(value) for key, value in contract.items() if key in PUBLIC_FIELDS}}


def validate(document):
    from tools import requirements, analysis
    if is_brief(document):
        allowed = {'schema_version', 'scope', 'requirements', 'brief', 'delivery', 'evaluation_mode'}
        if (set(document) != allowed or document['scope'] != 'engineering' or
                document['evaluation_mode'] != 'diagnostic' or document['requirements'] != {}):
            raise ValueError('Uncompiled briefs require diagnostic engineering scope and no inferred predicates')
        brief = document['brief']
        if (not isinstance(brief, dict) or set(brief) != {'text', 'sha256'} or
                not isinstance(brief['text'], str) or not brief['text'].strip() or
                hashlib.sha256(brief['text'].encode()).hexdigest() != brief['sha256']):
            raise ValueError('Engineering brief text/hash mismatch')
        delivery = document['delivery']
        if (not isinstance(delivery, dict) or set(delivery) != {'directory', 'project_name'} or
                delivery['directory'] != 'final_project' or
                not isinstance(delivery['project_name'], str) or
                not __import__('re').fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', delivery['project_name'])):
            raise ValueError('Invalid brief delivery target')
        return document
    if (not isinstance(document, dict) or document.get('schema_version') != 1 or
            set(document) - (PUBLIC_FIELDS | {'schema_version'})):
        raise ValueError('Unsupported public requirement document')
    if document.get('scope') not in ('cad_prototype', 'engineering', 'electrical'):
        raise ValueError('Public requirements must declare the acceptance scope')
    req = document.get('requirements')
    checked = requirements.validate(req)
    if checked['status'] != 'PASS':
        raise ValueError('Invalid public requirements: ' + '; '.join(checked['issues']))
    if not req.get('board') or not req.get('board_rules'):
        raise ValueError('Public board envelope and geometry rules are required')
    if 'analysis' in req:
        analysis.validate_plan(req['analysis'])
    if 'brief_coverage' in document:
        from tools import task_obligations
        task_obligations.validate(document['brief_coverage'])
    if 'verification_policy' in document:
        from tools import verification_policy
        checked=verification_policy.validate(document['verification_policy'])
        if checked['status']!='PASS':raise ValueError('Invalid verification policy: '+'; '.join(checked['issues']))
        policy=document['verification_policy']['analysis'];plan=req.get('analysis',{})
        conflict=set(policy['not_applicable'])&(set(plan.get('required',[]))|{t['category'] for t in plan.get('tests',[])})
        conflict|=set(policy['required'])&set(plan.get('not_applicable',{}))
        if conflict:raise ValueError('Analysis applicability conflicts with public tests: '+', '.join(sorted(conflict)))
    return document


def load():
    path, expected = os.environ.get('PCB_PUBLIC_REQUIREMENTS'), os.environ.get('PCB_REQUIREMENTS_SHA256')
    if not path and not expected and os.environ.get('PCB_REQUIREMENTS_REQUIRED') != '1':
        return None
    if not path or not expected:
        raise ValueError('Task requirements binding is missing; no fallback to model intent')
    source = Path(path)
    # The trusted controller supplies this path/digest. Never resolve it from
    # model spec, claims, current directory or tool arguments.
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Immutable task requirements hash mismatch')
    document = validate(json.loads(raw))
    return document


def binding():
    document = load()
    return {} if document is None else {
        'requirements_sha256': os.environ['PCB_REQUIREMENTS_SHA256'],
        'requirements_path': os.environ['PCB_PUBLIC_REQUIREMENTS'],
        'authorized_scope': document['scope'],
    }


def functional_coverage(document):
    """Curated requirement readiness, never a simulation or engineering verdict.

    Circuit-training selection must require fixed numerical function tests.
    CAD-only tasks remain available explicitly as tool regressions. No task IDs,
    expected topologies or candidate-authored tests determine this distinction.
    """
    from tools import validation as v
    if document is None or is_brief(document):
        return v.result('UNKNOWN','functional acceptance coverage',
                        ['No compiled immutable functional contract'],eligible_for_circuit_training=False)
    if 'brief_coverage' in document:
        from tools import task_obligations
        coverage = task_obligations.readiness(document['brief_coverage'])
        if coverage['status'] != 'PASS':
            return dict(coverage, eligible_for_circuit_training=False)
    plan=document.get('requirements',{}).get('analysis',{})
    policy=document.get('verification_policy',{}).get('analysis',{})
    required=set(plan.get('required',[]))|set(policy.get('required',[]))
    issues=[];tests=[]
    if 'function' not in required:issues.append('Function is not an independently required category')
    for test in plan.get('tests',[]):
        if test.get('category')!='function':continue
        ident=test.get('id')
        backend=test.get('backend')
        if not test.get('assertions'):issues.append(str(ident)+': no functional acceptance bounds');continue
        if backend=='spice':
            if not all(test.get(k) for k in ('sources','measures','command')):
                issues.append(str(ident)+': stimulus, measurements or analysis command missing');continue
        elif backend=='external':
            if test.get('evidence_kind')!='simulation' or not test.get('engine'):
                issues.append(str(ident)+': explicitly declared numerical simulation backend required');continue
        else:
            issues.append(str(ident)+': geometry cannot establish circuit function');continue
        tests.append(ident)
    if not tests:issues.append('No executable fixed numerical function tests declared')
    ready=not issues
    return v.result('PASS' if ready else 'UNKNOWN','functional acceptance coverage',issues,
        eligible_for_circuit_training=ready,required_test_ids=tests,
        note='Contract readiness only. Reference/control preflight and candidate-bound simulation PASS are still required; CAD regression PASS is not circuit-function success.')


def observation(document, pointer='', offset=0, limit=10):
    """Bounded access to the pinned public document, never a filesystem path.

    Keep small documents compatible; index large ones instead of silently
    losing requirements to transport truncation. JSON pointers select exact
    predicates and arrays can be paged without copying them into design intent.
    """
    from tools.argument_schema import InputError
    if document is None:
        return None
    def index(value, path):
        if isinstance(value,dict):
            return {'path':path,'type':'object','keys':list(value)}
        if isinstance(value,list):
            return {'path':path,'type':'array','items':len(value)}
        return {'path':path,'type':type(value).__name__,'value':value}
    def child_path(path, key):
        return path+'/'+str(key).replace('~','~0').replace('/','~1')
    def fits(value):
        return len(json.dumps(value,ensure_ascii=False).encode())<=10000
    if not pointer:
        if fits(document):return document
        req=document.get('requirements',{})
        plan=req.get('analysis',{})
        return {'view':'INDEX_ONLY','complete':False,'scope':document['scope'],
            'board':req.get('board'),'board_rules':req.get('board_rules'),
            'requirement_values':{k:v for k,v in req.items() if k not in ('board','board_rules','analysis') and len(json.dumps(v).encode())<=2000},
            'verification_policy':document.get('verification_policy'),
            'sections':[index(value,child_path('',key)) for key,value in document.items()],
            'requirement_sections':[index(value,child_path('/requirements',key)) for key,value in req.items()],
            'analysis':{'required':plan.get('required',[]),'test_count':len(plan.get('tests',[])),
                        'tests_path':'/requirements/analysis/tests'},
            'next_action':'Use project_status(requirements_path=<JSON pointer>) for an exact section; arrays accept offset and limit. run_analysis executes the fixed public tests without copying their plan.'}
    value=document;current=''
    for token in pointer.split('/')[1:] if pointer.startswith('/') else [None]:
        key=token.replace('~1','/').replace('~0','~') if token is not None else None
        try:
            if isinstance(value,dict):value=value[key]
            elif isinstance(value,list) and isinstance(key,str) and key.isdecimal():value=value[int(key)]
            else:raise KeyError(key)
        except (KeyError,IndexError):
            error=InputError('UNKNOWN_REQUIREMENT_PATH','arguments.requirements_path',pointer,
                index(value,current),'Use a JSON pointer from the public section index; no filesystem paths are accepted.')
            error.operation_executed=False
            raise error
        current=child_path(current,key)
    result={'view':'SECTION','path':pointer,'offset':offset}
    if isinstance(value,list):
        selected=[];used=0
        for i,item in enumerate(value[offset:offset+limit],offset):
            entry={'path':child_path(pointer,i),'value':item,'complete':True} if fits(item) else dict(index(item,child_path(pointer,i)),complete=False)
            size=len(json.dumps(entry).encode())
            if selected and used+size>10000:break
            selected.append(entry);used+=size
        next_offset=offset+len(selected) if offset+len(selected)<len(value) else None
        result.update(items=selected,total_items=len(value),next_offset=next_offset,
                      next_call={'tool':'project_status','arguments':{'requirements_path':pointer,'offset':next_offset,'limit':limit}} if next_offset is not None else None,
                      has_more=next_offset is not None,
                      page_complete=all(x['complete'] for x in selected),
                      section_complete=offset==0 and len(selected)==len(value) and all(x['complete'] for x in selected),
                      complete=offset==0 and len(selected)==len(value) and all(x['complete'] for x in selected),
                      completeness_note='has_more refers to later pages; page_complete to unabridged entries on this page; section_complete/complete to the entire section in this response, not cumulative reads.')
    elif fits(value):result.update(value=value,complete=True)
    elif isinstance(value,dict):
        result.update(sections=[index(v,child_path(pointer,k)) for k,v in value.items()],complete=False)
    else:
        next_offset=offset+10000 if offset+10000<len(value) else None
        result.update(text=value[offset:offset+10000],total_characters=len(value),
                      next_offset=next_offset,has_more=next_offset is not None,page_complete=True,
                      section_complete=False,complete=False,
                      next_call={'tool':'project_status','arguments':{'requirements_path':pointer,'offset':next_offset,'limit':limit}} if next_offset is not None else None)
    return result


def effective_spec(intent, document):
    """Apply authoritative constraints without replacing the chosen circuit.

    Tests/waivers/bindings are exactly those in the public contract. Additional
    model-authored analyses remain available as diagnostics in its own intent,
    but cannot substitute for an obligation or exclude a populated component.
    """
    if document is None or is_brief(document):
        return copy.deepcopy(intent)
    req = document['requirements']
    spec = copy.deepcopy(intent)
    spec['constraints'] = {'board_rules': copy.deepcopy(req['board_rules'])}
    spec['postroute'] = copy.deepcopy(req.get('postroute', {}))
    for key in ('analysis', 'power_tree', 'interfaces'):
        if key in req:
            spec[key] = copy.deepcopy(req[key])
        elif key == 'analysis':
            spec.pop(key, None)
        else:
            spec[key] = []
    return spec


def assess(workspace, out):
    from tools import acceptance
    document = load()
    if document is None:
        raise ValueError('No bound public requirements')
    # Claims are submitted only after this fresh check. The final independent
    # scorer adds the identical claim gate after assessing the same document.
    contract = document if is_brief(document) else dict(document, require_supported_claims=False)
    result = acceptance.assess(workspace, contract, out,check_claims=False)
    result.update(binding())
    result['checks'] = result['cad']['checks'] if 'cad' in result else result.get('checks', {})
    return result
