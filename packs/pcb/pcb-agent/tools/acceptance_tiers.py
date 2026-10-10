"""Three scoped acceptance tiers over trusted policy and revision-bound evidence.

This is reporting, not a replacement gate. The caller must supply the external
policy and its effective test plan; candidate-authored plans alone cannot pass.
"""
from tools import validation as v, verification_policy as vp
from tools.evidence_binding import binding_issues, conditions_hash

SIMULATION_BACKENDS={'spice','pcb_dc','pcb_thermal','openems','qualified_device_cosimulation'}
HARDWARE_KINDS={'measurement','physical','protocol'}


def summarize(report, policy, tests, *, board_sha256, schematic_sha256, spec_sha256, stage='final'):
    unknown=lambda message:v.result('UNKNOWN','acceptance tier',[message])
    tiers={name:unknown('No complete trusted acceptance policy') for name in
           ('design_delivery','simulation','hardware_qualification')}
    if policy is None or vp.validate(policy)['status']!='PASS':return tiers
    evaluated=vp.evaluate(report,policy,board_sha256=board_sha256,
        schematic_sha256=schematic_sha256,spec_sha256=spec_sha256,tests=tests,stage=stage)
    groups=evaluated.get('checks',{})
    tiers['design_delivery']=groups.get('cad',unknown('CAD requirements missing'))
    analyses=report.get('checks',{}).get('analyses',{})
    categories=analyses.get('categories',{})
    selected={'simulation':{},'hardware_qualification':{}}
    for category in policy['analysis']['required']:
        if category=='mechanical':continue  # assembly geometry is not field simulation or measurement
        declared=[t for t in tests if t.get('category')==category]
        target='hardware_qualification' if category=='physical' else 'simulation'
        if not declared:
            selected[target][category]=unknown('Required category has no independently declared tests')
        for test in declared:
            kind=test.get('evidence_kind')
            engine=test.get('engine') if test.get('backend')=='external' else test.get('backend')
            if engine in SIMULATION_BACKENDS:kind='simulation'
            elif category=='physical':kind='measurement'
            if kind in HARDWARE_KINDS:target='hardware_qualification'
            elif kind=='simulation':target='simulation'
            else:
                # An opaque capture/adapter cannot establish which tier it covers.
                for tier in selected:selected[tier][test['id']]=unknown('External test must declare its evidence_kind')
                continue
            observed=categories.get(category,{}).get('checks',{}).get(test['id'],{})
            item=observed if observed.get('status') in ('PASS','FAIL','UNKNOWN') else unknown('Required test not executed')
            if item['status']=='PASS':
                problems=binding_issues(item,dict(board_sha256=board_sha256,
                    schematic_sha256=schematic_sha256,spec_sha256=spec_sha256,
                    conditions_sha256=conditions_hash(test)),require_board=True)
                actual=item.get('evidence_kind')
                if target=='simulation' and actual!='simulation':problems.append('Numerical simulation evidence required')
                if target=='hardware_qualification' and actual not in HARDWARE_KINDS:problems.append('Measured hardware evidence required')
                minimum=policy.get('board_evidence',{}).get(category,{}).get('minimum_witnesses',1)
                if len(declared)<minimum:problems.append('Too few independently declared witnesses')
                if category=='dynamic_simulation' and test.get('backend')=='spice' and not str(test.get('command','')).lower().startswith('.tran '):
                    problems.append('Dynamic SPICE requires transient analysis')
                if problems:item=unknown('; '.join(problems))
            selected[target][test['id']]=item
    for tier,checks in selected.items():
        tiers[tier]=v.aggregate(checks) if checks else unknown('No independently required tests for this tier')
        tiers[tier]['scope']='All independently required tests assigned to this tier, for the bound revision and declared conditions only'
    # No intermediate-stage or stale result can establish final acceptance.
    if stage!='final' or 'revision' in groups:
        for name in tiers:tiers[name]=unknown('Final unchanged board revision verification required')
    return tiers
