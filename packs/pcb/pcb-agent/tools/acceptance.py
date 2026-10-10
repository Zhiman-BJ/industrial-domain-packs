"""Independent acceptance against externally supplied original design requirements.

Run without network/credentials on a private copy of model output. Contracts
must live outside the model-writable workspace. CAD and electrical verdicts
remain separate. This is verification of declared requirements, not full function.
"""
from pathlib import Path
import argparse,copy,json,shutil,os
from tools import design,validation as v,verify,postroute,pcb_editor as ed
from tools.project_rules import validation_copy


def assess(workspace,contract,out_dir,*,check_claims=True,stage='final'):
    if stage not in ('final','pre_export'):raise ValueError('Invalid acceptance stage')
    from tools import library
    previous=os.environ.get("PCB_WORKSPACE")
    try:
        os.environ["PCB_WORKSPACE"]=str(Path(workspace).resolve());library.refresh()
        from tools import task_contract
        if task_contract.is_brief(contract):
            return _assess_brief(workspace, task_contract.validate(contract), out_dir)
        return _assess(workspace,contract,out_dir,check_claims=check_claims,stage=stage)
    finally:
        if previous is None:os.environ.pop("PCB_WORKSPACE",None)
        else:os.environ["PCB_WORKSPACE"]=previous
        library.refresh()


def _assess_brief(workspace, document, out_dir):
    """Measure candidate CAD while keeping uncompiled brief coverage UNKNOWN.

    Model-authored constraints/analyses are diagnostics, not task acceptance.
    A failing native check remains visible. No amount of self-declared PASS
    can promote this diagnostic session to a completed original task.
    """
    from tools import claims, task_contract
    root = Path(workspace).resolve(); out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    names = ('spec.json', 'board.kicad_sch', 'board.kicad_pcb', 'board.kicad_pro')
    missing = [name for name in names if not (root/name).is_file()]
    if missing:
        measured = v.result('FAIL', 'candidate artifacts', ['Missing '+name for name in missing])
        observed = {'cad_status': 'FAIL'}
    else:
        spec = json.loads((root/'spec.json').read_text()); design.validate(spec)
        measured = verify.verify(root/'board.kicad_pcb', root/'board.kicad_sch', spec=spec, out_dir=out/'native')
        observed = claims.evidence(measured)
    delivery = document['delivery']; target = root/delivery['directory']; stem = delivery['project_name']
    missing_delivery = [stem+ext for ext in ('.kicad_pro', '.kicad_sch', '.kicad_pcb')
                        if not (target/(stem+ext)).is_file()]
    checks = {
        'candidate_diagnostics': measured,
        'delivery_presence': v.result('FAIL' if missing_delivery else 'PASS', 'requested project files', missing_delivery),
        # Diagnostic contracts intentionally have no compiled predicates yet.
        # Keep this explicit so an empty predicate set cannot look like PASS.
        'task_requirements': v.result('NOT_APPLICABLE', 'compiled task predicates',
            ['Original brief is retained; independent predicates are not compiled in diagnostic mode.']),
        'brief_coverage': v.result('UNKNOWN', 'complete immutable engineering brief',
            ['Full textual requirements are retained but not yet compiled into qualified acceptance tests.',
             'Candidate-authored analyses do not replace independent functional acceptance.']),
    }
    report = v.aggregate(checks)
    report.update(cad_status=observed.get('cad_status','UNKNOWN'), electrical_status='UNKNOWN',
                  simulation_status='UNKNOWN', physical_status='UNKNOWN', scope='engineering',
                  evaluation_mode='diagnostic', eligible_for_model_success_rate=False,
                  brief_sha256=document['brief']['sha256'],
                  requirements_sha256=__import__('hashlib').sha256(json.dumps(document,sort_keys=True).encode()).hexdigest(),
                  diagnostic_electrical_status=measured.get('electrical_status','UNKNOWN'))
    from tools import task_obligations, diagnostics
    report['task_coverage']=task_obligations.evaluate(task_obligations.uncompiled(document['brief']['text']),{})
    report['feedback']=diagnostics.summarize({'checks':{'task_coverage':report['task_coverage']}},'acceptance',out/'result.json')
    (out/'result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    return report


def _check_path(report, path):
    """Resolve a dotted verifier path without treating missing evidence as pass."""
    if not isinstance(path, str) or not path.strip() or any(part in ('', '.', '..') for part in path.split('.')):
        return v.result('UNKNOWN', 'required verifier check', ['Invalid check path: '+str(path)])
    value=report
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            return v.result('UNKNOWN', path, ['Required verifier check missing'])
        value=value[part]
    if not isinstance(value, dict) or value.get('status') not in ('PASS','FAIL','UNKNOWN'):
        return v.result('UNKNOWN', path, ['Path does not identify a verifier result'])
    return value


def _assess(workspace,contract,out_dir,*,check_claims=True,stage='final'):
    root=Path(workspace).resolve();out=Path(out_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    flexible='requirements' in contract
    if flexible and 'spec' in contract:raise ValueError('Choose exact spec or declarative requirements, not both')
    if flexible:
        from tools import requirements
        schema=requirements.validate(contract['requirements'])
        if schema['status']=='PASS' and ('verification_policy' in contract or 'brief_coverage' in contract):
            from tools import task_contract
            try:task_contract.validate(task_contract.public_document(contract))
            except ValueError as error:schema=v.result('UNKNOWN','public verification contract',[str(error)])
        if schema['status']!='PASS':
            result=dict(schema,scope=contract.get('scope','electrical'),electrical_status='UNKNOWN',
                        requirements_mode='declarative',checks={'requirements_schema':schema},
                        contract_sha256=__import__('hashlib').sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest())
            from tools import diagnostics
            result['feedback']=diagnostics.summarize({'checks':result['checks']},'acceptance',out/'result.json')
            (out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False));return result
    if flexible and not (root/'spec.json').is_file():
        missing=v.result('FAIL','candidate design intent',['Missing spec.json'])
        result=dict(status='FAIL',scope=contract.get('scope','electrical'),cad=v.aggregate({'spec.json':missing}),
                    cad_status='FAIL',electrical_status='UNKNOWN',requirements_mode='declarative',
                    issues=['Missing spec.json'])
        (out/'result.json').write_text(json.dumps(result,indent=2));return result
    spec=copy.deepcopy(json.loads((root/'spec.json').read_text()) if flexible else contract['spec']);stem=contract.get('name','board')
    if flexible:
        # The circuit is model-selected; mandatory checks come only from the
        # external contract. Never accept model-authored waivers or test bounds.
        from tools import task_contract
        spec=task_contract.effective_spec(spec,contract)
    if Path(stem).name!=stem or stem in ('','.','..'):raise ValueError('Invalid project name')
    required=contract.get('required_files',['spec.json',stem+'.kicad_sch',stem+'.kicad_pcb',stem+'.kicad_pro'])
    if any(Path(n).is_absolute() or '..' in Path(n).parts for n in required):raise ValueError('Invalid required file path')
    checks={n:v.result('PASS' if (root/n).is_file() else 'FAIL','required output',[] if (root/n).is_file() else ['Missing '+n]) for n in required}
    full=None
    if all(c['status']=='PASS' for c in checks.values()):
        sch=root/(stem+'.kicad_sch');pcb=root/(stem+'.kicad_pcb')
        checks['erc']=v.run_erc(sch,out/'erc.json');checks['drc_as_delivered']=v.run_drc(pcb,out/'drc.json')
        checks['netlist']=v.export_netlist(sch,out/'netlist.xml')
        checks['parity_to_original']=v.compare_schematic_pcb(sch,pcb,out/'parity',spec)
        from tools import hierarchy
        checks['hierarchy_to_original']=hierarchy.check_structure(spec,sch)
        if contract.get('preserve_items'):
            from tools.board_ops import item_hashes
            actual_items=item_hashes(pcb)
            changed=[item for item,digest in contract['preserve_items'].items() if actual_items.get(item)!=digest]
            checks['preserved_items']=v.result('FAIL' if changed else 'PASS','explicitly protected original PCB objects',changed)
        actual_spec=json.loads((root/'spec.json').read_text());design.validate(actual_spec)
        intended={'components':{p['ref']:{'value':p.get('value',''),'footprint':p.get('footprint','')} for p in actual_spec['parts'] if not p.get('schematic_only')},'pins':v.intended_connections(actual_spec)}
        checks['saved_spec_to_original']=v.compare_spec_netlist(spec,intended)
        # Repeat DRC under the original declared rules, never a model-weakened project.
        controlled=validation_copy(root,out/'original-rules',spec,stem,contract.get('custom_rules'))
        checks['erc_original_rules']=v.run_erc(controlled/(stem+'.kicad_sch'),out/'original-erc.json')
        checks['drc_original_rules']=v.run_drc(controlled/(stem+'.kicad_pcb'),out/'original-drc.json')
        actual=ed.load(pcb);outline=ed.pcbnew.SHAPE_POLY_SET()
        if not ed.board_polygon_outlines(actual, outline):raise ValueError('Board outline could not be resolved')
        box=outline.BBox();dims={'w':ed.to_mm(box.GetWidth()),'h':ed.to_mm(box.GetHeight()),'layers':actual.GetCopperLayerCount()}
        issues=[f'board.{k}: expected {wanted}, measured {dims[k]}' for k,wanted in spec['board'].items() if k in dims and abs(dims[k]-wanted)>0.02]
        checks['board_dimensions']=v.result('FAIL' if issues else 'PASS','declared board bounding dimensions/layers',issues,measured=dims)
        if flexible:
            from tools import requirements
            exported=v.export_netlist(sch,out/'requirements-netlist.xml')
            checks['design_requirements']=requirements.check(contract['requirements'],spec,exported['data'],{'board':dims}) if exported['status']=='PASS' else exported
        if spec.get('outline'):
            from tools.board_ops import check_outline
            checks['board_outline']=check_outline(actual,spec['outline'])
        requirements=spec.setdefault('postroute',{})
        placements=requirements.setdefault('placements',[])
        for p in spec['parts']:
            if p.get('at') is not None:placements.append(dict(ref=p['ref'],x_mm=p['at'][0],y_mm=p['at'][1],**({'rotation_deg':p['rot']} if 'rot' in p else {}),**({'side':p['side']} if 'side' in p else {})))
        checks['postroute_original']=postroute.check_postroute(pcb,spec,out/'postroute')
        full=verify.verify(pcb,sch,spec=spec,out_dir=out/'full',verification_policy=contract.get('verification_policy'),
                           analysis_applicability=contract.get('requirements',{}).get('analysis'))
        if spec.get('analysis') is not None:
            # Required function/stability/EMC checks cannot be waived by choosing
            # CAD prototype scope or deleting the plan from model-authored spec.
            checks['required_analyses']=full['checks']['analyses']
        # Contracts may enumerate a stable set of required evidence paths. A
        # missing path is UNKNOWN and a failing path is FAIL; neither can be
        # bypassed by a model-authored claim or by another passing layer.
        required_paths=contract.get('required_checks',[])
        if required_paths is not None:
            if not isinstance(required_paths,list) or any(not isinstance(path,str) for path in required_paths):
                checks['required_checks']=v.result('UNKNOWN','required verifier checks',['required_checks must be a list of dotted paths'])
            else:
                selected={path:_check_path(full,path) for path in required_paths}
                checks['required_checks']=v.aggregate(selected) if selected else v.result('NOT_APPLICABLE','required verifier checks',['No extra checks declared'])
        # A full engineering contract can request a portable model inventory
        # and release bundle.  These checks are opt-in so CAD prototypes keep
        # their explicit scope, while the requirement cannot be accidentally
        # skipped when a contract declares it.
        if contract.get('require_3d'):
            try:
                from tools import mechanical
                records=mechanical.inventory(pcb)
                missing=[r['ref'] for r in records if not r['dnp'] and (not r['models'] or any(not m['exists'] for m in r['models']))]
                checks['model3d']=v.result('UNKNOWN' if missing else 'PASS','portable 3D body inventory',
                                            ['Missing assigned bodies: '+', '.join(missing)] if missing else [],records=records)
            except (OSError,ValueError,KeyError,TypeError) as error:
                checks['model3d']=v.result('UNKNOWN','portable 3D body inventory',[str(error)])
        else:
            checks['model3d']=v.result('NOT_APPLICABLE','portable 3D body inventory',['Contract does not require 3D bodies'])
        delivery_files=contract.get('delivery_files')
        if stage=='pre_export':
            checks['delivery']=v.result('NOT_APPLICABLE','delivery files',['Deferred until export'],deferred=True)
        elif delivery_files is not None:
            if not isinstance(delivery_files,list) or any(not isinstance(name,str) or not name or Path(name).is_absolute() or '..' in Path(name).parts for name in delivery_files):
                checks['delivery']=v.result('UNKNOWN','delivery files',['delivery_files must be relative paths'])
            else:
                missing=[name for name in delivery_files if not (root/name).is_file() or not (root/name).stat().st_size]
                checks['delivery']=v.result('FAIL' if missing else 'PASS','declared delivery files', ['Missing or empty: '+', '.join(missing)] if missing else [], files=delivery_files)
        else:
            checks['delivery']=v.result('NOT_APPLICABLE','declared delivery files',['Contract does not require a release bundle'])
        # New task contracts carry one generic policy covering the full
        # multi-stage verifier.  It is evaluated independently of any model
        # claim and keeps protocol/dynamic/RF/thermal/physical evidence from
        # being mistaken for ordinary ERC/DRC success.
        policy=contract.get('verification_policy')
        if policy is not None:
            from tools import verification_policy,verification_layers
            full['checks']['delivery']=checks['delivery']
            if contract.get('require_3d'):full['checks']['model3d']=checks['model3d']
            full['verification_layers']=verification_layers.summarize(full,spec,policy=policy,stage=stage)
            full['electrical_status']=full['verification_layers']['engineering_status']
            checks['verification_policy']=verification_policy.evaluate(
                full, policy,
                board_sha256=v.file_hash(pcb),
                schematic_sha256=v.file_hash(sch),
                spec_sha256=design.fingerprint(spec),
                tests=(spec.get('analysis') or {}).get('tests', []),stage=stage)
    if full is None and contract.get('required_checks'):
        checks['required_checks']=v.result('UNKNOWN','required verifier checks',['CAD artifacts failed before the requested checks could run'])
    cad=v.aggregate(checks);scope=contract.get('scope','electrical')
    # A simulation/model requirement can block task completion while native
    # CAD passes. Keep these meanings identical in the local and final gates.
    cad_status=v.aggregate({k:c for k,c in checks.items() if k not in ('required_analyses','verification_policy')})['status']
    engineering_plan=v.result('PASS','engineering scope')
    if scope=='engineering':
        engineering_plan=(full or {}).get('verification_layers',{}).get('engineering_coverage',v.result('UNKNOWN','engineering coverage',['Missing applicable evidence']))
    electrical=(full or {}).get('verification_layers',{}).get('electrical_qualification',full or v.result('UNKNOWN','missing artifacts'))
    status=cad['status'] if scope=='cad_prototype' else v.aggregate({'cad':cad,'electrical':electrical,'coverage':engineering_plan})['status']
    result={'status':status,'scope':scope,'cad':cad,'cad_status':cad_status,'board_sha256':v.file_hash(root/(stem+'.kicad_pcb')) if (root/(stem+'.kicad_pcb')).is_file() else None,
            'schematic_sha256':v.file_hash(root/(stem+'.kicad_sch')) if (root/(stem+'.kicad_sch')).is_file() else None,
            'electrical_status':full.get('electrical_status','UNKNOWN') if full else 'NOT_RUN',
            'scoped_status':((full or {}).get('verification_layers') or {}).get('scoped_status','UNKNOWN'),
            'full_engineering_status':((full or {}).get('verification_layers') or {}).get('full_engineering_status','UNKNOWN'),
            'simulation_status':((full or {}).get('verification_layers') or {}).get('acceptance_tiers',{}).get('simulation',{}).get('status','UNKNOWN'),
            'physical_status':((full or {}).get('verification_layers') or {}).get('acceptance_tiers',{}).get('hardware_qualification',{}).get('status','UNKNOWN'),
            'acceptance_tiers':((full or {}).get('verification_layers') or {}).get('acceptance_tiers',{}),
            'requirements_mode':'declarative' if flexible else 'exact_spec',
            'verification_layers':(full or {}).get('verification_layers'),
            'electrical_evidence':{k:{'status':check['status'],'issues':check.get('issues',[]),'warnings':check.get('warnings',[])} for k,check in (full or {}).get('checks',{}).items()},
            'contract_sha256':__import__('hashlib').sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest(),
            'coverage':engineering_plan,'analyses':(full or {}).get('checks',{}).get('analyses'),
            'note':'PASS covers only the explicitly declared checks and operating/model conditions. It is not product certification.'}
    from tools.task_contract import functional_coverage
    result['functional_coverage']=functional_coverage(contract)
    result['circuit_function_success']=bool(stage=='final' and result['status']=='PASS' and result['simulation_status']=='PASS'
        and result['functional_coverage']['eligible_for_circuit_training'])
    if result['acceptance_tiers']:
        tier=result['acceptance_tiers']['design_delivery']
        result['acceptance_tiers']['design_delivery']=v.aggregate({'policy_cad':tier,'contract_design_and_delivery':v.result(cad_status,'Independent design and delivery checks')})
    if 'brief_coverage' in contract:
        from tools import task_obligations
        result['task_coverage'] = task_obligations.evaluate(contract['brief_coverage'], result, stage=stage)
        result['status'] = v.aggregate({'design': v.result(result['status'], scope),
                                       'requirements': result['task_coverage']})['status']
        result['scoped_status'] = v.aggregate({'policy_scope': v.result(result['scoped_status'], scope),
                                              'requirements': result['task_coverage']})['status']
        if result['verification_layers']:
            result['verification_layers']['scoped_status']=result['scoped_status']
        result['circuit_function_success'] = result['circuit_function_success'] and result['status']=='PASS'
        status = result['status']
    if check_claims and (contract.get('require_supported_claims') or contract.get('verification_policy',{}).get('claims',{}).get('required')):
        from tools import claims
        try:submitted=json.loads((root/'claims.json').read_text())
        except (OSError,ValueError):submitted=None
        observed=claims.evidence(full or {})
        observed['cad_status']=cad_status
        observed['task_status']=status
        observed['scope']=scope
        result['claims']=claims.assess(submitted,observed,'cad_prototype' if scope=='cad_prototype' else 'engineering')
        result['status']=v.aggregate({'acceptance':v.result(status,scope),'claims':result['claims']})['status']
        result['circuit_function_success']=result['circuit_function_success'] and result['status']=='PASS'
    result['claims_checked']=bool(check_claims and (contract.get('require_supported_claims') or contract.get('verification_policy',{}).get('claims',{}).get('required')))
    from tools import diagnostics
    explained={'cad':cad,'coverage':engineering_plan,**({'claims':result['claims']} if 'claims' in result else {})}
    if 'task_coverage' in result:explained['task_coverage']=result['task_coverage']
    if scope!='cad_prototype':explained['electrical']=full or v.result('UNKNOWN','Missing electrical evidence')
    result['feedback']=diagnostics.summarize({'checks':explained},'acceptance',out/'result.json')
    (out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False));return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--contract',required=True);p.add_argument('--out',required=True)
    a=p.parse_args()
    try:r=assess(a.workspace,json.loads(Path(a.contract).read_text()),a.out)
    except Exception as e:
        r=v.result('UNKNOWN','independent acceptance',[f'{type(e).__name__}: {e}']);Path(a.out).mkdir(parents=True,exist_ok=True);(Path(a.out)/'result.json').write_text(json.dumps(r,indent=2))
    print(json.dumps(r,indent=2));raise SystemExit(0 if r['status']=='PASS' else 1 if r['status']=='FAIL' else 2)
