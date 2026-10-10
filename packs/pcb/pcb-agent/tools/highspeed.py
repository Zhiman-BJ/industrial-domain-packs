"""Explicit differential paths and bounded copper-length tuning with native DRC."""
from pathlib import Path
import math,shutil
from tools import pcb_editor as ed,board_ops as ops,validation as v,kicad_cli as kc,library,postroute


def _xy(point):return [ed.to_mm(point.x),ed.to_mm(point.y)]


def _simple(points,width,clearance):
    from shapely.geometry import LineString
    line=LineString(points)
    if not line.is_simple:raise ValueError('Path self-intersects')
    edges=[LineString([a,b]) for a,b in zip(points,points[1:])]
    for i,a in enumerate(edges):
        for b in edges[i+2:]:
            if a.distance(b)<width+clearance-1e-6:raise ValueError('Nonadjacent same-net copper would touch or violate tuning spacing')


def _errors(report):
    if not report.get('available') or not report.get('report_valid'):raise ValueError('Native DRC unavailable')
    return {(x['type'],tuple(sorted(str(p.get('uuid')) for p in x['items']))):x for x in report['violations']
            if x['severity']=='error' and x['group']!='unconnected_items'}


def _attempt(source,out,edit):
    source=Path(source).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    before=v.file_hash(source);library.copy_project_libraries(source.parent,out)
    for ext in ('.kicad_sch','.kicad_pro','.kicad_dru'):
        p=source.with_suffix(ext)
        if p.is_file():shutil.copy2(p,out/p.name)
    for name in ('sym-lib-table','fp-lib-table'):
        if (source.parent/name).is_file():shutil.copy2(source.parent/name,out/name)
    baseline=kc.drc(source,out/'before-drc.json',schematic_parity=False)
    old_errors=_errors(baseline)
    board=ed.load(source);details=edit(board)
    candidate=out/source.name;ed.save(board,candidate)
    report=kc.drc(candidate,out/'after-drc.json',schematic_parity=False)
    errors=_errors(report);new=[x for key,x in errors.items() if key not in old_errors]
    result=v.result('FAIL' if new else 'PASS','local copper edit; final DRC, parity and analyses still required',new,
                    candidate=str(candidate),source_sha256=before,drc=report,**details)
    if v.file_hash(source)!=before:return v.result('UNKNOWN','source changed during routing')
    return result


def differential(board,out,p_net,n_net,centerline,width_mm,gap_mm,layer,terminals):
    """Route one pair along a caller-selected corridor; no obstacle search or via fanout."""
    from shapely.geometry import LineString
    ops._points(centerline);ops._positive(width_mm);ops._positive(gap_mm)
    if p_net==n_net:raise ValueError('A pair requires two distinct nets')
    def edit(b):
        ops._layer(b,layer,True);pads={fp.GetReference()+':'+p.GetNumber():p for fp in b.GetFootprints() for p in fp.Pads()}
        if set(terminals)!={'p','n'} or any(len(terminals[k])!=2 for k in terminals):raise ValueError('terminals requires p/n lists of two unique ref:pad anchors')
        if any(t.GetNetname() in (p_net,n_net) for t in b.GetTracks()):raise ValueError('Pair already has copper; explicitly remove the affected path first')
        if any(z.GetNetname() in (p_net,n_net) and not z.GetIsRuleArea() for z in b.Zones()):raise ValueError('Pair net zones need a different routing model')
        paths={};ids=[];spine=LineString(centerline)
        if not spine.is_simple:raise ValueError('Centerline crosses itself')
        for polarity,net,side in [('p',p_net,'left'),('n',n_net,'right')]:
            offset=spine.parallel_offset((width_mm+gap_mm)/2,side,join_style=2,mitre_limit=2)
            if offset.geom_type!='LineString':raise ValueError('Offset corridor collapsed')
            points=[list(p) for p in offset.coords]
            pair=[pads[p] for p in terminals[polarity]]
            if any(p.GetNetname()!=net or not p.IsOnLayer(b.GetLayerID(layer)) for p in pair):raise ValueError('Terminal net or layer mismatch')
            a,c=map(lambda p:_xy(p.GetPosition()),pair)
            if math.dist(points[-1],a)<math.dist(points[0],a):points.reverse()
            if math.dist(points[0],a)>.002 or math.dist(points[-1],c)>.002:raise ValueError('Offset endpoints must match actual pad centers; route fanout explicitly')
            points[0]=a;points[-1]=c;_simple(points,width_mm,gap_mm)
            paths[polarity]=points
        if LineString(paths['p']).distance(LineString(paths['n']))<width_mm+gap_mm-1e-6:raise ValueError('Pair gap collapses at a bend')
        for polarity,net in [('p',p_net),('n',n_net)]:
            for a,c in zip(paths[polarity],paths[polarity][1:]):ids.append(ed.add_track(b,*a,*c,net,width_mm,layer).m_Uuid.AsString())
        return dict(added_ids=ids,paths=paths,lengths_mm={k:LineString(p).length for k,p in paths.items()},gap_mm=gap_mm)
    return _attempt(board,out,edit)


def tune(board,out,item_id,target_length_mm,pitch_mm,max_amplitude_mm,side):
    """Replace one straight segment by rectangular trombones to meet net copper length."""
    from shapely.geometry import LineString
    for x in (target_length_mm,pitch_mm,max_amplitude_mm):ops._positive(x)
    if side not in ('left','right'):raise ValueError('side must be left/right relative to segment direction')
    def edit(b):
        segment=next((t for t in b.GetTracks() if t.m_Uuid.AsString()==item_id),None)
        if segment is None or segment.GetClass()!='PCB_TRACK' or segment.IsLocked():raise ValueError('Unlocked straight segment UUID required')
        net=segment.GetNetname();layer=b.GetLayerName(segment.GetLayer());width=ed.to_mm(segment.GetWidth())
        tracks=[t for t in b.GetTracks() if t.GetNetname()==net]
        if any(t.GetClass()!='PCB_TRACK' or t.GetLayer()!=segment.GetLayer() for t in tracks):raise ValueError('Tune supports straight tracks on one layer; vias/arcs need delay extraction')
        if any(z.GetNetname()==net and not z.GetIsRuleArea() for z in b.Zones()):raise ValueError('Zone contact makes centerline length ambiguous')
        endpoints={};pads=[p for f in b.GetFootprints() for p in f.Pads() if p.GetNetname()==net]
        for t in tracks:
            for p in (t.GetStart(),t.GetEnd()):endpoints[(p.x,p.y)]=endpoints.get((p.x,p.y),0)+1
        if len(pads)!=2 or sorted(endpoints.values()).count(1)!=2 or max(endpoints.values())>2:raise ValueError('Tune requires one unbranched two-pad route')
        total=sum(ed.to_mm(t.GetLength()) for t in tracks);extra=target_length_mm-total
        if extra<=.001:raise ValueError('Target must exceed current copper length')
        a,c=_xy(segment.GetStart()),_xy(segment.GetEnd());length=math.dist(a,c)
        clearance=ed.to_mm(b.GetDesignSettings().m_MinClearance)
        if pitch_mm<=width+clearance:raise ValueError('Pitch must exceed track width plus declared clearance')
        cycles=int(length/(2*pitch_mm))
        if cycles<1:raise ValueError('Segment too short for this pitch')
        amplitude=extra/(2*cycles)
        if amplitude>max_amplitude_mm:raise ValueError('Target exceeds the selected tuning corridor')
        ux,uy=(c[0]-a[0])/length,(c[1]-a[1])/length;sign=1 if side=='left' else -1
        local=[[0,0]]
        for i in range(cycles):
            start=(2*i+.5)*pitch_mm
            local += [[start,0],[start,amplitude],[start+pitch_mm,amplitude],[start+pitch_mm,0]]
        local.append([length,0]);points=[[a[0]+x*ux-y*uy*sign,a[1]+x*uy+y*ux*sign] for x,y in local]
        _simple(points,width,clearance)
        # Native DRC ignores many same-net shortcuts; reject new contacts with
        # old nonterminal copper before relying on centerline measurements.
        route=LineString(points)
        for t in tracks:
            if t is segment or t.m_Uuid.AsString()==item_id:continue
            edge=LineString([_xy(t.GetStart()),_xy(t.GetEnd())])
            contact=route.buffer(width/2).intersection(edge.buffer(ed.to_mm(t.GetWidth())/2))
            if not contact.is_empty:
                from shapely.geometry import Point
                allowed=Point(a).buffer(width*2).union(Point(c).buffer(width*2))
                if not contact.difference(allowed).is_empty:raise ValueError('Tuning would shortcut existing same-net copper')
        b.Delete(segment);ids=[ed.add_track(b,*p,*q,net,width,layer).m_Uuid.AsString() for p,q in zip(points,points[1:])]
        measured=total-length+sum(math.dist(p,q) for p,q in zip(points,points[1:]))
        return dict(removed_ids=[item_id],added_ids=ids,net=net,previous_length_mm=total,copper_length_mm=measured,amplitude_mm=amplitude)
    return _attempt(board,out,edit)
