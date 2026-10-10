"""Small transactional PCB edit vocabulary using stable native item identifiers."""
import copy
import math
import re
from tools import pcb_editor as ed, design
from tools.argument_schema import InputError


def item_hashes(board_path):
    """Canonical native item bodies for explicit original-contract preservation."""
    import hashlib
    from pathlib import Path
    from tools.sexpr import parse,dump,child
    result={}
    for node in parse(Path(board_path).read_text()):
        if not isinstance(node,list):continue
        identity=child(node,'uuid')
        if identity:result[str(identity[1])]=hashlib.sha256(dump(node).encode()).hexdigest()
    return result

OPERATIONS=('track','arc','via','remove','track_width','pad_zone_connection','move','side','lock','outline','layers','zone','keepout','text','field')

PAD_ZONE_CONNECTIONS={'inherited':-1,'none':0,'thermal':1,'solid':2,'thermal_through_hole':3}

FIELDS={'field':{'ref','field','at','size_mm','rotation','visible'},'track':{'points','net','width_mm','layer'},
        'arc':{'start','mid','end','net','width_mm','layer'},'via':{'at','net','drill_mm','diameter_mm'},
        'remove':{'id'},'track_width':{'id','width_mm'},'pad_zone_connection':{'id','connection'},'move':{'ref','at','rotation'},
        'side':{'ref','side'},'lock':{'ref','locked'},'outline':{'points'},'layers':{'count'},
        'zone':{'points','net','layer','clearance_mm','thermal_gap_mm','thermal_spoke_mm'},
        'keepout':{'points','layer'},'text':{'text','at','layer','size_mm'}}


def check_outline(board, points):
    """Compare the declared straight polygon, allowing reversal and split edges."""
    from tools import validation as v
    points=_outline_points(points)
    from shapely.geometry import Polygon
    polygon=Polygon(points)
    if not polygon.is_valid or polygon.area<=0:
        return v.result('FAIL','polygon outline',['Declared outline is not a valid nonzero polygon'])
    actual_poly=ed.pcbnew.SHAPE_POLY_SET();expected_poly=ed.pcbnew.SHAPE_POLY_SET()
    # A temporary native BOARD can tear down project state used by the live
    # design. The expected shape is already an explicit validated polygon.
    expected_poly.NewOutline()
    for x,y in points:expected_poly.Append(ed.mm(x),ed.mm(y))
    if not ed.board_polygon_outlines(board, actual_poly):
        return v.result('FAIL','polygon outline',['Unresolvable outline'])
    actual_poly.BooleanXor(expected_poly)
    different=abs(actual_poly.Area())>ed.mm(.002)**2
    return v.result('FAIL' if different else 'PASS','declared polygon outline',
                    ['Actual board polygon differs from original outline'] if different else [])


def _positive(x):
    if type(x) not in (float,int) or not math.isfinite(x) or x<=0:raise ValueError('Positive finite dimension required')
    return x


def _require_item(items, ident):
    """Exact candidate UUID lookup; never resolve a prefix into an edit."""
    valid=isinstance(ident,str) and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',ident)
    if not valid or ident not in items:
        error=InputError('UNKNOWN_BOARD_ITEM' if valid else 'INVALID_ITEM_ID','arguments.item_id',ident,
            {'type':'string','format':'full existing KiCad UUID (8-4-4-4-12 hex digits)',
             'lookup':'inspect_board(include_copper=true) or a fresh run_drc report'},
            'Inspect the current candidate, select the intended object and copy its complete UUID. Prefixes are not accepted; deleted/stale objects must be inspected again.')
        error.operation_executed=False
        raise error
    return items[ident]


def _points(points, minimum=2):
    if not isinstance(points,list) or len(points)<minimum:raise ValueError('Too few points')
    for pair in points:
        if len(pair)!=2 or any(type(x) not in (float,int) or not math.isfinite(x) for x in pair):raise ValueError('Finite [x,y] pairs required')
    return points


def _layer(board,name,copper=False):
    lid=board.GetLayerID(name)
    if lid<0 or not board.IsLayerEnabled(lid) or (copper and not ed.pcbnew.IsCopperLayer(lid)):raise ValueError('Invalid or disabled layer: '+name)
    return lid


def _outline_points(points):
    points=_points(points,3)
    if points[0]==points[-1]:points=points[:-1]
    if len(set(map(tuple,points)))!=len(points) or len(points)<3:raise ValueError('Outline needs distinct vertices')
    return points


def set_outline(board, points):
    """Replace Edge.Cuts with an explicit polygon; native DRC checks validity."""
    points=_outline_points(points)
    for item in list(board.GetDrawings()):
        if item.GetLayer()==ed.pcbnew.Edge_Cuts:board.Delete(item)
    for a,b in zip(points,points[1:]+points[:1]):
        item=ed.pcbnew.PCB_SHAPE(board);item.SetShape(ed.pcbnew.SHAPE_T_SEGMENT)
        item.SetStart(ed.V(*a));item.SetEnd(ed.V(*b));item.SetLayer(ed.pcbnew.Edge_Cuts);item.SetWidth(ed.mm(.05));board.Add(item)


def apply(board,spec,operations):
    """Apply all operations in memory; caller saves only after the entire batch succeeds."""
    updated=copy.deepcopy(spec);changes=[]
    for operation in operations:
        op=dict(operation);kind=op.pop('operation')
        if kind not in OPERATIONS:raise ValueError('Unknown PCB operation: '+kind)
        if set(op)-FIELDS[kind]:raise ValueError('Unknown parameters for '+kind+': '+', '.join(sorted(set(op)-FIELDS[kind])))
        nets=board.GetNetsByName()
        parts={fp.GetReference():fp for fp in board.GetFootprints()}
        items={str(item.m_Uuid.AsString()):item for item in [*board.GetTracks(),*board.Zones(),*board.GetDrawings()]}
        item=None;modified=True
        if kind=='track':
            points=_points(op['points']);width=_positive(op['width_mm']);lid=_layer(board,op['layer'],True);modified=False
            if op['net'] not in nets:raise ValueError('Unknown network')
            for a,b in zip(points,points[1:]):
                endpoints={(ed.mm(a[0]),ed.mm(a[1])),(ed.mm(b[0]),ed.mm(b[1]))}
                if len(endpoints)!=2:raise ValueError('Zero-length segment')
                item=next((x for x in board.GetTracks() if x.GetClass()=='PCB_TRACK'
                    and x.GetNetname()==op['net'] and x.GetLayer()==lid and x.GetWidth()==ed.mm(width)
                    and {(x.GetStart().x,x.GetStart().y),(x.GetEnd().x,x.GetEnd().y)}==endpoints),None)
                if item is None:item=ed.add_track(board,*a,*b,op['net'],width,op['layer']);modified=True
        elif kind=='arc':
            points=_points([op['start'],op['mid'],op['end']],3)
            width=_positive(op['width_mm']);lid=_layer(board,op['layer'],True)
            if op['net'] not in nets:raise ValueError('Unknown network')
            # A native PCB_ARC retains a UUID and is checked by KiCad DRC like
            # any other copper item.  Do not approximate an arc with segments:
            # curvature and the true clearance envelope matter to fabrication.
            item=next((x for x in board.GetTracks() if x.GetClass()=='PCB_ARC'
                        and x.GetNetname()==op['net'] and x.GetLayer()==lid
                        and x.GetWidth()==ed.mm(width)
                        and (x.GetStart().x,x.GetStart().y)==(ed.mm(op['start'][0]),ed.mm(op['start'][1]))
                        and (x.GetMid().x,x.GetMid().y)==(ed.mm(op['mid'][0]),ed.mm(op['mid'][1]))
                        and (x.GetEnd().x,x.GetEnd().y)==(ed.mm(op['end'][0]),ed.mm(op['end'][1]))),None)
            modified=item is None
            if item is None:
                item=ed.pcbnew.PCB_ARC(board)
                item.SetStart(ed.V(*op['start']));item.SetMid(ed.V(*op['mid']));item.SetEnd(ed.V(*op['end']))
                item.SetWidth(ed.mm(width));item.SetLayer(lid);item.SetNet(nets[op['net']]);board.Add(item)
        elif kind=='via':
            _points([op['at']],1);drill=_positive(op['drill_mm']);diameter=_positive(op['diameter_mm'])
            if drill>=diameter or op['net'] not in nets:raise ValueError('Invalid via/network')
            item=ed.add_via(board,*op['at'],op['net'],drill,diameter)
        elif kind=='remove':
            item=_require_item(items,op['id'])
            if item.GetLayer()==ed.pcbnew.Edge_Cuts:raise ValueError('Use outline operation to preserve intent')
            board.Delete(item);item=None
        elif kind=='track_width':
            width=_positive(op['width_mm']);item=_require_item(items,op['id'])
            if item.GetClass() not in ('PCB_TRACK','PCB_ARC'):raise ValueError('Select a track UUID, not a via, zone or drawing')
            item.SetWidth(ed.mm(width))
        elif kind=='pad_zone_connection':
            mode=op['connection']
            if mode not in PAD_ZONE_CONNECTIONS:raise ValueError('Unknown pad zone connection')
            item=_require_item({p.m_Uuid.AsString():p for fp in parts.values() for p in fp.Pads()},op['id'])
            fp=item.GetParentFootprint()
            part=next(p for p in updated['parts'] if p['ref']==fp.GetReference())
            if fp.IsLocked() or part.get('locked'):raise ValueError(fp.GetReference()+' is locked; explicitly unlock first')
            if not item.GetNumber() or not item.GetNetname() or not any(ed.pcbnew.IsCopperLayer(l) for l in item.GetLayerSet().Seq()):
                raise ValueError('Select a numbered copper pad with a network, not paste or mechanical geometry')
            modified=int(item.GetLocalZoneConnection())!=PAD_ZONE_CONNECTIONS[mode]
            item.SetLocalZoneConnection(PAD_ZONE_CONNECTIONS[mode])
        elif kind in ('move','side','lock'):
            ref=op['ref'];fp=parts[ref];part=next(p for p in updated['parts'] if p['ref']==ref)
            if kind=='lock':
                if type(op['locked']) is not bool:raise ValueError('locked must be boolean')
                fp.SetLocked(op['locked']);part['locked']=op['locked']
            else:
                if fp.IsLocked() or part.get('locked'):raise ValueError(ref+' is locked; explicitly unlock first')
                if kind=='move':
                    _points([op['at']],1);rotation=op.get('rotation',fp.GetOrientationDegrees())
                    if type(rotation) not in (int,float) or not math.isfinite(rotation):raise ValueError('Invalid rotation')
                    fp.SetPosition(ed.V(*op['at']));fp.SetOrientationDegrees(rotation)
                else:
                    if op['side'] not in ('top','bottom'):raise ValueError('side must be top or bottom')
                    if fp.IsFlipped()!=(op['side']=='bottom'):fp.Flip(fp.GetPosition(),False)
                part.update(at=[ed.to_mm(fp.GetPosition().x),ed.to_mm(fp.GetPosition().y)],rot=fp.GetOrientationDegrees(),side='bottom' if fp.IsFlipped() else 'top')
        elif kind=='outline':
            set_outline(board,op['points']);updated['outline']=op['points']
            xs,ys=zip(*op['points']);updated['board'].update(w=max(xs)-min(xs),h=max(ys)-min(ys))
        elif kind=='layers':
            count=op['count']
            if type(count)!=int or count<2 or count>32 or count%2:raise ValueError('Even layer count 2..32 required')
            old=board.GetCopperLayerCount()
            if count<old:
                active={x.GetLayer() for x in [*board.GetTracks(),*board.Zones()]}
                for zone in board.Zones():active.update(zone.GetLayerSet().Seq())
                if any(x not in (ed.pcbnew.F_Cu,ed.pcbnew.B_Cu) for x in active):raise ValueError('Remove/reroute internal copper before reducing layers')
            ed.set_layer_count(board,count);updated['board']['layers']=count
        elif kind in ('zone','keepout'):
            lid=_layer(board,op['layer'],True);points=_points(op['points'],3)
            item=ed.pcbnew.ZONE(board);item.SetLayer(lid)
            if kind=='zone':
                if op['net'] not in nets:raise ValueError('Unknown zone network')
                item.SetNet(nets[op['net']]);item.SetLocalClearance(ed.mm(_positive(op['clearance_mm'])))
                item.SetThermalReliefGap(ed.mm(_positive(op['thermal_gap_mm'])))
                item.SetThermalReliefSpokeWidth(ed.mm(_positive(op['thermal_spoke_mm'])))
            else:
                item.SetIsRuleArea(True);item.SetDoNotAllowTracks(True);item.SetDoNotAllowVias(True)
                # KiCad 10 renamed the zone-fill restriction; preserve its meaning.
                setter=getattr(item,'SetDoNotAllowZoneFills',None) or item.SetDoNotAllowCopperPour
                setter(True)
            poly=item.Outline();poly.NewOutline()
            for x,y in points:poly.Append(ed.mm(x),ed.mm(y))
            board.Add(item)
        elif kind=='field':
            if op['field'] not in ('reference','value'):raise ValueError('reference or value required')
            _points([op['at']],1);size=_positive(op['size_mm'])
            fp=parts[op['ref']];item=fp.Reference() if op['field']=='reference' else fp.Value()
            item.SetPosition(ed.V(*op['at']));item.SetTextSize(ed.V(size,size))
            item.SetTextAngle(ed.pcbnew.EDA_ANGLE(op['rotation'],ed.pcbnew.DEGREES_T));item.SetVisible(op['visible'])
        elif kind=='text':
            _points([op['at']],1);lid=_layer(board,op['layer'])
            if ed.pcbnew.IsCopperLayer(lid) or lid==ed.pcbnew.Edge_Cuts:raise ValueError('Text tool supports non-copper annotation layers')
            item=ed.pcbnew.PCB_TEXT(board);item.SetText(op['text']);item.SetPosition(ed.V(*op['at']));item.SetLayer(lid)
            size=_positive(op['size_mm']);item.SetTextSize(ed.V(size,size));board.Add(item)
        changes.append({'operation':kind,'id':str(item.m_Uuid.AsString()) if item is not None else None,'modified':modified})
    design.validate(updated)
    return updated,changes
