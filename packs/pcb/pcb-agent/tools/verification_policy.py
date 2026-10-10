"""Task-neutral acceptance policy: missing coverage is unresolved, never waived."""
from tools import validation as v
from tools.evidence_binding import binding_issues, conditions_hash

SCHEMA_VERSION=1
CAD_CHECK_PATHS={name:'checks.'+name for name in (
    'erc','drc','netlist','parity','hierarchy','footprints','datasheets',
    'power_tree','interfaces','operating_limits','mechanical','model3d','delivery')}
ANALYSIS_CATEGORIES=('function','stability','dynamic_simulation','emc_precheck',
                     'emc','rf','thermal','si','pi','mechanical','protocol','physical')
BOARD_EVIDENCE=('protocol','dynamic_simulation','rf','thermal','physical')
HASH_FIELDS=('board_sha256','schematic_sha256','spec_sha256','conditions_sha256','evidence_sha256')
SCHEMATIC_CAD={'erc','netlist','hierarchy','footprints','datasheets','power_tree','interfaces','operating_limits'}
SCHEMATIC_ANALYSIS={'function','stability','dynamic_simulation'}


def default_policy(required_analysis=(), not_applicable=None, *, cad_not_applicable=None, require_delivery=True):
    """An incomplete declaration stays incomplete until the curator reviews it.

    No function-to-transient or SI-to-RF equivalence is inferred. Absence of a
    test does not establish non-applicability. Unaccounted classes block use.
    """
    na=dict(not_applicable or {});cna=dict(cad_not_applicable or {})
    if not require_delivery:cna.setdefault('delivery','No delivery bundle required by this external policy')
    return dict(schema_version=1,
        cad=dict(required=[k for k in CAD_CHECK_PATHS if k not in cna],not_applicable=cna),
        analysis=dict(required=list(required_analysis),not_applicable=na),
        board_evidence={k:dict(required=k in required_analysis,binding={h:True for h in HASH_FIELDS},minimum_witnesses=1) for k in BOARD_EVIDENCE},
        claims=dict(required=True))


def validate(policy):
    issues=[]
    if not isinstance(policy,dict) or type(policy.get('schema_version')) is not int or policy.get('schema_version')!=1:
        return v.result('UNKNOWN','verification policy',['Unsupported verification policy schema'])
    if set(policy)!={'schema_version','cad','analysis','board_evidence','claims'}:
        issues.append('Unknown or missing verification policy fields')
    for section,allowed in [('cad',set(CAD_CHECK_PATHS)),('analysis',set(ANALYSIS_CATEGORIES))]:
        item=policy.get(section,{})
        if not isinstance(item,dict) or set(item)!={'required','not_applicable'}:
            issues.append(section+': required and not_applicable declarations needed');continue
        req=item['required'];na=item['not_applicable']
        if not isinstance(req,list) or any(not isinstance(k,str) or k not in allowed for k in req):
            issues.append(section+': invalid required checks');continue
        if len(set(req))!=len(req):issues.append(section+': duplicate required checks')
        if not isinstance(na,dict) or any(k not in allowed or not isinstance(r,str) or not r.strip() for k,r in na.items()):
            issues.append(section+': invalid non-applicability reasons');continue
        if set(req)&set(na):issues.append(section+': required checks cannot be waived')
        missing=allowed-set(req)-set(na)
        if missing:issues.append(section+': unresolved applicability: '+', '.join(sorted(missing)))
    evidence=policy.get('board_evidence')
    if not isinstance(evidence,dict) or set(evidence)!=set(BOARD_EVIDENCE):
        issues.append('All board evidence classes must be listed')
    else:
        for name,item in evidence.items():
            if not isinstance(item,dict) or set(item)!={'required','binding','minimum_witnesses'}:
                issues.append(name+': invalid evidence declaration');continue
            declared=policy.get('analysis',{})
            required=declared.get('required',[]) if isinstance(declared,dict) else []
            if type(item['required']) is not bool or item['required']!=(isinstance(required,list) and name in required):
                issues.append(name+': evidence requirement contradicts analysis requirement')
            binding=item['binding']
            if not isinstance(binding,dict) or set(binding)!=set(HASH_FIELDS) or any(value is not True for value in binding.values()):
                issues.append(name+': all revision and evidence bindings must be enabled')
            if type(item['minimum_witnesses']) is not int or item['minimum_witnesses']<1:
                issues.append(name+': positive minimum_witnesses required')
    claims=policy.get('claims')
    if not isinstance(claims,dict) or set(claims)!={'required'} or type(claims['required']) is not bool:
        issues.append('claims.required must be a boolean')
    return v.result('UNKNOWN' if issues else 'PASS','verification policy schema',issues)


def _required(item,name):
    if not isinstance(item,dict) or item.get('status') not in ('PASS','FAIL','UNKNOWN'):
        return v.result('UNKNOWN',name,['Required check is absent or marked NOT_APPLICABLE'])
    return item


def evaluate(report,policy,*,board_sha256=None,schematic_sha256=None,spec_sha256=None,tests=(),stage='final'):
    schema=validate(policy)
    if schema['status']!='PASS':return schema
    if stage not in ('schematic','pre_export','final'):raise ValueError('Unknown verification stage')
    checks=report.get('checks',{});analyses=checks.get('analyses',{})
    categories=analyses.get('categories',{})
    groups={}
    by_id={t['id']:t for t in tests}
    for section,observed in [('cad',checks),('analysis',categories)]:
        selected={}
        for name in policy[section]['required']:
            if stage=='pre_export' and section=='cad' and name=='delivery':
                selected[name]=v.result('NOT_APPLICABLE',name,['Deferred until files are exported'],deferred=True);continue
            if stage=='schematic' and name not in (SCHEMATIC_CAD if section=='cad' else SCHEMATIC_ANALYSIS):
                selected[name]=v.result('NOT_APPLICABLE',name,['Deferred to final board verification'],deferred=True);continue
            item=observed.get(name)
            selected[name]=_required(item,name)
            if section=='analysis' and selected[name]['status']=='PASS':
                witnesses={}
                for ident,test in by_id.items():
                    if test.get('category')!=name:continue
                    witness=_required(item.get('checks',{}).get(ident),ident)
                    if witness['status']=='PASS':
                        expected=dict(board_sha256=board_sha256,schematic_sha256=schematic_sha256,spec_sha256=spec_sha256,conditions_sha256=conditions_hash(test))
                        issues=binding_issues(witness,expected,require_board=stage!='schematic')
                        if issues:witness=v.result('UNKNOWN',ident,issues)
                    witnesses[ident]=witness
                selected[name]=v.aggregate(witnesses) if witnesses else v.result('UNKNOWN',name,['No independently declared, executed witnesses'])
        for name,reason in policy[section]['not_applicable'].items():
            item=observed.get(name)
            # Missing facts in an explicitly excluded class do not make the
            # scoped verdict UNKNOWN. Keep their diagnostic status visible;
            # an actual measured failure is never silently hidden.
            selected[name]=item if isinstance(item,dict) and item.get('status')=='FAIL' else v.result(
                'NOT_APPLICABLE',name,[reason],observed_status=item.get('status') if isinstance(item,dict) else 'NOT_RUN')
        groups[section]=v.aggregate(selected)
        if selected and all(item['status']=='NOT_APPLICABLE' for item in selected.values()):
            groups[section]=v.result('NOT_APPLICABLE',section,['Every check in this group explicitly excluded by the external contract'],checks=selected)
    by_id={t['id']:t for t in tests}
    selected={}
    for name,item in policy['board_evidence'].items():
        if stage=='schematic':
            selected[name]=v.result('NOT_APPLICABLE',name,['Board revision witnesses deferred to final verification'],deferred=True);continue
        if not item['required']:
            selected[name]=v.result('NOT_APPLICABLE',name,[policy['analysis']['not_applicable'][name]]);continue
        aliases=(name,)
        observed={}
        for alias in aliases:
            group=categories.get(alias,{})
            if isinstance(group,dict):
                observed.update(group.get('checks',{}) or {})
        witnesses={}
        for ident,test in by_id.items():
            if test.get('category') not in aliases:continue
            result=_required(observed.get(ident),ident)
            if result['status']=='PASS':
                expected=dict(board_sha256=board_sha256,schematic_sha256=schematic_sha256,spec_sha256=spec_sha256,conditions_sha256=conditions_hash(test))
                problems=binding_issues(result,expected)
                if name=='dynamic_simulation' and test.get('backend')=='spice' and not str(test.get('command','')).lower().startswith('.tran '):
                    problems.append('Dynamic SPICE evidence requires an actual transient analysis')
                kind=result.get('evidence_kind')
                if name=='physical' and kind not in ('measurement','physical'):problems.append('Physical evidence must come from reviewed measurements')
                elif name=='protocol' and kind not in ('measurement','protocol','simulation'):problems.append('Protocol evidence needs a capture or qualified simulation')
                elif name in ('dynamic_simulation','rf','thermal') and kind not in ('simulation','measurement','physical'):problems.append('Numerical or measurement evidence required')
                if problems:result=v.result('UNKNOWN',ident,problems)
            witnesses[ident]=result
        if len(witnesses)<item['minimum_witnesses']:
            witnesses['coverage']=v.result('UNKNOWN',name,['Too few independently declared witnesses'])
        selected[name]=v.aggregate(witnesses)
    groups['board_evidence']=v.aggregate(selected)
    # All evidence may be explicitly outside a CAD prototype's scope.
    if selected and all(i['status']=='NOT_APPLICABLE' for i in selected.values()):
        groups['board_evidence']=v.result('NOT_APPLICABLE','board witnesses',['All evidence classes explicitly excluded by the contract'],checks=selected)
    if categories.get('revision') or 'revision' in analyses.get('checks',{}) or 'revision' in checks:
        groups['revision']=v.result('UNKNOWN','revision',['Inputs changed during verification'])
    return v.aggregate(groups)
