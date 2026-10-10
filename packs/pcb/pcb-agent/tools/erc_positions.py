"""Locate ERC objects by native UUID, preserving raw report positions and units."""
import math
import re
from tools import hierarchy
from tools.sexpr import parse, child, children


def anchors(schematic):
    found={}
    def add(uuid,point):
        if uuid:found.setdefault(str(uuid[1]),set()).add(tuple(round(float(v),6) for v in point))
    for path in hierarchy.dependencies(schematic):
        tree=parse(path.read_text());libs=child(tree,'lib_symbols',[])
        library={str(s[1]):s for s in children(libs,'symbol')}
        for obj in tree[1:]:
            if not isinstance(obj,list) or not obj:continue
            at=child(obj,'at')
            if at:add(child(obj,'uuid'),at[1:3])
            if obj[0]!='symbol' or not at:continue
            identity=child(obj,'lib_id');definition=library.get(str(identity[1])) if identity else None
            if definition is None:continue
            unit=int(child(obj,'unit',['unit',1])[1]);style=int(child(obj,'convert',['convert',1])[1]);pins={}
            for body in children(definition,'symbol'):
                match=re.search(r'_(\d+)_(\d+)$',str(body[1]))
                if not match or int(match[1]) not in (0,unit) or int(match[2]) not in (0,style):continue
                for p in children(body,'pin'):
                    n=child(p,'number');loc=child(p,'at')
                    if n and loc:pins.setdefault(str(n[1]),[]).append(loc)
            angle=math.radians(float(at[3]));c=math.cos(angle);s=math.sin(angle)
            mirror=child(obj,'mirror',['mirror',''])[1]
            for pin in children(obj,'pin'):
                locations=pins.get(str(pin[1]),[])
                if len(locations)!=1:continue
                x,y=map(float,locations[0][1:3])
                # Native symbol coordinates are Cartesian; sheet coordinates
                # have downward Y. Mirroring is applied in sheet space.
                dx=c*x-s*y;dy=-s*x-c*y
                if mirror=='x':dy=-dy
                elif mirror=='y':dx=-dx
                elif mirror:continue
                add(child(pin,'uuid'),(float(at[1])+dx,float(at[2])+dy))
    return {uid:next(iter(points)) for uid,points in found.items() if len(points)==1}


def normalize(report,schematic):
    try:positions=anchors(schematic)
    except (OSError,ValueError,IndexError,TypeError) as error:
        positions={};report['position_error']=str(error)
    # Wires have pts, not an at anchor. Report their native geometry directly;
    # never apply a global 100x correction to unrelated objects or prose.
    wires={}
    try:
        for path in hierarchy.dependencies(schematic):
            for obj in children(parse(path.read_text()),'wire'):
                uid=child(obj,'uuid');pts=child(obj,'pts',[])
                points=[tuple(map(float,p[1:3])) for p in children(pts,'xy')]
                if uid and len(points)==2:wires.setdefault(str(uid[1]),set()).add(tuple(points))
    except (OSError,ValueError,IndexError,TypeError) as error:
        report['geometry_error']=str(error)
    matched=0;unknown=0
    for violation in report.get('violations',[]):
        for item in violation.get('items',[]):
            raw=item.pop('pos',None)
            item['native_pos']=raw;item['native_coordinate_units']=report.get('coordinate_units','unspecified')
            location=positions.get(item.get('uuid'))
            segments=wires.get(item.get('uuid'),set())
            if len(segments)==1:
                a,b=next(iter(segments));length=math.dist(a,b)
                item.update(endpoints_mm=[dict(zip(('x','y'),p)) for p in (a,b)],length_mm=length,
                            native_description=item.get('description'),
                            description=f'Wire, length {length:.6g} mm (source geometry)')
                # Match a reported endpoint to this exact UUID's geometry.
                # Both corrected and affected native exporters are supported;
                # ambiguous/unmatched positions stay UNKNOWN.
                if isinstance(raw,dict) and all(type(raw.get(k)) in (int,float) for k in ('x','y')):
                    matches={p for p in (a,b) for factor in (1,100)
                             if all(abs(raw[k]*factor-p[i])<1e-5 for i,k in enumerate(('x','y')))}
                    if len(matches)==1:location=next(iter(matches))
            if location is None:
                item['position_status']='UNKNOWN';unknown+=1
            else:
                item.update(pos=dict(zip(('x','y'),location)),position_units='mm',position_status='RESOLVED',
                            position_source='schematic object UUID')
                matched+=1
    report['position_resolution']={'resolved':matched,'unknown':unknown,'method':'native schematic UUID anchors; raw JSON retained'}
    return report
