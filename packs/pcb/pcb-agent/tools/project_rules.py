"""Persist explicitly selected project geometry and net classes."""
import json,math
import shutil
from pathlib import Path
from tools import pcb_editor as ed


def validation_copy(root, destination, spec, name='board', custom_rules=None):
    """Private native-check copy with authoritative rules and no local waivers."""
    from tools import library
    root=Path(root);out=Path(destination);out.mkdir(parents=True,exist_ok=True)
    for filename in (name+'.kicad_sch',name+'.kicad_pcb',name+'.kicad_pro','sym-lib-table','fp-lib-table'):
        if (root/filename).is_file():shutil.copy2(root/filename,out/filename)
    library.copy_project_libraries(root,out)
    if (out/(name+'.kicad_pcb')).is_file():apply(out/(name+'.kicad_pcb'),spec)
    project_path=out/(name+'.kicad_pro')
    project=json.loads(project_path.read_text()) if project_path.is_file() else {}
    settings=project.setdefault('board',{}).setdefault('design_settings',{})
    settings.pop('drc_exclusions',None);settings.pop('rule_severities',None)
    project['erc']={}
    project_path.write_text(json.dumps(project,indent=2))
    if custom_rules:(out/(name+'.kicad_dru')).write_text(custom_rules)
    return out

def restore(board, project_path):
    """Restore project netclasses into pcbnew before DSN export (LoadBoard alone loses them)."""
    path=Path(project_path)
    if not path.is_file():return
    project=json.loads(path.read_text());settings=project.get('net_settings',{})
    classes={c['name']:c for c in settings.get('classes',[])}
    default=classes.get('Default')
    if not default:return
    rules=project.get('board',{}).get('design_settings',{}).get('rules',{})
    ed.set_design_rules(board,clearance_mm=default['clearance'],track_mm=default['track_width'],
                        via_mm=default['via_diameter'],drill_mm=default['via_drill'],
                        edge_mm=rules.get('min_copper_edge_clearance',.3),hole_mm=rules.get('min_hole_clearance',.25))
    import fnmatch
    for assignment in settings.get('netclass_patterns',[]):
        name=assignment['netclass'];cls=classes[name]
        nets=[str(n) for n in board.GetNetsByName().keys() if fnmatch.fnmatchcase(str(n),assignment['pattern'])]
        ed.set_net_class(board,name,nets,track_mm=cls['track_width'],clearance_mm=cls['clearance'],
                         via_mm=cls['via_diameter'],drill_mm=cls['via_drill'],
                         dp_width_mm=cls.get('diff_pair_width'),dp_gap_mm=cls.get('diff_pair_gap'))

def apply(pcb, spec):
    pcb=Path(pcb);out=pcb.parent;name=pcb.stem
    b=ed.load(pcb)
    rules=spec.get('constraints',{}).get('board_rules')
    if not rules:
        raise ValueError('Explicit board_rules required; no hidden factory defaults')
    expected={'clearance_mm','track_mm','via_mm','drill_mm','edge_mm','hole_mm'}
    if set(rules)!=expected or any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<=0 for x in rules.values()):
        raise ValueError(f'board_rules require positive values for {sorted(expected)}')
    if rules['via_mm'] <= rules['drill_mm']:
        raise ValueError('Via diameter must exceed drill diameter')
    ed.set_design_rules(b,**rules)
    for net,meta in spec.get('nets',{}).items():
        nr=meta.get('rules',{})
        if nr:ed.set_net_class(b,net,[net],**dict({'track_mm':rules['track_mm'],'clearance_mm':rules['clearance_mm'],'via_mm':rules['via_mm'],'drill_mm':rules['drill_mm']},**nr))
    ed.save(b,pcb)
    # Netclasses live in the project file; preserve them across CLI reloads.
    project=json.loads((out/f'{name}.kicad_pro').read_text())
    # KiCad rejects an incomplete class object and silently retains native
    # defaults. Supply its complete schema so CLI reloads honor selected rules.
    # Styling and unused geometry fields below are native schema defaults, not
    # a fabrication profile or a claim that these dimensions were qualified.
    default_class={
        'bus_width':12,'clearance':rules['clearance_mm'],
        'diff_pair_gap':.25,'diff_pair_via_gap':.25,'diff_pair_width':.2,
        'line_style':0,'microvia_diameter':.3,'microvia_drill':.1,
        'name':'Default','pcb_color':'rgba(0, 0, 0, 0.000)',
        'priority':2147483647,'schematic_color':'rgba(0, 0, 0, 0.000)',
        'track_width':rules['track_mm'],'via_diameter':rules['via_mm'],
        'via_drill':rules['drill_mm'],'wire_width':6,
    }
    project['net_settings']={'meta':{'version':4},'classes':[default_class], 'netclass_patterns':[]}
    for net,meta in spec.get('nets',{}).items():
        nr=meta.get('rules',{})
        if nr:
            cls=dict(project['net_settings']['classes'][0],name=net)
            aliases={'track_mm':'track_width','clearance_mm':'clearance','via_mm':'via_diameter','drill_mm':'via_drill','dp_width_mm':'diff_pair_width','dp_gap_mm':'diff_pair_gap'}
            cls.update({aliases[k]:val for k,val in nr.items()});project['net_settings']['classes'].append(cls)
            project['net_settings']['netclass_patterns'].append({'netclass':net,'pattern':net})
    project.setdefault('board',{}).setdefault('design_settings',{})['rules']={'min_clearance':rules['clearance_mm'],'min_track_width':rules['track_mm'],'min_via_diameter':rules['via_mm'],'min_through_hole_diameter':rules['drill_mm'],'min_copper_edge_clearance':rules['edge_mm'],'min_hole_clearance':rules['hole_mm']}
    (out/f'{name}.kicad_pro').write_text(json.dumps(project,indent=2))
    return rules
