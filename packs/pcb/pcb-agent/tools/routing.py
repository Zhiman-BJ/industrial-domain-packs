"""Obstacle-aware Freerouting adapter; native KiCad DRC remains the authority.

Use a pinned local JAR (FREEROUTING_JAR) and Java 25+ (JAVA). Work in an
isolated attempt, retain DSN/SES/logs, preserve the source, never accept a JAR
exit code or a nonempty session file as proof of connectivity.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
from tools import validation as v, kicad_cli as kc


def backend_info():
    """Report actual router/runtime identity, including JAR content hash."""
    jar = Path(os.environ.get('FREEROUTING_JAR', ''))
    java = os.environ.get('JAVA') or shutil.which('java')
    if not jar.is_file() or not java:
        return v.result('UNKNOWN', 'routing backend', ['Set FREEROUTING_JAR to a pinned JAR and JAVA to a compatible Java executable'])
    try:
        run = subprocess.run([java, '-version'], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return v.result('UNKNOWN', 'Java runtime', [str(e)])
    return v.result('PASS' if run.returncode == 0 else 'UNKNOWN', 'routing dependencies only',
                    jar=str(jar.resolve()), jar_sha256=v.file_hash(jar), java=java,
                    java_version=(run.stdout + run.stderr).strip())


def _cad_script(script, args, timeout=120):
    return subprocess.run([sys.executable, '-c', script, *map(str,args)],
                          capture_output=True, text=True, timeout=timeout)


def _snapshot(board):
    from tools import pcb_editor as ed
    b=ed.load(board)
    return {fp.GetReference(): {'at': [fp.GetPosition().x, fp.GetPosition().y],
                'rotation': fp.GetOrientationDegrees(), 'side': fp.GetLayer(),
                'value': fp.GetValue(), 'footprint': fp.GetFPIDAsString(),
                'pads': sorted((str(p.GetNumber()),str(p.GetNetname())) for p in fp.Pads())}
            for fp in b.GetFootprints()}


def route_board(board, out_dir, timeout_s=300, max_passes=10, jar=None):
    """Export DSN, run Freerouting, import SES, fill zones and run native DRC.

Returns a candidate board in a new directory even when DRC fails. The source
board is never overwritten; callers adopt a candidate only after acceptance.
Not a differential-pair/impedance/length-tuning solver; use postroute constraints.
"""
    if timeout_s <= 0 or max_passes < 1: raise ValueError('Positive timeout/pass budget required')
    info=backend_info() if jar is None else None
    if jar is not None:
        info=dict(backend_info(),jar=str(Path(jar).resolve()))
        if not Path(jar).is_file(): return v.result('UNKNOWN','router',['JAR missing'])
        info['jar_sha256']=v.file_hash(jar)
        if not info.get('java'): info['java']=os.environ.get('JAVA') or shutil.which('java')
        if info['java']: info['status']='PASS'
    if info['status']!='PASS':return info
    source=Path(board).resolve();out=Path(out_dir).resolve()
    if out.exists():raise FileExistsError('Routing attempt already exists: '+str(out))
    out.mkdir(parents=True)
    before=v.file_hash(source);candidate=out/source.name
    checks={};dsn=out/'input.dsn';ses=out/'output.ses'
    tracked={str(p):v.file_hash(p) for p in (source,source.with_suffix('.kicad_sch'),source.with_suffix('.kicad_pro'),source.with_suffix('.kicad_dru'),source.parent/'sym-lib-table',source.parent/'fp-lib-table') if p.is_file()}
    from tools import library
    library.copy_project_libraries(source.parent,out)
    for path in tracked:shutil.copy2(path,out/Path(path).name)
    def finish():
        if any(not Path(p).is_file() or v.file_hash(p)!=sha for p,sha in tracked.items()):
            checks['source_revision']=v.result('UNKNOWN','source revision',['Design changed while routing'])
        result=v.aggregate(checks)
        result.update(candidate=str(candidate),source_sha256=before,backend=info,
                      artifacts={p.name:v.file_hash(p) for p in out.iterdir() if p.is_file() and p.name!='routing.json'})
        (out/'routing.json').write_text(json.dumps(result,indent=2));return result
    try:
        snapshot=_snapshot(source)
        run=_cad_script("import pcbnew,sys; from tools.project_rules import restore; from pathlib import Path; b=pcbnew.LoadBoard(sys.argv[1]); restore(b,Path(sys.argv[1]).with_suffix('.kicad_pro')); ok=pcbnew.ExportSpecctraDSN(b,sys.argv[2]); sys.exit(0 if ok else 1)",[candidate,dsn])
        if run.returncode or not dsn.is_file():
            checks['dsn']=v.result('UNKNOWN','DSN export',[run.stderr]);return finish()
        # Disable API, analytics and GUI; isolate settings from the user's profile.
        argv=[info['java'],'-Djava.awt.headless=true','-jar',info['jar'],
              '-de',str(dsn),'-do',str(ses),'-mp',str(max_passes),'-mt','2','-da',
              '--gui.enabled=false','--api_server.enabled=false','--router.automatic_neckdown=false',
              '--user_data_path='+str(out/'router-settings')]
        env=dict(os.environ, FREEROUTING__GUI__ENABLED='false',
                 FREEROUTING__API_SERVER__ENABLED='false',FREEROUTING__ROUTER__AUTOMATIC_NECKDOWN='false')
        with (out/'router.log').open('w') as log:
            run=subprocess.run(argv,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout_s)
        checks['engine']=v.result('PASS' if run.returncode==0 and ses.is_file() and ses.stat().st_size else 'UNKNOWN',
                                  'router execution only',argv=argv,returncode=run.returncode)
        if checks['engine']['status']!='PASS':return finish()
        run=_cad_script("import pcbnew,sys; from pathlib import Path; from tools.project_rules import restore; b=pcbnew.LoadBoard(sys.argv[1]); restore(b,Path(sys.argv[1]).with_suffix('.kicad_pro')); ok=pcbnew.ImportSpecctraSES(b,sys.argv[2]); pcbnew.SaveBoard(sys.argv[1],b) if ok else None; sys.exit(0 if ok else 1)",[candidate,ses])
        if run.returncode:
            checks['ses']=v.result('UNKNOWN','SES import',[run.stderr]);return finish()
        checks['preserved_intent']=v.result('PASS' if _snapshot(candidate)==snapshot else 'FAIL',
                                           'placement, values, footprints and pad networks preserved')
        from tools import pcb_editor as ed
        from tools.project_rules import restore
        routed=ed.load(candidate);restore(routed,source.with_suffix('.kicad_pro'))
        # Some router pad exits are narrowed even with neckdown disabled.
        # Restore each explicitly selected minimum on the candidate and let
        # native DRC reject any resulting collision; never adopt thin escapes.
        widened=[]
        for track in routed.GetTracks():
            if isinstance(track,ed.pcbnew.PCB_VIA):continue
            minimum=max(routed.GetDesignSettings().m_TrackMinWidth,ed.mm(ed.net_class_width(routed,track.GetNetname())))
            if track.GetWidth()<minimum:
                widened.append({'id':track.m_Uuid.AsString(),'net':track.GetNetname(),'from_mm':ed.to_mm(track.GetWidth()),'to_mm':ed.to_mm(minimum)})
                track.SetWidth(minimum)
        if widened:ed.save(routed,candidate)
        checks['route_widths']=v.result('PASS','Selected minimum widths restored before native clearance checks',adjusted=widened)
        if routed.GetAreaCount():
            checks['zone_fill']=kc.fill_zones_file(candidate)
        # Native import/fill can serialize a default netclass on process exit.
        # DRC must evaluate exactly the source project, not that regenerated file.
        for suffix in ('.kicad_pro','.kicad_dru'):
            original=source.with_suffix(suffix)
            if original.is_file():shutil.copy2(original,candidate.with_suffix(suffix))
        checks['drc']=v.run_drc(candidate,out/'drc.json')
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as e:
        checks['execution']=v.result('UNKNOWN','routing execution',[str(e)])
    return finish()
