"""Native hierarchical sheets, explicit ports, scoped nets and dependency tracking."""
from pathlib import Path
import copy,hashlib,re,shutil,uuid
from tools.sexpr import parse,dump,child,children,Quoted as Q


def sheet_path(path):
    if not isinstance(path,str) or not re.fullmatch(r'/(?:[A-Za-z][A-Za-z0-9_-]*(?:/[A-Za-z][A-Za-z0-9_-]*)*)?',path):
        raise ValueError('Sheet path must be / or /Name/Subsheet with portable names')
    return path


def validate_spec(spec):
    sheets=spec.get('sheets',{})
    if not isinstance(sheets,dict):raise ValueError('sheets must map absolute logical paths to definitions')
    known={'/',*sheets}
    for path,definition in sheets.items():
        sheet_path(path)
        if path=='/' or not isinstance(definition,dict) or set(definition)-{'ports'}:raise ValueError('Child sheets accept ports only; root / is implicit')
        parent=path.rsplit('/',1)[0] or '/'
        if parent not in known:raise ValueError('Create parent sheet first: '+parent)
        ports=definition.get('ports',{})
        if not isinstance(ports,dict):raise ValueError('ports must be an object')
        used=set()
        for name,port in ports.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',name) or not isinstance(port,dict) or set(port)!={'net','direction'}:
                raise ValueError('Port requires name and {net,direction}')
            if port['net'] not in spec.get('nets',{}) or port['net'] in used:raise ValueError('Ports require distinct declared nets')
            if port['direction'] not in ('input','output','bidirectional'):raise ValueError('Invalid port direction')
            if spec['nets'][port['net']].get('scope') not in (None,'global'):raise ValueError('Sheet ports connect globally named intent nets; scoped internal nets stay local')
            used.add(port['net'])
    for name,net in spec.get('nets',{}).items():
        scope=net.get('scope','global')
        if scope!='global':
            sheet_path(scope)
            prefix=scope.strip('/')+'/' if scope!='/' else ''
            if scope not in known or not name.startswith(prefix) or not name[len(prefix):] or '/' in name[len(prefix):]:
                raise ValueError('Scoped net name must be Sheet/Subsheet/LocalName')
    for part in spec.get('parts',[]):
        path=part.get('sheet','/')
        if path not in known:raise ValueError(part['ref']+': unknown sheet '+str(path))
        for net in part.get('nets',{}).values():
            scope=spec.get('nets',{}).get(net,{}).get('scope','global')
            if scope!='global' and scope!=path:raise ValueError(part['ref']+': scoped net belongs to '+scope)


def dependencies(schematic):
    """Resolve actual Sheetfile references within the project; reject cycles/escapes."""
    root=Path(schematic).resolve();base=root.parent;seen=set();result=[]
    def visit(path,ancestors):
        path=path.resolve()
        if path!=root and base not in path.parents:raise ValueError('Schematic dependency escapes project')
        if path in ancestors:raise ValueError('Cyclic schematic hierarchy')
        if path in seen:return
        seen.add(path);result.append(path)
        if not path.is_file():return
        tree=parse(path.read_text())
        if tree[0]!='kicad_sch':raise ValueError('Expected native schematic')
        for sheet in children(tree,'sheet'):
            prop=next((p for p in children(sheet,'property') if str(p[1]) in ('Sheetfile','Sheet file')),None)
            if prop is None:raise ValueError('Sheet missing filename')
            name=str(prop[2])
            if Path(name).is_absolute():raise ValueError('Use project-relative sheet paths')
            visit(path.parent/name,ancestors|{path})
    visit(root,set());return result


def hashes(schematic):
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'MISSING' for p in dependencies(schematic)}


def copy_sheets(schematic,destination):
    root=Path(schematic).resolve();out=Path(destination)
    for source in dependencies(root):
        if not source.is_file():raise FileNotFoundError(source)
        target=out/source.relative_to(root.parent);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)


def check_structure(spec,schematic):
    """Compare declared sheet/port organization with actual native files."""
    from tools import validation as v
    actual={};assignments={}
    def walk(path,logical):
        tree=parse(path.read_text())
        for symbol in children(tree,'symbol'):
            ref=next((str(p[2]) for p in children(symbol,'property') if p[1]=='Reference'),None)
            if ref:assignments.setdefault(ref,set()).add(logical)
        for sheet in children(tree,'sheet'):
            props={str(p[1]):str(p[2]) for p in children(sheet,'property')}
            name=props.get('Sheetname',props.get('Sheet name'));filename=props.get('Sheetfile',props.get('Sheet file'))
            sub=logical.rstrip('/')+'/'+name
            if sub in actual:raise ValueError('Duplicate logical sheet path')
            actual[sub]={str(pin[1]):str(pin[2]) for pin in children(sheet,'pin')}
            walk(path.parent/filename,sub)
    try:
        dependencies(schematic);walk(Path(schematic).resolve(),'/')
    except (ValueError,TypeError,OSError) as e:return v.result('UNKNOWN','native sheet structure',[str(e)])
    expected={path:{name:port['direction'] for name,port in sheet.get('ports',{}).items()} for path,sheet in spec.get('sheets',{}).items()}
    issues=[]
    if actual!=expected:issues.append('Native sheet paths/port directions differ from intent')
    for part in spec['parts']:
        if assignments.get(part['ref'])!={part.get('sheet','/')}:
            issues.append(part['ref']+': native sheet assignment differs')
    return v.result('FAIL' if issues else 'PASS','sheet organization and component assignments',issues)


def _effects():return ['effects',['font',['size',1.27,1.27]],['justify','left']]
def _uuid():return ['uuid',Q(str(uuid.uuid4()))]
def _wire(a,b):return ['wire',['pts',['xy',*a],['xy',*b]],['stroke',['width',0],['type','default']],_uuid()]


def generate(spec,out_dir,name):
    """Generate distinct native files/instances; netlist export validates all boundaries."""
    from tools.schematic import Schematic
    validate_spec(spec);out=Path(out_dir);definitions=spec.get('sheets',{})
    paths=['/']+sorted(definitions,key=lambda x:(x.count('/'),x))
    pages={p:str(i+1) for i,p in enumerate(paths)}
    docs={p:Schematic(name) for p in paths}
    inst={p:str(uuid.uuid4()) for p in paths if p!='/'}
    instance_paths={'/':'/'+docs['/'].uuid}
    files={'/':Path(name+'.kicad_sch')}
    for path in paths[1:]:
        parent=path.rsplit('/',1)[0] or '/'
        instance_paths[path]=instance_paths[parent]+'/'+inst[path]
        files[path]=Path('schematics')/(path.strip('/').replace('/','__')+'-'+hashlib.sha256(path.encode()).hexdigest()[:12]+'.kicad_sch')
    for part in spec['parts']:
        docs[part.get('sheet','/')].add_part(part['ref'],part['symbol'],part.get('footprint',''),nets=part.get('nets'),
            value=part.get('value',''),no_connect=part.get('no_connect'),schematic_only=part.get('schematic_only',False),pad_map=part.get('pad_map'),placement=part.get('schematic_placement'))
    for path,doc in docs.items():
        tree=parse(doc.render());ports=definitions.get(path,{}).get('ports',{})
        aliases={port['net']:port_name for port_name,port in ports.items()}
        def label(net,at):
            scope=spec['nets'].get(net,{}).get('scope','global')
            if net in aliases:return ['hierarchical_label',Q(aliases[net]),['shape',ports[aliases[net]]['direction']],['at',*at,0],_effects(),_uuid()]
            if scope!='global':return ['label',Q(net.rsplit('/',1)[-1]),['at',*at,0],_effects(),_uuid()]
            return ['global_label',Q(net),['shape','bidirectional'],['at',*at,0],_effects(),_uuid()]
        for i,node in enumerate(tree):
            if not isinstance(node,list) or not node:continue
            if node[0]=='global_label':tree[i]=label(str(node[1]),child(node,'at')[1:3])
            elif node[0]=='symbol':
                for project in children(child(node,'instances',[]),'project'):
                    for item in children(project,'path'):item[1]=Q(instance_paths[path])
        for index,sub in enumerate(p for p in paths[1:] if (p.rsplit('/',1)[0] or '/')==path):
            x,y=50.8+(index%3)*101.6,177.8+(index//3)*76.2
            subports=definitions[sub].get('ports',{});height=max(25.4,10.16+len(subports)*7.62)
            relative=files[sub].name if path!='/' else str(files[sub])
            sheet=['sheet',['at',x,y],['size',50,height],['stroke',['width',0],['type','default']],['fill',['color',0,0,0,0]],['uuid',Q(inst[sub])],
                ['property',Q('Sheetname'),Q(sub.rsplit('/',1)[1]),['at',x,y-2,0],_effects()],
                ['property',Q('Sheetfile'),Q(relative),['at',x,y+height+2,0],_effects()],
                ['instances',['project',Q(name),['path',Q(instance_paths[path]),['page',Q(pages[sub])]]]]]
            for pi,(port_name,port) in enumerate(subports.items()):
                a=[x,y+5.08+pi*7.62];b=[x-5.08,a[1]]
                sheet.append(['pin',Q(port_name),port['direction'],['at',*a,180],_effects(),_uuid()])
                tree.extend([_wire(a,b),label(port['net'],b)])
            tree.append(sheet)
        if path!='/':tree=[n for n in tree if not (isinstance(n,list) and n and n[0]=='sheet_instances')]
        target=out/files[path];target.parent.mkdir(parents=True,exist_ok=True);target.write_text(dump(tree)+'\n')
    return [str(out/files[p]) for p in paths]
