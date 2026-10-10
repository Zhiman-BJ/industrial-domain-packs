"""Post-routing geometry checks against explicit project requirements.

No net-name heuristics or universal length/skew limits. Copper length excludes
vertical via delay, package delay and zone propagation; SI/PI/EMC require their
own source-bound evidence, not a DRC pass.
"""
from pathlib import Path
import json
import math
from tools import validation as v
from tools.sexpr import parse, children, child


def _positive(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>=0


def measure_board(board):
    """Measure native track/arc lengths, vias, electrical pads and placements."""
    from tools import pcb_editor as ed
    b=ed.load(board);nets={};positions={}
    def net(name):return nets.setdefault(name,{'copper_length_mm':0.,'vias':0,'pads':[],'segments':0,'endpoint_branch_detected':False})
    degrees={}
    for track in b.GetTracks():
        name=str(track.GetNetname());n=net(name)
        if isinstance(track,ed.pcbnew.PCB_VIA):n['vias']+=1;continue
        n['copper_length_mm']+=float(ed.pcbnew.ToMM(track.GetLength()));n['segments']+=1
        for p in (track.GetStart(),track.GetEnd()):
            key=(name,p.x,p.y,track.GetLayer());degrees[key]=degrees.get(key,0)+1
    for (name,x,y,layer),degree in degrees.items():
        if degree>2:net(name)['endpoint_branch_detected']=True
    for fp in b.GetFootprints():
        positions[str(fp.GetReference())]={'x_mm':float(ed.pcbnew.ToMM(fp.GetPosition().x)),
                'y_mm':float(ed.pcbnew.ToMM(fp.GetPosition().y)), 'rotation_deg':fp.GetOrientationDegrees(),
                'side':'bottom' if fp.IsFlipped() else 'top','locked':fp.IsLocked()}
        for pad in fp.Pads():
            if str(pad.GetNetname()):net(str(pad.GetNetname()))['pads'].append(f'{fp.GetReference()}:{pad.GetNumber()}')
    for n in nets.values():n['pads']=sorted(set(n['pads']))
    tree=parse(Path(board).read_text());zones=[]
    net_names={str(n[1]):str(n[2]) for n in children(tree,'net') if len(n)>2}
    for z in children(tree,'zone'):
        if child(z,'keepout'):continue
        name=child(z,'net_name');code=child(z,'net');layer=child(z,'layer');layers=child(z,'layers')
        zones.append({'net':str(name[1]) if name else net_names.get(str(code[1]),'') if code else '',
                      'layers':[str(layer[1])] if layer else [str(x) for x in layers[1:]] if layers else [],
                      'filled':bool(children(z,'filled_polygon'))})
    outline=ed.pcbnew.SHAPE_POLY_SET();bounds=None
    if ed.board_polygon_outlines(b, outline):
        box=outline.BBox()
        bounds=dict(xmin_mm=ed.to_mm(box.GetX()),ymin_mm=ed.to_mm(box.GetY()),
                    width_mm=ed.to_mm(box.GetWidth()),height_mm=ed.to_mm(box.GetHeight()))
    return {'nets':nets,'placements':positions,'zones':zones,'bounds':bounds,'board_sha256':v.file_hash(board),
            'connectivity_status':'NOT_CHECKED',
            'measurement_scope':'Track lengths and same-layer coincident endpoint degree only. endpoint_branch_detected=false does not prove pad connectivity or absence of T junctions, pad/via/zone branches. Use native DRC for copper shorts/unconnected items; schematic/PCB parity separately checks declared pin/pad/net membership.'}


def check_constraints(measured, requirements):
    """Check declared copper lengths, via budgets, pair skew, placement and filled zones."""
    requirements=requirements or {}
    issues=[];unknown=[];nets=measured['nets'];observed={};evaluated=0
    supported={'nets','differential_pairs','placements','placement_regions','zones','analyses'}
    if set(requirements)-supported:unknown.append('Unsupported postroute requirements: '+str(sorted(set(requirements)-supported)))
    for name,limits in requirements.get('nets',{}).items():
        n=nets.get(name)
        if n is None:issues.append('Required net missing: '+name);continue
        for key,value in limits.items():
            if key not in ('min_copper_length_mm','max_copper_length_mm','max_vias'):
                unknown.append(f'{name}: unsupported metric {key}');continue
            if not _positive(value):issues.append(f'{name}: invalid limit {key}');continue
            evaluated+=1
            actual=n['copper_length_mm' if key.endswith('copper_length_mm') else 'vias']
            if (actual<value if key.startswith('min_') else actual>value):issues.append(f'{name}: {key} violated ({actual}, limit {value})')
    for pair in requirements.get('differential_pairs',[]):
        p,n=pair['p'],pair['n'];a,b=nets.get(p),nets.get(n)
        if not a or not b:issues.append(f'{p}/{n}: pair net missing');continue
        if any(len(x['pads'])!=2 or x.get('endpoint_branch_detected',x.get('branches',True)) or not x['segments'] for x in (a,b)):
            unknown.append(f'{p}/{n}: copper skew check supports routed two-pad nets without branches only');continue
        limit=pair.get('max_copper_skew_mm')
        if not _positive(limit):unknown.append(f'{p}/{n}: explicit copper skew limit required');continue
        evaluated+=1
        skew=abs(a['copper_length_mm']-b['copper_length_mm']);observed[p+'/'+n]=skew
        if skew>limit:issues.append(f'{p}/{n}: copper length mismatch {skew}mm > {limit}mm')
        if set(pair)-{'p','n','max_copper_skew_mm'}:unknown.append(f'{p}/{n}: unsupported pair constraint; impedance/coupling/delay need an analysis backend')
    for rule in requirements.get('placements',[]):
        actual=measured['placements'].get(rule['ref'])
        if actual is None:issues.append('Required placed component missing: '+rule['ref']);continue
        tolerance=rule.get('tolerance_mm',0.01)
        if not _positive(tolerance):issues.append('Invalid position tolerance');continue
        evaluated+=len(set(rule)&{'x_mm','y_mm','rotation_deg','side','locked'})
        for k in ('x_mm','y_mm'):
            if k in rule and abs(actual[k]-rule[k])>tolerance:issues.append(f"{rule['ref']}: {k} changed")
        if 'rotation_deg' in rule and abs((actual['rotation_deg']-rule['rotation_deg']+180)%360-180)>rule.get('angle_tolerance_deg',0.01):issues.append(rule['ref']+': rotation changed')
        for k in ('side','locked'):
            if k in rule and actual[k]!=rule[k]:issues.append(f"{rule['ref']}: {k} differs")
        if set(rule)-{'ref','x_mm','y_mm','rotation_deg','side','locked','tolerance_mm','angle_tolerance_deg'}:unknown.append(rule['ref']+': unsupported placement constraint')
    for rule in requirements.get('placement_regions',[]):
        if (not isinstance(rule,dict) or set(rule)-{'ref','x_fraction','y_fraction','side'} or
                not isinstance(rule.get('ref'),str) or not any(k in rule for k in ('x_fraction','y_fraction'))):
            unknown.append('Placement region requires a reference and fractional board-axis bounds');continue
        actual=measured['placements'].get(rule['ref']);bounds=measured.get('bounds')
        if actual is None:issues.append('Required placed component missing: '+rule['ref']);continue
        if not bounds:unknown.append('Native board outline unavailable for placement region');continue
        for axis,span in [('x','width_mm'),('y','height_mm')]:
            limits=rule.get(axis+'_fraction')
            if limits is None:continue
            if (not isinstance(limits,list) or len(limits)!=2 or not all(_positive(x) for x in limits) or
                    not 0<=limits[0]<=limits[1]<=1 or bounds[span]<=0):
                unknown.append(rule['ref']+': invalid fractional placement bounds');continue
            fraction=(actual[axis+'_mm']-bounds[axis+'min_mm'])/bounds[span]
            evaluated+=1
            if not limits[0]-1e-6<=fraction<=limits[1]+1e-6:issues.append(rule['ref']+': outside '+axis+' placement region')
        if 'side' in rule:
            if rule['side'] not in ('top','bottom'):unknown.append(rule['ref']+': invalid placement side')
            elif actual['side']!=rule['side']:issues.append(rule['ref']+': placement side differs')
    for rule in requirements.get('zones',[]):
        evaluated+=1
        matches=[z for z in measured['zones'] if z['net']==rule['net'] and rule['layer'] in z['layers']]
        if not matches:issues.append(f"Missing copper zone {rule['net']} on {rule['layer']}")
        elif any(not z['filled'] for z in matches):issues.append(f"Unfilled copper zone {rule['net']} on {rule['layer']}")
        if set(rule)-{'net','layer'}:unknown.append('Unsupported zone geometry requirement')
    for z in measured.get('zones',[]):
        if not z['filled']:issues.append('Non-keepout copper zone has no filled polygon: '+z['net'])
    if not evaluated:unknown.append('No geometric predicates to evaluate; empty declarations are not postroute verification')
    return v.result('FAIL' if issues else 'UNKNOWN' if unknown else 'PASS',
                    'declared geometry only; no SI/PI/EMC or thermal proof',issues+unknown, pair_copper_skew_mm=observed)


def check_analysis_evidence(requirements, board, base_dir):
    """Validate required external analysis measurements bound to this PCB revision.

Each report records method, producer, assumptions, board hash and numeric
measurements. This checks evidence consistency; it does not certify the solver.
"""
    checks={}
    for item in requirements:
        name=item['id'];path=Path(base_dir)/item.get('report','');assertions=item.get('assertions',{})
        if not path.is_file():checks[name]=v.result('UNKNOWN',name,['Required analysis report missing']);continue
        try:
            report=json.loads(path.read_text())
            if report.get('board_sha256')!=v.file_hash(board):
                checks[name]=v.result('UNKNOWN',name,['Analysis is for a different PCB revision']);continue
            if not assertions or any(not report.get(k) for k in ('method','producer','assumptions','measurements')):
                checks[name]=v.result('UNKNOWN',name,['Analysis method, producer, assumptions, measurements and acceptance bounds required']);continue
            bad=[];missing=[]
            for metric,limits in assertions.items():
                value=report['measurements'].get(metric)
                if not isinstance(value,(int,float)) or not math.isfinite(value):missing.append(metric);continue
                if not limits or set(limits)-{'min','max'} or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in limits.values()):missing.append(metric+': invalid acceptance bound');continue
                if ('min' in limits and value<limits['min']) or ('max' in limits and value>limits['max']):bad.append(metric+': outside acceptance range')
            checks[name]=v.result('FAIL' if bad else 'UNKNOWN' if missing else 'PASS',name,bad+missing,report_sha256=v.file_hash(path),method=report['method'])
        except (ValueError,TypeError,KeyError) as e:checks[name]=v.result('UNKNOWN',name,[str(e)])
    return v.aggregate(checks) if checks else v.result('NOT_APPLICABLE','external analyses',['No external analyses declared'])


def check_postroute(board, spec, out_dir):
    """Measure current native board and persist geometry/evidence check results."""
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    try:
        measured=measure_board(board)
        requirements=spec.get('postroute')
        checks={'geometry':check_constraints(measured,requirements),
                'analyses':check_analysis_evidence((requirements or {}).get('analyses',[]),board,Path(board).parent)}
        r=v.aggregate(checks);r['measurements']=measured
    except Exception as e:r=v.result('UNKNOWN','postroute',[str(e)])
    (out/'postroute.json').write_text(json.dumps(r,indent=2));return r
