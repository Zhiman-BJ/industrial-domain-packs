"""Native KiCad copper polygons and holes in millimetres, for numerical backends."""
from tools import pcb_editor as ed
from shapely.geometry import Polygon,Point,LineString
from shapely.ops import unary_union


def polygons(poly):
    # Native filled zones encode holes using zero-width fracture bridges. Restore
    # their hole topology on a copy; do not heal arbitrary invalid CAD geometry.
    poly=ed.pcbnew.SHAPE_POLY_SET(poly)
    poly.Unfracture()
    def ring(chain):return [(ed.to_mm(chain.CPoint(i).x),ed.to_mm(chain.CPoint(i).y)) for i in range(chain.PointCount())]
    pieces=[]
    for i in range(poly.OutlineCount()):
        shape=Polygon(ring(poly.COutline(i)),[ring(poly.Hole(i,j)) for j in range(poly.HoleCount(i))])
        if not shape.is_valid:raise ValueError('Invalid native polygon')
        pieces.append(shape)
    return unary_union(pieces)


def extract(path):
    b=ed.load(path);native=ed.pcbnew.SHAPE_POLY_SET()
    if not ed.board_polygon_outlines(b, native):raise ValueError('Closed native board outline required')
    outline=polygons(native);layers=[b.GetLayerName(i) for i in b.GetEnabledLayers().CuStack()]
    shapes={};pads={};barrels=[];holes=[];parts={};minimum_width=None
    def add(net,layer,shape):
        if not shape.is_empty:shapes.setdefault((net,layer),[]).append(shape)
    for track in b.GetTracks():
        if track.GetClass()=='PCB_VIA':
            d=ed.to_mm(track.GetDrillValue());p=track.GetPosition();xy=(ed.to_mm(p.x),ed.to_mm(p.y))
            hole=Point(xy).buffer(d/2,resolution=24);holes.append(hole)
            barrels.append(dict(id=track.m_Uuid.AsString(),net=track.GetNetname(),xy=xy,drill_mm=d,
                                top=b.GetLayerName(track.TopLayer()),bottom=b.GetLayerName(track.BottomLayer())))
        else:
            width=ed.to_mm(track.GetWidth());minimum_width=width if minimum_width is None else min(minimum_width,width)
        for layer in layers:
            lid=b.GetLayerID(layer)
            if not track.IsOnLayer(lid):continue
            poly=ed.pcbnew.SHAPE_POLY_SET();track.TransformShapeToPolygon(poly,lid,0,ed.mm(.002),ed.pcbnew.ERROR_INSIDE)
            add(track.GetNetname(),layer,polygons(poly))
    for fp in b.GetFootprints():
        p=fp.GetPosition();parts[fp.GetReference()]=[ed.to_mm(p.x),ed.to_mm(p.y)]
        for pad in fp.Pads():
            key=fp.GetReference()+':'+pad.GetNumber()
            p=pad.GetPosition();xy=(ed.to_mm(p.x),ed.to_mm(p.y));size=pad.GetDrillSize();drill=max(size.x,size.y)
            if drill:
                if size.x!=size.y:raise ValueError('Slotted holes need an explicit 3D/mesh adapter')
                holes.append(Point(xy).buffer(ed.to_mm(drill)/2,resolution=24))
                if pad.GetAttribute()==ed.pcbnew.PAD_ATTRIB_PTH:
                    barrels.append(dict(id=pad.m_Uuid.AsString(),net=pad.GetNetname(),xy=xy,drill_mm=ed.to_mm(drill),top=layers[0],bottom=layers[-1]))
            record=dict(xy=xy,net=pad.GetNetname(),layers={},drill_mm=ed.to_mm(drill),members=[dict(uuid=pad.m_Uuid.AsString(),xy=xy)])
            for layer in layers:
                lid=b.GetLayerID(layer)
                if not pad.IsOnLayer(lid):continue
                poly=ed.pcbnew.SHAPE_POLY_SET();pad.TransformShapeToPolygon(poly,lid,0,ed.mm(.002),ed.pcbnew.ERROR_INSIDE)
                shape=polygons(poly);record['layers'][layer]=shape;add(pad.GetNetname(),layer,shape)
            if pad.GetNumber():
                if key in pads:
                    previous=pads[key]
                    if previous['net']!=record['net']:
                        raise ValueError('Repeated physical pads have conflicting nets: '+key)
                    # Exposed pads and their plated holes commonly share one
                    # logical terminal. Keep every physical shape and barrel.
                    previous['members'].extend(record['members'])
                    for layer,shape in record['layers'].items():
                        previous['layers'][layer]=unary_union([previous['layers'].get(layer,Polygon()),shape])
                else:pads[key]=record
    for zone in b.Zones():
        if zone.GetIsRuleArea():continue
        for layer in layers:
            lid=b.GetLayerID(layer)
            if not zone.IsOnLayer(lid):continue
            poly=zone.GetFilledPolysList(lid)
            if poly.OutlineCount()==0:raise ValueError('Fill every copper zone before numerical analysis')
            add(zone.GetNetname(),layer,polygons(poly))
    voids=unary_union(holes);copper={key:unary_union(items).difference(voids) for key,items in shapes.items()}
    return dict(board=b,outline=outline.difference(voids),board_outline=outline,layers=layers,copper=copper,pads=pads,
                barrels=barrels,holes=voids,parts=parts,minimum_width_mm=minimum_width)
