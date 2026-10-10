"""Gated schematic-first build/check loop with immutable attempt directories.

Exit 0: verified for the declared scope, 1: failed checks, 2: missing evidence,
3: stalled routing. Repeated invocation never overwrites prior attempts.
This is a fresh-build utility, not a repair controller. Existing attempts require
local workspace edits; an explicit fresh_build creates a separate new design. The legacy
router is opt-in and is never described as an obstacle-aware routing engine.
"""
from __future__ import annotations
import argparse
import datetime
import json
import math
from pathlib import Path
import shutil
import time
import uuid
from tools import bootstrap,design,validation as v,kicad_cli as kc,pcb_editor as ed,verify,knowledge,routing,intake,analysis


class Trajectory:
    def __init__(self,path): self.path=Path(path);self.t0=time.monotonic()
    def log(self,step,**kw):
        with self.path.open('a') as f:f.write(json.dumps(dict(step=step,t=time.monotonic()-self.t0,**kw),ensure_ascii=False,default=str)+'\n')


def run(spec,out_dir,name='board',route=False,max_iter=3,router=None,route_timeout_s=300,base_dir=None,fresh_build=False):
    """Generate schematic, gate electrical checks, build PCB, and verify changes."""
    design.validate(spec)
    if not spec['parts']:raise ValueError('An empty design cannot be validated')
    if Path(name).name!=name or name in ('','.','..'):raise ValueError('name must be a filename stem')
    if max_iter<1:raise ValueError('max_iter must be positive')
    if router not in (None,'freerouting'):raise ValueError('Unsupported routing backend')
    if route and router:raise ValueError('Select either Freerouting or the experimental legacy router')
    root=Path(out_dir);root.mkdir(parents=True,exist_ok=True)
    existing=sorted((root/'attempts').glob('*')) if (root/'attempts').is_dir() else []
    if not fresh_build and (existing or (root/f'{name}.kicad_pcb').exists() or (root/f'{name}.kicad_sch').exists()):
        raise FileExistsError('Existing design at '+str(root.resolve())+'. Inspect latest.json and the saved check report; use local workspace edits and update_pcb to preserve routing. A deliberate separate rebuild requires fresh_build=True / --fresh-build.')
    out=root/'attempts'/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
    out.mkdir(parents=True);design.save_spec(spec,out/'spec.json')
    traj=Trajectory(out/'trajectory.jsonl');checks={};sch=out/f'{name}.kicad_sch';pcb=out/f'{name}.kicad_pcb'
    traj.log('start',spec_sha256=design.fingerprint(spec),action={'tool':'loop.run','route':route,'max_iter':max_iter},tool_version=kc.version(), source_hashes={p.name:v.file_hash(p) for p in Path(__file__).parent.glob('*.py')}, rule_registry_sha256=v.file_hash(knowledge.DEFAULT))
    def stop(reason):
        from tools import diagnostics
        report=v.aggregate(checks)
        report.update(reason=reason,attempt=str(out),spec_sha256=design.fingerprint(spec),pass_=report['status']=='PASS')
        report['pass']=report.pop('pass_')
        report['feedback']=diagnostics.summarize(report,'build',out/'result.json')
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        (out/'DONE.md').write_text(f"# {report['status']}: {reason}\n\nScope: declared circuit constraints and CAD checks only.\n\nSee result.json for evidence.\n")
        traj.log('stop',status=report['status'],reason=reason)
        (root/'latest.json').write_text(json.dumps({'attempt':str(out),'status':report['status']}))
        return report
    def gate(name,check):
        checks[name]=check;traj.log('check',check=name,result=check)
        return check['status'] in ('PASS','NOT_APPLICABLE')
    try:
        base=Path(base_dir or '.').resolve()
        from tools import models
        for dependency in models.dependencies(spec.get('analysis',{})):
            path=Path(dependency)
            if path.is_absolute():continue
            if '..' in path.parts:raise ValueError('Relative analysis dependencies must stay inside project')
            target=out/path;target.parent.mkdir(parents=True,exist_ok=True)
            if (base/path).is_file():shutil.copy2(base/path,target)
        acquisition=spec.get('acquisition')
        if acquisition:
            acquired={}
            for mpn in sorted({p['mpn'] for p in spec['parts'] if p.get('mpn')}):
                acquired[mpn]=intake.ensure_component(mpn,acquisition['catalog'],out/'intake',acquisition['installed_root'])
            if not gate('acquisition',v.aggregate(acquired)):return stop('COMPONENT_EVIDENCE_BLOCKED')
        bootstrap.build(out,name,spec,schematic_only=True)
        if not gate('built_in_electrical',knowledge.check_schematic_rules(spec)):return stop('SCHEMATIC_BLOCKED')
        if not gate('erc',v.run_erc(sch,out/'erc.json')):return stop('SCHEMATIC_BLOCKED')
        exp=v.export_netlist(sch,out/'intent.xml')
        if not gate('netlist',exp):return stop('SCHEMATIC_BLOCKED')
        if not gate('intent_parity',v.compare_spec_netlist(spec,exp['data'])):return stop('SCHEMATIC_BLOCKED')
        electric=analysis.electrical_checks(spec,sch,None,out/'electrical',stage='schematic')
        if not gate('electrical',v.aggregate(electric)):return stop('ELECTRICAL_BLOCKED')
        if not gate('footprints',v.check_footprints(spec)):return stop('FOOTPRINT_BLOCKED')
        bootstrap.build(out,name,spec)
        if not gate('initial_parity',v.compare_schematic_pcb(sch,pcb,out/'initial-parity',spec)):return stop('PCB_SYNC_BLOCKED')
        from tools.project_rules import apply
        apply(pcb,spec)
        if router=='freerouting':
            routed=routing.route_board(pcb,out/'routing',timeout_s=route_timeout_s,max_passes=max_iter)
            traj.log('action',tool='routing.route_board',result=routed)
            if not gate('routing',routed):return stop('ROUTING_BLOCKED')
            shutil.copy2(routed['candidate'],pcb)
        previous=None
        for i in range(max_iter if route else 1):
            before=v.file_hash(pcb)
            if route:
                b=ed.load(pcb);ed.unroute(b);ar=ed.autoroute(b);ed.save(b,pcb)
                traj.log('action',i=i,tool='pcb_editor.autoroute',experimental=True,before_sha256=before,after_sha256=v.file_hash(pcb),result=ar)
            b=ed.load(pcb)
            if b.GetAreaCount():
                if not gate('copper_fill',kc.fill_zones_file(pcb)):return stop('COPPER_FILL_BLOCKED')
            check=verify.verify(pcb,sch,out_dir=out/'verify',spec=spec)
            gate('final',check)
            traj.log('transition',i=i,action={'tool':'autoroute' if route else 'verify'},before_sha256=before,after_sha256=v.file_hash(pcb),next_state={'status':check['status']},result=check['status'])
            if check['status']=='PASS':return stop('VERIFIED_FOR_DECLARED_SCOPE')
            now=v.file_hash(pcb)
            if not route or now==previous:return stop('STALLED_NEEDS_DESIGN_OR_ROUTING_EDIT')
            previous=now
            # The legacy router has no adaptive obstacle solver; repeating the same
            # operation is not a repair. Return control to the agent immediately.
            return stop('STALLED_NEEDS_DESIGN_OR_ROUTING_EDIT')
        return stop('BUDGET_EXHAUSTED')
    except Exception as e:
        gate('execution',v.result('UNKNOWN','tool execution',[f'{type(e).__name__}: {e}']))
        return stop('TOOL_ERROR')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',required=True);p.add_argument('--out',required=True);p.add_argument('--name',default='board')
    p.add_argument('--experimental-route',action='store_true');p.add_argument('--max-iter',type=int,default=3)
    p.add_argument('--router',choices=['freerouting']);p.add_argument('--route-timeout',type=int,default=300)
    p.add_argument('--fresh-build',action='store_true',help='Explicitly start another independent build; never use this to repair existing layout/copper')
    a=p.parse_args(argv);s=json.loads(Path(a.spec).read_text())
    r=run(s,a.out,a.name,a.experimental_route,a.max_iter,a.router,a.route_timeout,base_dir=Path(a.spec).resolve().parent,fresh_build=a.fresh_build);print(json.dumps(r,indent=2,ensure_ascii=False))
    return 0 if r['status']=='PASS' else 2 if r['status']=='UNKNOWN' else 3 if r['reason'].startswith('STALLED') else 1


if __name__=='__main__':raise SystemExit(main())
