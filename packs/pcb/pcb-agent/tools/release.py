"""Fresh verification and revision-bound design deliverables; no ordering."""
from pathlib import Path
import csv
import json
import shutil
import copy
from tools import validation as v,verify,kicad_cli as kc,design


def export_bundle(board, schematic, spec, destination, include_3d=False, task_document=None):
    """Verify current design then export CAD, Gerber, drill, BOM, positions and SVG.

All exports are made in a new directory. Failure retains evidence but never
creates a successful delivery manifest. Gerber output is not factory approval.
"""
    out=Path(destination).resolve()
    if out.exists():raise FileExistsError(out)
    out.mkdir(parents=True)
    board=Path(board).resolve();schematic=Path(schematic).resolve()
    inputs=[board,schematic,board.with_suffix('.kicad_pro'),board.with_suffix('.kicad_dru'),board.parent/'sym-lib-table',board.parent/'fp-lib-table']
    hashes={str(p):v.file_hash(p) for p in inputs if p.is_file()}
    from tools import hierarchy,models
    hierarchy_error=None
    try:hashes.update(hierarchy.hashes(schematic))
    except (ValueError,OSError) as e:hierarchy_error=str(e)
    dependencies=models.dependencies(spec.get('analysis',{}))
    if include_3d or any(t.get('category')=='mechanical' for t in spec.get('analysis',{}).get('tests',[])):
        from tools import mechanical
        for dependency in mechanical.dependencies(board):
            if dependency.name=='sources.json' and not dependency.is_file():continue
            if not dependency.is_relative_to(board.parent):raise ValueError('Portable 3D delivery requires project-local models; prepare_3d_model first')
            dependencies.append(str(dependency.relative_to(board.parent)))
    for dependency in dependencies:
        path=Path(dependency)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Portable release requires project-relative model/input paths')
        source=board.parent/path
        hashes[str(source)]=v.file_hash(source) if source.is_file() else 'MISSING'
    if task_document is None:
        checked=verify.verify(board,schematic,out_dir=out/'checks',spec=spec)
    else:
        from tools import acceptance, task_contract
        # Files this operation creates are checked after export and at final
        # acceptance. All design/analysis obligations apply before export.
        checked=acceptance.assess(board.parent,task_document,out/'checks',check_claims=False,stage='pre_export')
    checks={'verification':checked}
    if hierarchy_error:checks['dependencies']=v.result('UNKNOWN','schematic dependencies',[hierarchy_error])
    def finish():
        if any(not Path(p).is_file() or v.file_hash(p)!=h for p,h in hashes.items()):
            checks['revision']=v.result('UNKNOWN','export revision',['Input changed during export'])
        report=v.aggregate(checks)
        report.update(source_hashes=hashes,spec_sha256=design.fingerprint(spec),
                      files={str(p.relative_to(out)):v.file_hash(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})
        from tools import verification_layers
        delivery=v.aggregate({key:check for key,check in checks.items() if key!='verification'})
        if checked.get('verification_layers'):
            layers=copy.deepcopy(checked['verification_layers'])
            layers['layers']['delivery_files']=delivery
            layers['standard_check_statuses']['delivery']=delivery['status']
            layers['production_status']=v.aggregate({'engineering':v.result(layers['engineering_status'],'engineering'),
                'files':delivery,'process':layers['layers']['manufacturing_process']})['status']
            report['verification_layers']=layers
        else:report['verification_layers']=verification_layers.summarize(checked,spec,delivery)
        if task_document is not None:
            report['public_document_sha256']=__import__('hashlib').sha256(json.dumps(task_document,sort_keys=True).encode()).hexdigest()
            report['verification_scope']='Public design requirements before export; final acceptance also checks declared delivery paths and claims'
        report['export_status']=report['status']
        report['production_status']=report['verification_layers']['production_status']
        report['scope']='Revision-bound CAD/CAM export checks; production readiness is reported separately'
        (out/'manifest.json').write_text(json.dumps(report,indent=2));return report
    if any(check['status']!='PASS' for check in checks.values()):return finish()
    cad=out/'cad';cad.mkdir()
    for path in hashes:
        source=Path(path);target=cad/source.relative_to(board.parent);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    from tools import library
    library.copy_project_libraries(board.parent,cad)
    design.save_spec(spec,cad/'spec.json')
    jobs=[('gerbers',lambda:kc.export_gerbers(board,out/'gerber')),
          ('drill',lambda:kc.export_drill(board,out/'drill')),
          ('bom',lambda:kc.export_bom(schematic,out/'bom.csv')),
          ('positions',lambda:kc.export_positions(board,out/'positions.csv')),
          ('svg',lambda:kc.export_svg(board,out/'views'/'board.svg'))]
    for name,fn in jobs:
        r=fn();checks[name]=v.result('PASS' if r.ok else 'UNKNOWN','export '+name,[r.stderr] if not r.ok else [],argv=r.argv)
    if include_3d:
        from tools import mechanical
        for kind in ('step','glb','png'):
            checks['3d_'+kind]=mechanical.export(board,out/'views'/('board.'+kind),kind)
    required=['bom.csv','positions.csv']
    missing=[name for name in required if not (out/name).is_file() or not (out/name).stat().st_size]
    for directory,suffix in [('gerber','.gbr'),('drill','.drl'),('views','.svg')]:
        if not any(p.stat().st_size for p in (out/directory).glob('*'+suffix)):missing.append(directory)
    checks['outputs']=v.result('FAIL' if missing else 'PASS','nonempty expected export formats',missing)
    if not missing:
        try:
            with (out/'bom.csv').open() as f:rows=list(csv.DictReader(f))
            refs=set()
            for row in rows:
                value=row.get('Reference') or row.get('References') or row.get('Refs') or ''
                refs.update(x.strip() for x in value.split(',') if x.strip())
            expected={p['ref'] for p in spec['parts'] if not p.get('schematic_only')}
            checks['bom_membership']=v.result('PASS' if refs==expected else 'FAIL','exported BOM reference membership',expected=sorted(expected),actual=sorted(refs))
            with (out/'positions.csv').open() as f:rows=list(csv.DictReader(f))
            refs={row.get('Ref') or row.get('Reference') for row in rows}
            checks['position_membership']=v.result('PASS' if refs==expected else 'FAIL','exported placement reference membership',expected=sorted(expected),actual=sorted(refs-{None}))
        except (ValueError,OSError) as e:checks['export_parse']=v.result('UNKNOWN','export validation',[str(e)])
    return finish()
