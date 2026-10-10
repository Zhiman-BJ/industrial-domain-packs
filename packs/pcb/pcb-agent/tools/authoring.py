"""Workspace-local CAD drafts. Authoring never qualifies manufacturer facts."""
import math
import re
import tempfile
from pathlib import Path
from tools.sexpr import Quoted as Q, child, children, dump, parse

PIN_TYPES = {'input','output','bidirectional','tri_state','passive','free','unspecified',
             'power_in','power_out','open_collector','open_emitter','no_connect'}


def _path(identity, kind):
    if not isinstance(identity, str) or identity.count(':') != 1:
        raise ValueError('Expected library:name')
    lib, name = identity.split(':')
    for token in (lib, name):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+\-]*', token):
            raise ValueError('Use simple CAD library and item names without path separators')
    root = Path.cwd().resolve()
    base = root / '.pcb/authoring'
    path = base/'symbols'/(lib+'.kicad_sym') if kind == 'symbol' else base/'footprints'/(lib+'.pretty')/(name+'.kicad_mod')
    # Do not follow a draft symlink into another workspace file, even within root.
    if any(p.is_symlink() for p in (path, *path.parents) if p != root and root in p.parents):
        raise ValueError('Authoring paths must not contain symlinks')
    return path, name


def _number(value, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('Finite numeric coordinate or positive dimension required')
    return value


def _text(value, empty=False):
    if not isinstance(value,str) or (not value and not empty) or any(ord(c)<32 for c in value):
        raise ValueError('Expected printable string')
    return Q(value)


def _effects():
    return ['effects',['font',['size',1.27,1.27]]]


def _pin(data):
    allowed={'number','name','electrical_type','x','y','angle','length','unit'}
    if not isinstance(data,dict) or set(data)-allowed or not {'number','name','electrical_type','x','y'} <= set(data):
        raise ValueError('Pin requires number,name,electrical_type,x,y; optional angle,length,unit')
    if data['electrical_type'] not in PIN_TYPES: raise ValueError('Unsupported electrical_type')
    unit=data.get('unit',1)
    if type(unit) is not int or unit<1:raise ValueError('Unit must be a positive integer')
    angle=_number(data.get('angle',0))
    if angle not in (0,90,180,270):raise ValueError('Pin angle must be 0,90,180,270')
    return unit,['pin',data['electrical_type'],'line',
        ['at',_number(data['x']),_number(data['y']),angle],['length',_number(data.get('length',2.54),True)],
        ['name',_text(data['name']),_effects()],['number',_text(data['number']),_effects()]]


def _pad(data):
    allowed={'number','x','y','sx','sy','shape','angle','type','drill','side'}
    if not isinstance(data,dict) or set(data)-allowed or not {'number','x','y','sx','sy'} <= set(data):
        raise ValueError('Pad requires number,x,y,sx,sy; optional shape,angle,type,drill,side')
    kind=data.get('type','smd');shape=data.get('shape','rect');side=data.get('side','front')
    if kind not in ('smd','thru_hole','np_thru_hole') or shape not in ('rect','circle','oval') or side not in ('front','back'):
        raise ValueError('Unsupported pad type, shape or side')
    number=_text(data['number'],empty=kind=='np_thru_hole')
    if kind=='np_thru_hole' and number:raise ValueError('Non-plated holes have no electrical pad number')
    sx=_number(data['sx'],True);sy=_number(data['sy'],True)
    if shape=='circle' and sx!=sy:raise ValueError('Circular pad must have equal dimensions')
    pad=['pad',number,kind,shape,['at',_number(data['x']),_number(data['y']),_number(data.get('angle',0))],['size',sx,sy]]
    if kind=='smd':
        if 'drill' in data:raise ValueError('SMD pads cannot have a drill')
        prefix='F' if side=='front' else 'B'
        layers=[prefix+'.Cu',prefix+'.Paste',prefix+'.Mask']
    else:
        drill=_number(data.get('drill'),True)
        if drill>min(sx,sy) or (kind=='thru_hole' and drill==min(sx,sy)):
            raise ValueError('Drill must fit pad, with positive annulus for plated holes')
        pad.append(['drill',drill]);layers=['*.Cu','*.Mask']
    pad.append(['layers',*[Q(s) for s in layers]])
    return pad


def _write(path, tree, identity, **extra):
    text=dump(tree)+'\n';parse(text)
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as f:
        temp=Path(f.name)
        try:f.write(text)
        except Exception:
            temp.unlink();raise
    try:temp.replace(path)
    finally:temp.unlink(missing_ok=True)
    return dict(status='STAGED',path=str(path.relative_to(Path.cwd())),library_id=identity,
                qualification='REQUIRED',**extra)


def create_symbol(identity, pins, description=''):
    path,name=_path(identity,'symbol')
    tree=parse(path.read_text()) if path.exists() else ['kicad_symbol_lib',['version',20231120],['generator',Q('pcb-agent')]]
    if tree[0]!='kicad_symbol_lib':raise ValueError('Invalid library root')
    if any(n[1]==name for n in children(tree,'symbol')):raise FileExistsError(identity)
    symbol=['symbol',Q(name),['in_bom','yes'],['on_board','yes']]
    for key,value,y in [('Reference','U',7.62),('Value',name,-7.62),('Description',description,0)]:
        symbol.append(['property',Q(key),_text(value,empty=True),['at',0,y,0],_effects()])
    seen=set();units={}
    for data in pins:
        unit,pin=_pin(data);number=child(pin,'number')[1]
        if number in seen:raise ValueError('Duplicate pin number')
        seen.add(number);units.setdefault(unit,[]).append(pin)
    for unit,rows in units.items():symbol.append(['symbol',Q(f'{name}_{unit}_1'),*rows])
    tree.append(symbol)
    return _write(path,tree,identity,pins=len(seen))


def add_symbol_pin(identity,data):
    path,name=_path(identity,'symbol');tree=parse(path.read_text());unit,pin=_pin(data)
    symbol=next((s for s in children(tree,'symbol') if s[1]==name),None)
    if symbol is None:raise ValueError('Symbol identity missing in library')
    bodies=children(symbol,'symbol')
    if any(child(p,'number')[1]==child(pin,'number')[1] for b in bodies for p in children(b,'pin')):
        raise ValueError('Duplicate pin number')
    body=next((b for b in bodies if b[1]==f'{name}_{unit}_1'),None)
    if body is None:body=['symbol',Q(f'{name}_{unit}_1')];symbol.append(body)
    body.append(pin)
    return _write(path,tree,identity,added_pin=str(child(pin,'number')[1]))


def create_footprint(identity,pads,description=''):
    path,name=_path(identity,'footprint')
    if path.exists():raise FileExistsError(path)
    rows=[_pad(p) for p in pads];numbers=[p[1] for p in rows if p[1]]
    if len(set(numbers))!=len(numbers):raise ValueError('Duplicate pad number')
    return _write(path,['footprint',Q(name),['version',20240108],['generator',Q('pcb-agent')],
        ['layer',Q('F.Cu')],['descr',_text(description,empty=True)],*rows],identity,pads=len(rows))


def add_footprint_pad(identity,data):
    path,name=_path(identity,'footprint');tree=parse(path.read_text());pad=_pad(data)
    if tree[0]!='footprint' or tree[1]!=name:raise ValueError('Footprint identity mismatch')
    if pad[1] and any(p[1]==pad[1] for p in children(tree,'pad')):raise ValueError('Duplicate pad number')
    tree.append(pad)
    return _write(path,tree,identity,added_pad=str(pad[1]))


def edit_pin(identity,number,replacement=None):
    path,name=_path(identity,'symbol');tree=parse(path.read_text())
    symbol=next((s for s in children(tree,'symbol') if s[1]==name),None)
    if symbol is None:raise ValueError('Symbol identity missing')
    matches=[(b,p) for b in children(symbol,'symbol') for p in children(b,'pin') if child(p,'number')[1]==number]
    if len(matches)!=1:raise ValueError('Select one existing pin number')
    body,pin=matches[0]
    if replacement is not None:
        unit,new=_pin(replacement)
        if replacement['number']!=number:raise ValueError('Pin identity is preserved; disconnect/review before renumbering')
        target=next((b for b in children(symbol,'symbol') if b[1]==f'{name}_{unit}_1'),None)
        if target is None:target=['symbol',Q(f'{name}_{unit}_1')];symbol.append(target)
        body.remove(pin);target.append(new)
    else:body.remove(pin)
    return _write(path,tree,identity,changed_pin=number)


def edit_pad(identity,number,replacement=None):
    path,name=_path(identity,'footprint');tree=parse(path.read_text())
    if tree[0]!='footprint' or tree[1]!=name:raise ValueError('Footprint identity mismatch')
    matches=[p for p in children(tree,'pad') if p[1]==number]
    if len(matches)!=1:raise ValueError('Select one unambiguous existing pad number')
    if replacement is not None:
        new=_pad(replacement)
        if replacement['number']!=number:raise ValueError('Pad identity is preserved; review pin mapping before renumbering')
        tree[tree.index(matches[0])]=new
    else:tree.remove(matches[0])
    return _write(path,tree,identity,changed_pad=number)
