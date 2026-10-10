"""Capture real CAD revisions and export a read-only, live/replay web view.

Capture runs in the session process. Native SVG rendering runs separately, so
export work cannot change the model's design or verification status.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path


def read(path,default=None):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return default


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def report_checks(checks, reports):
    """Project explicit report results without letting stale parents hide fresh checks."""
    shown={k:{'status':v.get('status','UNKNOWN'),'fresh':v.get('fresh',False)} for k,v in checks.items()}
    if 'analysis' in shown:shown['analyses']=shown.pop('analysis')
    def put(key,value,fresh):
        if not isinstance(value,dict):return
        if not fresh and shown.get(key,{}).get('fresh'):return
        shown[key]={'status':value.get('status','UNKNOWN') if fresh else 'STALE','fresh':fresh}
    def collect(report,fresh):
        if not isinstance(report,dict):return
        entries={**report,**report.get('checks',{})}
        for key in ('erc','drc','analyses'):
            if key in entries:put(key,entries[key],fresh)
        if 'drc_as_delivered' in entries:put('drc',entries['drc_as_delivered'],fresh)
        for key in ('cad','electrical'):
            if key in entries:collect(entries[key],fresh)
    for report,fresh in reports:collect(report,fresh)
    return shown


def pcb_view_layers(board):
    """The combined native view includes every enabled copper layer."""
    from tools import pcb_editor as ed
    copper=[board.GetLayerName(layer) for layer in board.GetEnabledLayers().Seq() if ed.pcbnew.IsCopperLayer(layer)]
    return ','.join(copper+['F.SilkS','B.SilkS','Edge.Cuts'])


def restore_recorded_checks(root,frames):
    """Repair old display metadata from earlier, revision-matching tool reports only.

    Original captures, observations and acceptance records remain untouched.
    Missing inputs or unmatched action indices cannot establish freshness.
    """
    from tools import design
    root=Path(root)
    trace=root/'session/model-trajectory.jsonl'
    if not trace.is_file():return
    stages={'run_erc':'erc','run_drc':'drc','verify_schematic':'schematic',
            'run_analysis':'analysis','verify_design':'final',
            'compare_schematic_pcb':'parity','export_netlist':'netlist'}
    reports={};index=0
    for line in trace.read_text().splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if event.get('event')!='tool_action':continue
        index+=1;frame=frames.get(index)
        if frame is None or frame.get('tool')!=event.get('tool') or frame.get('arguments')!=event.get('arguments'):continue
        try:report=json.loads(event.get('observation',{}).get('stdout',''))
        except ValueError:report=None
        stage=stages.get(event.get('tool'))
        if stage and isinstance(report,dict) and report.get('sources') and report.get('report_path'):
            reports[stage]=report
        if frame.get('capture_schema_version',1)>=3:continue
        available=dict(frame.get('files',{}))
        spec=read(root/'session/visual/frames'/f'{index:05d}'/'source/spec.json')
        if isinstance(spec,dict):available['spec']=design.fingerprint(spec)
        checks=dict(frame.get('checks',{}));observed=[]
        for stage,report in reports.items():
            fresh=all(available.get(name)==digest for name,digest in report['sources'].items())
            checks[stage]={'status':report.get('status','UNKNOWN') if fresh else 'STALE','fresh':fresh}
            observed.append((report,fresh))
        frame['checks']=report_checks(checks,observed)
        frame['checks_display_source']='Earlier recorded tool reports matched to captured input hashes; original evidence retained'


def capture(root,index,tool,arguments,observation=None):
    """Copy only relevant project artifacts after one action, before the next edit."""
    root_path=Path(root).absolute();root=root_path.resolve()
    base=root_path/'session/visual';frame=base/'frames'/f'{index:05d}'
    if frame.exists():return read(frame/'frame.json')
    frame.mkdir(parents=True)
    from tools.library import project_source_paths
    files={};total=0
    for item in project_source_paths(root):
        total+=item.stat().st_size
        if total>128*1024*1024:raise ValueError('Visual snapshot exceeds 128 MiB; CAD unchanged')
        relative=Path(item).resolve().relative_to(root);target=frame/'source'/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(item,target);target.chmod(0o644);files[str(relative)]=hashlib.sha256(target.read_bytes()).hexdigest()
    from tools import workspace
    try:checks=workspace.project_status().get('checks',{})
    except Exception:checks={}
    reports=[]
    for entry in sorted(checks.values(),key=lambda x:Path(root_path/x['path']).stat().st_mtime if x.get('path') and (root_path/x['path']).is_file() else 0):
        if entry.get('path'):reports.append((read(root_path/entry['path'],{}),entry.get('fresh',False)))
    observation=observation or {}
    try:result=json.loads(observation.get('stdout',''))
    except ValueError:result={}
    if not isinstance(result,dict):result={}
    data=dict(capture_schema_version=3,index=index,tool=tool,arguments=arguments,timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              files=files,checks=report_checks(checks,reports),
              returncode=observation.get('returncode',0),elapsed_s=observation.get('elapsed_s'),
              result_status=result.get('status'),issues=result.get('issues',[]),
              submitted_claim=result.get('submitted'),claim_saved=result.get('saved'),claim_evidence=result.get('evidence'),
              note='Captured after the named action; native CAD is shown only when a corresponding file exists')
    write(frame/'frame.json',data)
    return data


def export(command,cwd):
    process=subprocess.run(command,cwd=cwd,text=True,capture_output=True,timeout=60)
    if process.returncode:raise RuntimeError(process.stderr[-1500:] or process.stdout[-1500:])


def export_final(root,out):
    """Export the submitted revision for viewing; never alter acceptance scores."""
    from tools import library,mechanical
    root=Path(root).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    board=root/'board.kicad_pcb';sch=root/'board.kicad_sch';outputs={};errors=[]
    before={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in library.project_source_paths(root)}
    # Make the portable/ephemeral boundary explicit. KiCad creates
    # ``board.kicad_prl`` and a ``~*.lck`` file when its GUI opens a project;
    # those are per-machine window state and a lock, not CAD inputs. The CAD
    # rules are embedded in board.kicad_pro unless a project deliberately
    # supplies an external board.kicad_dru.
    project_manifest={
        'schema_version': 1,
        'portable_source_files': sorted(before),
        'published_source_root': 'The same portable files are copied below this directory so the project can be opened directly.',
        'required_kicad_files': ['board.kicad_pro','board.kicad_sch','board.kicad_pcb'],
        'missing_required_kicad_files': [name for name in ('board.kicad_pro','board.kicad_sch','board.kicad_pcb') if name not in before],
        'optional_project_rules': 'board.kicad_dru' if 'board.kicad_dru' in before else 'embedded in board.kicad_pro',
        'gui_state_excluded': ['board.kicad_prl'],
        'lock_files_excluded': ['~board.kicad_pro.lck'],
        'note': 'GUI state and lock files are regenerated by KiCad and are intentionally absent from cad.zip.',
    }
    write(out/'project-files.json',project_manifest)
    # Keep a directly-openable project next to the previews.  Previously the
    # source files existed only inside cad.zip, which made the published
    # directory appear to be missing one of the three KiCad inputs.  Copying
    # the immutable captured sources is safe and also preserves nested local
    # symbol, footprint and 3D dependencies for a portable project.
    for name in before:
        source = root / name
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if board.is_file():
        try:
            from tools import pcb_editor as ed
            export(['kicad-cli','pcb','export','svg','--layers',pcb_view_layers(ed.load(board)),
                    '--mode-single','--page-size-mode','2','--exclude-drawing-sheet','--output',str(out/'board.svg'),str(board)],root)
            outputs['pcb']={'file':'board.svg','status':'PASS'}
        except (OSError,RuntimeError,subprocess.TimeoutExpired) as e:errors.append(str(e))
        for key,layers in [('pcb_front','F.Cu,F.SilkS,Edge.Cuts'),('pcb_back','B.Cu,B.SilkS,Edge.Cuts')]:
            try:
                export(['kicad-cli','pcb','export','svg','--layers',layers,'--mode-single','--page-size-mode','2',
                        '--exclude-drawing-sheet','--output',str(out/(key+'.svg')),str(board)],root)
                outputs[key]={'file':key+'.svg','status':'PASS'}
            except (OSError,RuntimeError,subprocess.TimeoutExpired) as e:outputs[key]={'status':'UNKNOWN','issues':[str(e)]}
        jobs=[('glb','glb','isometric'),('step','step','isometric'),('render_top','png','top'),
              ('render_bottom','png','bottom'),('render_isometric','png','isometric')]
        for key,fmt,view in jobs:
            try:
                name=('board' if fmt!='png' else key)+'.'+fmt
                r=mechanical.export(board,out/name,fmt,view=view);write(out/(key+'-export.json'),r)
                outputs[key]={'file':name if r.get('file') else None,'status':r['status'],'issues':r['issues']}
            except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as e:outputs[key]={'status':'UNKNOWN','issues':[str(e)]}
    if sch.is_file():
        try:
            target=out/'schematic';target.mkdir()
            export(['kicad-cli','sch','export','svg','--exclude-drawing-sheet','--no-background-color','--output',str(target)+'/',str(sch)],root)
            outputs['schematic']={'files':[str(p.relative_to(out)) for p in sorted(target.glob('*.svg'))]}
        except (OSError,RuntimeError,subprocess.TimeoutExpired) as e:errors.append(str(e))
    with zipfile.ZipFile(out/'cad.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in before:archive.write(root/name,name)
        archive.write(out/'project-files.json','project-files.json')
    outputs['cad']={'file':'cad.zip','manifest':'project-files.json','status':'UNKNOWN' if project_manifest['missing_required_kicad_files'] else 'PASS'}
    after={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in library.project_source_paths(root)}
    if before!=after:errors.append('Candidate inputs changed during export')
    data={'sources':before,'outputs':outputs,'issues':errors,'integrity':'PASS' if before==after else 'UNKNOWN',
          'scope':'Final submitted CAD appearance; missing bodies remain UNKNOWN and scores are unchanged'}
    write(out/'exports.json',data);return data


RENDER_SCHEMA_VERSION=3


def render_frame(frame,out,cache):
    data=read(frame/'frame.json');source=frame/'source'
    view=dict(data);views={};errors=[]
    if data.get('capture_schema_version',1)<2:
        # Earlier preview capture could pick a nested postroute analyses result.
        # Recover only from retained explicit claim evidence; otherwise unknown.
        view['checks']=dict(data.get('checks',{}))
        statuses=(data.get('claim_evidence') or {}).get('analysis_statuses',{})
        if statuses:
            from tools import validation as v
            view['checks']['analyses']={'status':v.aggregate({k:v.result(x,k) for k,x in statuses.items()})['status'],'fresh':True}
        elif view['checks'].get('analyses',{}).get('status')=='NOT_APPLICABLE':
            view['checks']['analyses']={'status':'UNKNOWN','fresh':False}
    spec=read(source/'spec.json',{})
    view['components']=[{k:p.get(k) for k in ('ref','symbol','value','footprint','nets','at')} for p in spec.get('parts',[])]
    view['board']=spec.get('board',{})
    view['net_count']=len(spec.get('nets',{}))
    board=source/'board.kicad_pcb';schematic=source/'board.kicad_sch'
    for kind,path in [('schematic',schematic),('pcb',board)]:
        if not path.is_file():continue
        # Hierarchical dependencies affect a schematic render too.
        relevant={k:v for k,v in data['files'].items() if (k.endswith('.kicad_sch') if kind=='schematic' else k in ('board.kicad_pcb','board.kicad_pro'))}
        digest=hashlib.sha256(json.dumps([kind,relevant,'enabled-copper-v3'],sort_keys=True).encode()).hexdigest()
        asset=out/'assets'/digest;asset.mkdir(parents=True,exist_ok=True)
        if digest not in cache:
            try:
                if kind=='schematic':
                    export(['kicad-cli','sch','export','svg','--exclude-drawing-sheet','--no-background-color','--output',str(asset)+'/',str(path)],source)
                    svgs=sorted(asset.glob('*.svg'))
                    if not svgs:raise RuntimeError('KiCad did not export a schematic SVG')
                    cache[digest]={'sheets':[{'name':p.stem,'url':str(p.relative_to(out))} for p in svgs]}
                else:
                    from tools import pcb_editor as ed
                    b=ed.load(path)
                    for layer,layers in [('all',pcb_view_layers(b)),('front','F.Cu,F.SilkS,Edge.Cuts'),('back','B.Cu,B.SilkS,Edge.Cuts')]:
                        export(['kicad-cli','pcb','export','svg','--layers',layers,'--mode-single','--page-size-mode','2','--exclude-drawing-sheet','--output',str(asset/(layer+'.svg')),str(path)],source)
                    tracks=list(b.GetTracks())
                    cache[digest]={layer:str((asset/(layer+'.svg')).relative_to(out)) for layer in ('all','front','back')}
                    cache[digest]['metrics']={'components':len(list(b.GetFootprints())),'tracks':sum(not isinstance(t,ed.pcbnew.PCB_VIA) for t in tracks),'vias':sum(isinstance(t,ed.pcbnew.PCB_VIA) for t in tracks),'zones':len(list(b.Zones()))}
            except (OSError,RuntimeError,subprocess.TimeoutExpired) as error:
                errors.append(kind+': '+str(error));continue
        views[kind]=cache[digest]
    if board.is_file():
        from tools import mechanical
        relevant={k:h for k,h in data['files'].items() if k in ('board.kicad_pcb','board.kicad_pro') or k.startswith('.pcb/3dmodels/')}
        digest=hashlib.sha256(json.dumps(['3d',relevant],sort_keys=True).encode()).hexdigest()
        if digest not in cache:
            asset=out/'assets'/digest;asset.mkdir(parents=True,exist_ok=True);target=asset/'board.glb'
            try:
                # Never silently render a newer system/absolute model as an old
                # snapshot's body; every dependency must have been captured.
                for record in mechanical.inventory(board):
                    for model in record['models']:
                        path=Path(model['path'])
                        # Official KiCad libraries inside the frozen image are
                        # immutable across a session; render them without capture.
                        if str(path).startswith('/usr/share/kicad/'):continue
                        # A body that exists nowhere cannot be silently substituted;
                        # mechanical.export reports it and renders the rest.
                        if not model['exists']:continue
                        if not path.is_relative_to(source.resolve()) or str(path.relative_to(source.resolve())) not in data['files']:
                            raise ValueError('3D dependency was not captured for this revision: '+model['name'])
                # Cached exports belong to this captured revision, never a later board.
                report=read(asset/'export.json')
                if report is None:
                    if target.exists():target.unlink()
                    report=mechanical.export(board,target,'glb');write(asset/'export.json',report)
                cache[digest]={'url':str(target.relative_to(out)) if target.is_file() else None,
                               'status':report['status'],'issues':report['issues'],
                               'scope':'Nominal 3D preview; mechanical acceptance is a separate analysis'}
            except (OSError,ValueError,KeyError) as error:
                cache[digest]={'url':None,'status':'UNKNOWN','issues':[str(error)]}
        views['model3d']=cache[digest]
    view['views']=views;view['render_errors']=errors;view['render_schema_version']=RENDER_SCHEMA_VERSION
    return view


def render(root,out,title,watch=False):
    root=Path(root).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    assets=Path(__file__).resolve().parent/'data/viewer'
    for name in ('index.html','app.js','style.css','assembly.html','assembly.js'):shutil.copy2(assets/name,out/name)
    shutil.copytree(assets/'vendor',out/'vendor',dirs_exist_ok=True)
    existing=read(out/'data.json',{})
    frames={r['index']:r for r in existing.get('frames',[])};cache={}
    def publish(complete=False):
        stopped=read(root/'session/result.json',{})
        write(out/'data.json',dict(title=title,source='Native KiCad artifacts',
              model=stopped.get('model') or os.environ.get('PCB_VIEWER_MODEL',''),
              state=stopped.get('reason','RUNNING' if watch else 'RECORDED'),
              render_complete=complete,frames=[frames[k] for k in sorted(frames)],
              updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              note='Captured revisions; rendering is not engineering acceptance. Pending views are not missing CAD.'))
    while True:
        markers=sorted((root/'session/visual/frames').glob('*/frame.json'))
        # Publish every recorded action before expensive exports. An interrupted
        # renderer still leaves a usable timeline and can resume pending views.
        for marker in markers:
            current=read(marker)
            frames.setdefault(current['index'],dict(current,views={},render_errors=[],render_schema_version=0))
        restore_recorded_checks(root,frames)
        publish()
        for marker in markers:
            current=read(marker)
            if current['index'] not in frames or frames[current['index']].get('render_schema_version',1)<RENDER_SCHEMA_VERSION:
                previous=frames.get(current['index'],{})
                rendered=render_frame(marker.parent,out,cache)
                if previous.get('checks_display_source'):
                    rendered['checks']=previous['checks']
                    rendered['checks_display_source']=previous['checks_display_source']
                frames[current['index']]=rendered
                publish()
        stopped=read(root/'session/result.json',{})
        publish(complete=True)
        if not watch or stopped:return
        time.sleep(2)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--title',default='PCB 设计过程');parser.add_argument('--watch',action='store_true')
    args=parser.parse_args();render(args.workspace,args.out,args.title,args.watch)
