"""Compare explicit model claims with fresh scoped evidence without rewriting them."""
from tools import validation as v

CAD_CHECKS = ('drc', 'erc', 'netlist', 'parity', 'hierarchy', 'footprints', 'postroute', 'outline', 'revision')


def evidence(report):
    analyses=report.get('analyses') or report.get('checks',{}).get('analyses') or {}
    tests={ident:item for category in analyses.get('categories',{}).values()
           if isinstance(category,dict) for ident,item in category.get('checks',{}).items()}
    common={'test_evidence':tests,
            'narrative_status':'UNKNOWN',
            'narrative_note':'Free-text explanations and remaining_issues are not verified engineering assertions. Only structured statuses and cited test conditions are checked.'}
    qualification=(report.get('verification_layers') or {}).get('electrical_qualification') or {}
    common['electrical_qualification_gaps']={name:{'status':item.get('status','UNKNOWN'),'issues':item.get('issues',[])}
        for name,item in qualification.get('checks',{}).items() if item.get('status')!='PASS'}
    common['status_basis']='Electrical status comes from fresh verifier qualification, independently of test_claims. Citations identify individual passing tests and cannot upgrade aggregate electrical status. Scoped task PASS does not qualify omitted electrical evidence.'
    if 'requirements_sha256' in report:
        return {**common,'cad_status':report.get('cad_status','UNKNOWN'),
                'electrical_status':report.get('electrical_status','UNKNOWN'),
                'task_status':report.get('status','UNKNOWN'),
                'requirements_sha256':report['requirements_sha256'],
                'analysis_statuses':{k:r['status'] for k,r in (report.get('analyses') or {}).get('categories',{}).items()},
                'scope':report.get('scope'), 'reasons':report.get('issues',[])}
    checks = report.get('checks', {})
    cad = {name: checks[name] for name in CAD_CHECKS if name in checks}
    for required in ('drc', 'erc', 'netlist', 'parity', 'footprints'):
        cad.setdefault(required, v.result('UNKNOWN', required, ['Required final check missing']))
    return {
        **common,
        'cad_status': v.aggregate(cad)['status'],
        'electrical_status': report.get('electrical_status', 'UNKNOWN'),
        'analysis_statuses': {name: item['status'] for name, item in checks.get('analyses', {}).get('categories', {}).items()},
        'scope': 'Declared evidence only; electrical_status requires complete engineering coverage and fresh checks',
        'reasons': report.get('reasons', []),
    }


def assess(claims, observed, scope='engineering'):
    if scope not in ('engineering', 'cad_prototype'):
        raise ValueError('Unsupported claims scope')
    issues = []
    if not isinstance(claims, dict):
        return v.result('UNKNOWN', 'model claims', ['No structured claims submitted'])
    for name in ('cad_status', 'electrical_status'):
        if claims.get(name) not in ('PASS', 'FAIL', 'UNKNOWN'):
            issues.append('Invalid ' + name)
        elif claims[name] == 'PASS' and observed.get(name) != 'PASS':
            issues.append(f'{name}: claimed PASS, evidence {observed.get(name, "UNKNOWN")}')
    if type(claims.get('completed')) is not bool:
        issues.append('completed must be a boolean')
    if not isinstance(claims.get('remaining_issues'), list) or any(not isinstance(x, str) for x in claims.get('remaining_issues', [])):
        issues.append('remaining_issues must be a list of strings')
    citations=claims.get('test_claims',[]);verified=[]
    if not isinstance(citations,list):issues.append('test_claims must be a list')
    else:
        for citation in citations:
            if (not isinstance(citation,dict) or set(citation)!={'test_id','conditions_sha256'}
                    or any(not isinstance(x,str) or not x for x in citation.values())):
                issues.append('Each test claim requires test_id and conditions_sha256');continue
            item=observed.get('test_evidence',{}).get(citation['test_id'],{})
            if item.get('status')!='PASS' or item.get('conditions_sha256')!=citation['conditions_sha256']:
                issues.append(citation['test_id']+': no passing evidence for the cited conditions');continue
            verified.append(dict(citation,conditions=item.get('conditions'),values=item.get('values'),
                                 scope=item.get('scope'),board_sha256=item.get('board_sha256')))
    if claims.get('completed') is True:
        if 'task_status' in observed and observed['task_status']!='PASS':
            issues.append('Completion requires the immutable public task requirements to PASS')
        if observed.get('cad_status') != 'PASS':
            issues.append('Completion requires fresh CAD PASS')
        if scope == 'engineering' and observed.get('electrical_status') != 'PASS':
            issues.append('Engineering completion requires fresh full electrical PASS')
        if observed.get('electrical_status') == 'FAIL' or any(x in ('FAIL', 'UNKNOWN') for x in observed.get('analysis_statuses', {}).values()):
            issues.append('Declared failed or unresolved analyses block completion')
    return v.result('FAIL' if issues else 'PASS', 'structured claim consistency; independent original-contract acceptance is still required', issues, evidence=observed,
                    electrical_qualification_gaps=observed.get('electrical_qualification_gaps',{}),
                    status_basis=observed.get('status_basis'),
                    verified_test_claims=verified,narrative_status='UNKNOWN',
                    narrative_note='Free-text claims, formulas, tolerance and compliance statements are not certified by this gate.')
