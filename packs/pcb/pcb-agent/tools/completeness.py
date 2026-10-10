"""General environment checks; no benchmark tasks, model CLIs or private catalog."""
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tools import library,knowledge


def inspect_environment():
    checks={}
    for exe in ('kicad','pcbnew','eeschema','Xvfb','x11vnc','websockify','pcb-gui'):
        checks['gui:'+exe]={'status':'PASS' if shutil.which(exe) else 'UNKNOWN'}
    shapes=Path('/usr/share/kicad/3dmodels')
    count=sum(1 for p in shapes.glob('*.3dshapes/*') if p.suffix.lower() in ('.step','.stp','.wrl'))
    checks['standard_3d_bodies']={'status':'PASS' if count else 'UNKNOWN','files':count,
                                  'scope':'Nominal geometry availability, not MPN qualification'}
    for mod in ('design','validation','knowledge','schematic','bootstrap','pcb_editor','kicad_cli','loop','verify','trajectory','library','intake','routing','postroute','release','model_runner','analysis','board_ops','workspace','acceptance','agent_session'):
        try:importlib.import_module('tools.'+mod);checks['import:'+mod]={'status':'PASS'}
        except Exception as e:checks['import:'+mod]={'status':'UNKNOWN','reason':str(e)}
    executable_candidates={'kicad-cli':['/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli','/Volumes/KiCad/KiCad/KiCad.app/Contents/MacOS/kicad-cli'], 'ngspice':[]}
    for exe in ('kicad-cli','ngspice'):
        path=shutil.which(exe) or next((p for p in executable_candidates.get(exe,[]) if Path(p).is_file()),None)
        if not path:checks[exe]={'status':'UNKNOWN','reason':'not installed'};continue
        try:
            r=subprocess.run([path,'--version'],capture_output=True,text=True,timeout=30)
            checks[exe]={'status':'PASS' if r.returncode==0 else 'UNKNOWN','version':(r.stdout+r.stderr)[:300]}
        except Exception as e:checks[exe]={'status':'UNKNOWN','reason':str(e)}
    try:
        import pcbnew
        checks['pcbnew']={'status':'PASS','version':pcbnew.GetBuildVersion()}
    except Exception as e:checks['pcbnew']={'status':'UNKNOWN','reason':str(e)}
    for kind,dirs,glob in [('symbols',library.symbol_dirs(),'*.kicad_sym'),('footprints',library.footprint_dirs(),'*.pretty/*.kicad_mod')]:
        n=sum(1 for d in dirs for _ in Path(d).glob(glob));checks[kind]={'status':'PASS' if n else 'UNKNOWN','files':n,'paths':dirs}
    try:checks['rules']={'status':'PASS','revision':knowledge.registry()['revision']}
    except Exception as e:checks['rules']={'status':'UNKNOWN','reason':str(e)}
    return {'status':'PASS' if all(c['status']=='PASS' for c in checks.values()) else 'UNKNOWN','scope':'dependency availability, not design correctness','checks':checks}


if __name__=='__main__':
    r=inspect_environment();print(json.dumps(r,indent=2));raise SystemExit(0 if r['status']=='PASS' else 2)
