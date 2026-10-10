"""Local native schematic edits. Positions are drawing coordinates, never PCB poses."""
import copy
import math
from pathlib import Path
from tools.sexpr import parse, dump, child, children, Quoted as Q
from tools import hierarchy, validation as v, schematic


def source(name):
    path=(Path.cwd()/name).resolve()
    allowed=set(hierarchy.dependencies('board.kicad_sch'))
    if path not in allowed or not path.is_relative_to(Path.cwd()):raise ValueError('Select a sheet in the current project')
    return path


def ref(obj):
    return next((str(p[2]) for p in children(obj,'property') if p[1]=='Reference'),None)


def instance(tree,reference,unit):
    found=[n for n in children(tree,'symbol') if ref(n)==reference and int(child(n,'unit',['unit',1])[1])==unit]
    if len(found)!=1:raise ValueError('Select an unambiguous reference/unit on this sheet')
    return found[0]


def pin_positions(tree,obj):
    identity=child(obj,'lib_id');lib=child(tree,'lib_symbols',[])
    definition=next((s for s in children(lib,'symbol') if s[1]==identity[1]),None)
    if definition is None:raise ValueError('Embedded symbol definition missing')
    unit=int(child(obj,'unit',['unit',1])[1]);at=child(obj,'at');mirror=child(obj,'mirror',['mirror',''])[1]
    if int(child(obj,'convert',['convert',1])[1]) not in (0,1):raise ValueError('Alternate symbol body style needs explicit geometry support')
    c=math.cos(math.radians(float(at[3])));s=math.sin(math.radians(float(at[3])))
    result={}
    for number,name,x,dy,kind in schematic.symbol_units(definition)[unit]:
        y=-dy;dx=c*x-s*y;dy=-s*x-c*y
        if mirror=='x':dy=-dy
        elif mirror=='y':dx=-dx
        elif mirror:raise ValueError('Unsupported mirror')
        point=(round(float(at[1])+dx,6),round(float(at[2])+dy,6))
        result.setdefault(number,set()).add(point)
    return result


def inspect(path):
    tree=parse(path.read_text());objects=[]
    for obj in tree[1:]:
        if not isinstance(obj,list) or not child(obj,'uuid'):continue
        record=dict(id=str(child(obj,'uuid')[1]),kind=str(obj[0]))
        at=child(obj,'at')
        if at:record['at']=list(map(float,at[1:]))
        if obj[0]=='symbol':
            record.update(ref=ref(obj),unit=int(child(obj,'unit',['unit',1])[1]),symbol=str(child(obj,'lib_id')[1]),
                pins={n:[list(x) for x in sorted(pts)] for n,pts in pin_positions(tree,obj).items()},
                fields={str(p[1]):dict(value=str(p[2]),at=list(map(float,child(p,'at',[])[1:]))) for p in children(obj,'property')})
        elif obj[0]=='wire':record['points']=[list(map(float,p[1:])) for p in children(child(obj,'pts',[]),'xy')]
        elif obj[0] in ('label','global_label','hierarchical_label'):record['text']=str(obj[1])
        objects.append(record)
    return dict(sheet=str(path.relative_to(Path.cwd())),schematic_sha256=v.file_hash(path),objects=objects)


def move(tree,reference,unit,x,y,rotation):
    obj=instance(tree,reference,unit);old=copy.deepcopy(obj);at=child(obj,'at');oldpins=pin_positions(tree,obj)
    if rotation is None:rotation=float(at[3])
    if rotation%90:raise ValueError('Schematic symbols require 90-degree rotations')
    at[1:4]=[x,y,rotation%360];newpins=pin_positions(tree,obj);mapping={}
    for pin,points in oldpins.items():
        if len(points)!=1 or len(newpins[pin])!=1:raise ValueError('Ambiguous stacked pin geometry; edit the library first')
        mapping[next(iter(points))]=next(iter(newpins[pin]))
    for other in children(tree,'symbol'):
        if other is obj:continue
        if any(p in mapping for pts in pin_positions(tree,other).values() for p in pts):
            raise ValueError('Shared symbol pin anchor; separate its wiring explicitly before moving')
    # Interior taps cannot be stretched without choosing a new route. Keep the
    # old file untouched and ask for a wire edit instead of silently cutting it.
    for wire in children(tree,'wire'):
        pts=children(child(wire,'pts',[]),'xy')
        if len(pts)!=2:raise ValueError('Malformed wire')
        a,b=[tuple(map(float,p[1:3])) for p in pts]
        for p in mapping:
            if p not in (a,b) and abs(math.dist(a,p)+math.dist(p,b)-math.dist(a,b))<1e-6:
                raise ValueError('Pin lies on a wire interior; split/reroute that wire before moving')
        for p in pts:
            point=tuple(round(float(z),6) for z in p[1:3])
            if point in mapping:p[1:3]=mapping[point]
    for other in tree[1:]:
        if not isinstance(other,list) or other[0] not in ('no_connect','junction','label','global_label','hierarchical_label'):continue
        a=child(other,'at');point=tuple(round(float(z),6) for z in a[1:3]) if a else None
        if point in mapping:a[1:3]=mapping[point]
    oldat=child(old,'at');angle=math.radians(rotation-float(oldat[3]));c=math.cos(angle);s=math.sin(angle)
    for prop in children(obj,'property'):
        a=child(prop,'at')
        if not a:continue
        dx=float(a[1])-float(oldat[1]);dy=float(a[2])-float(oldat[2])
        a[1:4]=[round(x+c*dx+s*dy,6),round(y-s*dx+c*dy,6),float(a[3])-rotation+float(oldat[3])]
    return tree


def placement(obj):
    return dict(at=list(map(float,child(obj,'at')[1:3])),rotation=float(child(obj,'at')[3]),
        mirror=child(obj,'mirror',['mirror',''])[1],
        fields={str(p[1]):copy.deepcopy(p) for p in children(obj,'property')})


def inherit_layout(spec,root):
    """Preserve actual instance poses on regeneration, including external GUI edits."""
    result=copy.deepcopy(spec)
    if not Path(root).is_file():return result
    parts={p['ref']:p for p in result['parts']}
    paper=child(parse(Path(root).read_text()),'paper')
    if paper and paper[1] in ('A4','A3','A2','A1','A0'):result['schematic_page']=str(paper[1])
    for path in hierarchy.dependencies(root):
        for obj in children(parse(path.read_text()),'symbol'):
            if ref(obj) in parts:
                parts[ref(obj)].setdefault('schematic_placement',{})[str(child(obj,'unit',['unit',1])[1])]=placement(obj)
    return result


def preserve_ids(old_root,new_root):
    """Preserve UUIDs by sheet/ref/unit/pin and unchanged native object geometry."""
    if not Path(old_root).is_file():return
    oldpaths={p.relative_to(Path(old_root).resolve().parent):p for p in hierarchy.dependencies(old_root)}
    replacements={};trees={}
    for path in hierarchy.dependencies(new_root):
        tree=parse(path.read_text());trees[path]=tree;old=oldpaths.get(path.relative_to(Path(new_root).resolve().parent))
        if not old:continue
        prior=parse(old.read_text());replacements[str(child(tree,'uuid')[1])]=str(child(prior,'uuid')[1])
        def keys(t):
            found={}
            for n in t[1:]:
                if not isinstance(n,list) or not child(n,'uuid'):continue
                if n[0]=='symbol':key=('symbol',ref(n),str(child(n,'unit',['unit',1])[1]))
                else:key=(str(n[0]),dump([x for x in n if not (isinstance(x,list) and x[0]=='uuid')]))
                found[key]=n
            return found
        oldnodes=keys(prior)
        for key,node in keys(tree).items():
            previous=oldnodes.get(key)
            if previous is None:continue
            replacements[str(child(node,'uuid')[1])]=str(child(previous,'uuid')[1])
            pins={str(p[1]):p for p in children(previous,'pin')}
            for pin in children(node,'pin'):
                if str(pin[1]) in pins:replacements[str(child(pin,'uuid')[1])]=str(child(pins[str(pin[1])],'uuid')[1])
    def rewrite(node):
        for i,x in enumerate(node):
            if isinstance(x,list):rewrite(x)
            elif i==1 and node[0] in ('uuid','path') and isinstance(x,str):
                if x in replacements:node[i]=Q(replacements[x])
                elif x.startswith('/'):
                    node[i]=Q('/'.join(replacements.get(p,p) for p in x.split('/')))
    for path,tree in trees.items():rewrite(tree);path.write_text(dump(tree)+'\n')
