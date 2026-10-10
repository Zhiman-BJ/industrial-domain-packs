"""Stable evidence layers over shared verifiers; no task IDs or reference answers."""
from tools import validation as v

SCHEMA_VERSION=6

# These names are the stable public evidence contract.  A verifier may add
# diagnostics, but callers can always address these layers without knowing a
# task id or solver implementation.
STANDARD_CHECKS = (
    'erc', 'drc', 'netlist', 'parity', 'hierarchy', 'footprints',
    'datasheets', 'power_tree', 'interfaces', 'operating_limits',
    'function', 'stability', 'dynamic_simulation', 'protocol', 'physical',
    'rf', 'thermal', 'si', 'pi', 'emc_precheck', 'emc', 'mechanical', '3d', 'delivery',
)


def summarize(report,spec,delivery=None,policy=None,stage='final'):
    checks=report.get('checks',{})
    def group(names,scope,required=()):
        selected={key:checks[key] for key in names if key in checks}
        for key in required:selected.setdefault(key,v.result('UNKNOWN',key,['Required evidence missing']))
        result=v.aggregate(selected);result['scope']=scope
        return result
    layers={
        'schematic_connectivity':group(('erc','netlist','parity','hierarchy','built_in_electrical'),'Native ERC, netlist, pin connectivity and sheet consistency',('erc','netlist','parity')),
        'component_electrical':group(('datasheets','power_tree','interfaces','operating_limits'),'Sourced device and operating-condition evidence',('datasheets','operating_limits')),
        'function_and_physics':checks.get('analyses',v.result('UNKNOWN','Declared numerical/functional checks',['Analysis evidence missing'])),
        'mechanical_assembly':checks.get('mechanical',checks.get('analyses',{}).get('categories',{}).get('mechanical',v.result('UNKNOWN','Nominal STEP assembly checks',['No declared mechanical analysis; no collision or height verdict']))),
        'layout_and_geometry':group(('drc','footprints','postroute','outline'),'Native clearance, connectivity, footprint and declared geometry checks',('drc','footprints','postroute')),
        'artifact_integrity':v.result('UNKNOWN' if 'revision' in checks else 'PASS' if report.get('sources') else 'UNKNOWN','Input revision integrity',['Input changed during verification'] if 'revision' in checks else []),
        'delivery_files':delivery or v.result('UNKNOWN','Production-file generation and consistency',['No fresh checked deliverable bundle supplied']),
    }
    # Keep reproducible solver results and instrument/hardware evidence as
    # separate layers.  A passing KiCad/ngspice/openEMS run is useful evidence,
    # but it cannot silently become a physical board verdict.
    analyses=checks.get('analyses',{})
    analysis_categories=analyses.get('categories',{}) if isinstance(analyses,dict) else {}
    def analysis_group(name, categories=(), evidence_kinds=()):
        selected={}
        for category,aggregate in analysis_categories.items():
            if categories and category not in categories:continue
            for test_id,item in (aggregate.get('checks',{}) if isinstance(aggregate,dict) else {}).items():
                if not evidence_kinds or item.get('evidence_kind') in evidence_kinds:
                    selected[test_id]=item
        return v.aggregate(selected) if selected else v.result('UNKNOWN',name,['No executed '+name.replace('_',' ')+' tests for this revision'])

    # Keep source classes explicit. A protocol capture is not silently counted
    # as generic functional SPICE, and a thermal/RF test cannot be replaced by
    # a DRC or a board image.
    for layer,kind,categories,evidence_kinds in [
        ('simulation_evidence','simulation',(),('simulation',)),
        ('protocol_evidence','protocol',('protocol',),('protocol','measurement','simulation')),
        ('thermal_evidence','thermal',('thermal',),('simulation','measurement','physical')),
        ('rf_si_evidence','RF/SI',('rf','emc','si'),('simulation','measurement','physical')),
        ('power_integrity_evidence','PI',('pi',),('simulation','measurement','physical')),
        ('physical_evidence','measurement',(),('measurement','physical')),
    ]:
        if categories:
            summary=analysis_group(kind,categories,evidence_kinds)
        else:
            summary=analysis_group(kind,(),evidence_kinds)
        if 'revision' in analyses.get('checks',{}) or 'revision' in checks:
            summary=v.result('UNKNOWN',kind,['Inputs changed during verification'])
        summary.update(scope='Only the named '+kind+' tests and their declared conditions',test_count=len(summary.get('checks',{})))
        layers[layer]=summary
    # Geometry/functional evidence and a successful export are separate. In the
    # factory-neutral environment no process/material qualification is invented.
    layers['manufacturing_process']=v.result('UNKNOWN','Physical manufacturing process qualification',[
        'Factory-neutral environment has no qualified process/material/stackup compatibility check; DRC and file generation alone cannot guarantee production yield'])
    plan=spec.get('analysis',{})
    coverage=v.result('UNKNOWN','Engineering applicability',[
        'No reviewed external applicability policy supplied; executed analyses retain their individual verdicts. '
        'The contract owner must identify required checks and justified exclusions; candidate intent cannot establish complete engineering coverage.'])
    engineering=v.aggregate({'design':v.result(report.get('status','UNKNOWN'),'Fresh design checks'),'coverage':coverage})
    applicability={'source':'analysis plan only; complete engineering applicability is not reviewed',
                   'required':list(plan.get('required',[])), 'not_applicable':dict(plan.get('not_applicable',{})),
                   'scope_status':'UNKNOWN'}
    if policy is not None:
        from tools import verification_policy as vp, design
        # The caller supplies the trusted external contract. Never obtain this
        # policy from model-editable spec.json or a submitted claim.
        schema=vp.validate(policy)
        coverage=schema
        if schema['status']=='PASS':
            def primary_hash(kind,suffix):
                sources=report.get('sources',{});path=report.get('primary_sources',{}).get(kind)
                if path is not None:return sources.get(path) if path.endswith(suffix) else None
                matches=[h for name,h in sources.items() if name.endswith(suffix)]
                return matches[0] if len(matches)==1 else None
            board=primary_hash('board','.kicad_pcb');sch=primary_hash('schematic','.kicad_sch')
            engineering=vp.evaluate(report,policy,board_sha256=board,schematic_sha256=sch,
                                    spec_sha256=design.fingerprint(spec),tests=plan.get('tests',[]),stage=stage)
            applicability={'source':'external verification policy','required':policy['analysis']['required'],
                           'not_applicable':policy['analysis']['not_applicable'],
                           'cad':policy['cad'],'scope_status':engineering['status']}
        else:engineering=schema
        coverage=v.result(engineering['status'],'Required evidence coverage',
                          engineering.get('issues',[]),policy_schema_status=schema['status'],
                          details='verification_layers.scoped_evidence')
    # Scoped policy success is not full electrical qualification. Require real
    # device/operating facts and executed function evidence for that label.
    qualification={'applicable_checks':engineering}
    for name in ('datasheets','operating_limits'):
        item=checks.get(name,{})
        qualification[name]=item if item.get('status') in ('PASS','FAIL') else v.result('UNKNOWN',name,['Qualified electrical evidence required for electrical_status=PASS'])
    function=analysis_categories.get('function',{})
    qualification['function']=function if function.get('status') in ('PASS','FAIL') else v.result('UNKNOWN','function',['Executed functional evidence required'])
    qualified=v.aggregate(qualification)
    physical_scope=v.result('UNKNOWN','Physical qualification coverage',['No required physical witness in the external policy'])
    if policy is not None and coverage['status']=='PASS' and 'physical' in policy['analysis']['required']:
        physical_scope=engineering
    production=v.aggregate({'engineering':qualified,'files':layers['delivery_files'],'process':layers['manufacturing_process']})
    aliases={'drc':('drc','drc_as_delivered'),'parity':('parity','parity_to_original'),
             'netlist':('netlist',),'erc':('erc',),'hierarchy':('hierarchy','hierarchy_to_original'),
             'footprints':('footprints',),'datasheets':('datasheets',),'power_tree':('power_tree',),
             'interfaces':('interfaces',),'operating_limits':('operating_limits',),
             'mechanical':('mechanical',),'3d':('model3d',),'delivery':('delivery',)}
    status_map={}
    for name,names in aliases.items():
        item=next((checks[key] for key in names if key in checks),None)
        if item is None and name in analysis_categories:
            item=analysis_categories[name]
        status_map[name]=item.get('status','UNKNOWN') if isinstance(item,dict) else 'UNKNOWN'
    for name in ('function','stability','dynamic_simulation','protocol','physical','rf','thermal','si','pi','emc_precheck','emc'):
        status_map[name]=analysis_categories.get(name,{}).get('status','UNKNOWN') if isinstance(analysis_categories.get(name),dict) else 'UNKNOWN'
    from tools import acceptance_tiers
    tiers=acceptance_tiers.summarize(report,policy,plan.get('tests',[]),
        board_sha256=primary_hash('board','.kicad_pcb') if policy is not None and schema['status']=='PASS' else None,
        schematic_sha256=primary_hash('schematic','.kicad_sch') if policy is not None and schema['status']=='PASS' else None,
        spec_sha256=design.fingerprint(spec) if policy is not None else None,stage=stage)
    return {'acceptance_tiers':tiers,'schema_version':SCHEMA_VERSION,'layers':layers,'standard_checks':STANDARD_CHECKS,
            'standard_check_statuses':status_map,
            'design_status':report.get('status','UNKNOWN'),
            'engineering_status':qualified['status'],'engineering_coverage':coverage,
            'scoped_status':engineering['status'],'electrical_qualification':qualified,
            'full_engineering_status':v.aggregate({'electrical':qualified,'physical':layers['physical_evidence'],'physical_scope':physical_scope})['status'],
            'applicability':applicability,'scoped_evidence':engineering,
            'production_status':production['status'],
            'note':'PASS is limited to the named layer. Product certification, assembly yield and measured stability require evidence outside CAD.'}
