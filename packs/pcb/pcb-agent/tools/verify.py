"""Official CAD verification with explicit scope; no surrogate functional PASS."""
from pathlib import Path
import json
from tools import validation as v,knowledge,postroute,analysis,design


def verify(pcb, sch=None, pinout=None, contract=None, out_dir=None, reference_board=None, spec=None, verification_policy=None, analysis_applicability=None):
    """Re-run fresh DRC and ERC and compare real pin connectivity.

    Old port-contract arguments alone are insufficient for electrical sign-off.
    No approximate copper simulation contributes to this verdict.
    """
    out=Path(out_dir or Path(pcb).parent/'verify');out.mkdir(parents=True,exist_ok=True)
    tracked=[Path(p) for p in (pcb,sch,Path(pcb).with_suffix('.kicad_pro')) if p and Path(p).is_file()]
    if sch and Path(sch).is_file():
        from tools import hierarchy
        tracked.extend(p for p in hierarchy.dependencies(sch) if p!=Path(sch).resolve())
    before={str(p):v.file_hash(p) if p.is_file() else 'MISSING' for p in tracked}
    checks={'drc':v.run_drc(pcb,out/'drc.json')}
    if sch:
        checks['erc']=v.run_erc(sch,out/'erc.json')
        # Keep the exported netlist as a first-class observation.  Parity is
        # then a separate comparison of that netlist against the actual PCB;
        # callers do not have to infer a netlist check from a parity result.
        checks['netlist']=v.export_netlist(sch,out/'netlist.xml')
        checks['parity']=v.compare_schematic_pcb(sch,pcb,out,spec)
    else: checks['schematic']=v.result('UNKNOWN','schematic',['Schematic required'])
    if spec:
        from tools import hierarchy
        if sch:checks['hierarchy']=hierarchy.check_structure(spec,sch)
        checks['built_in_electrical']=knowledge.check_schematic_rules(spec)
        checks.update(analysis.electrical_checks(spec,sch,pcb,out/'electrical',
            applicability=verification_policy.get('analysis') if verification_policy else analysis_applicability))
        checks['footprints']=v.check_footprints(spec)
        checks['postroute']=postroute.check_postroute(pcb,spec,out/'postroute')
        # Mechanical and 3D are explicit, scoped checks.  They are
        # NOT_APPLICABLE for a project that declares no body/assembly
        # requirement; a missing declared model remains UNKNOWN.
        if any(t.get('category')=='mechanical' for t in spec.get('analysis',{}).get('tests',[])) or spec.get('mechanical'):
            try:
                from tools import mechanical
                records=mechanical.inventory(pcb)
                missing=mechanical.missing_bodies(records)
                checks['model3d']=v.result('UNKNOWN' if missing else 'PASS','assigned 3D body inventory',
                                            ['Missing project-local/assigned bodies: '+', '.join(missing)] if missing else [],
                                            records=records,board_sha256=v.file_hash(pcb))
            except (OSError,ValueError,KeyError,TypeError) as error:
                checks['model3d']=v.result('UNKNOWN','assigned 3D body inventory',[str(error)])
        else:
            checks['model3d']=v.result('NOT_APPLICABLE','3D assembly','No assembly or 3D requirement declared')
        checks['mechanical']=checks.get('analyses',{}).get('categories',{}).get('mechanical',
            v.result('NOT_APPLICABLE','mechanical checks','No mechanical analysis declared'))
        # Delivery generation is intentionally separate (`export_project`).
        # Recording the boundary prevents a verifier caller from mistaking a
        # design check for a release bundle.
        checks['delivery']=v.result('NOT_APPLICABLE','delivery bundle','Run export_project/export_bundle for fresh CAM and 3D deliverables')
        if spec.get('outline'):
            from tools import board_ops,pcb_editor
            checks['outline']=board_ops.check_outline(pcb_editor.load(pcb),spec['outline'])
    else: checks['electrical_intent']=v.result('UNKNOWN','electrical intent',['Supply canonical spec for electrical checks'])
    if pinout or contract or reference_board:
        checks['legacy_contract']=v.result('UNKNOWN','legacy contract',['Migrate external intent into spec.interfaces; no name-based signature sign-off'])
    after={str(p):v.file_hash(p) for p in tracked if p.is_file()}
    if before!=after:checks['revision']=v.result('UNKNOWN','revision',['Input changed during verification'])
    r=v.aggregate(checks);r['pass']=r['status']=='PASS'
    r['reasons']=[f"{k}: {x['status']}" for k,x in checks.items() if x['status'] not in ('PASS','NOT_APPLICABLE')]
    r['scope']='CAD/ERC/connectivity plus declared electrical constraints; not full product certification'
    r['sources']=after
    r['primary_sources']={'board':str(Path(pcb)),**({'schematic':str(Path(sch))} if sch else {})}
    if spec:r['spec_sha256']=design.fingerprint(spec)
    from tools import verification_layers
    r['verification_layers']=verification_layers.summarize(r,spec or {},policy=verification_policy)
    r['electrical_status']=r['verification_layers']['engineering_status']
    from tools import diagnostics
    r['feedback']=diagnostics.summarize(r,'final',out/'verify.json')
    (out/'verify.json').write_text(json.dumps(r,indent=2,ensure_ascii=False))
    return r


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('pcb');p.add_argument('--sch');p.add_argument('--spec');p.add_argument('--out')
    a=p.parse_args();s=json.loads(Path(a.spec).read_text()) if a.spec else None
    r=verify(a.pcb,a.sch,out_dir=a.out,spec=s);print(json.dumps(r,ensure_ascii=False,indent=2))
    raise SystemExit(0 if r['status']=='PASS' else 1 if r['status']=='FAIL' else 2)
