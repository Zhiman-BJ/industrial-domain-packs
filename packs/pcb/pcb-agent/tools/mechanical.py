"""Native KiCad 3D exports and provenance-bound model preparation.

Exports are observations of nominal CAD, not a manufacturing or collision PASS.
Numerical assembly checks run separately through the mechanical analysis backend.
"""
from pathlib import Path
import hashlib,json,math,os,re,shutil,struct,tempfile
from tools import sexpr,validation as v,kicad_cli as kc,library

MODEL_REPOSITORY='https://gitlab.com/kicad/libraries/kicad-packages3D'


def official_model_source(name, relative):
    revision=library.installed_revision()
    major=re.match(r'^\$\{KICAD(\d+)_3DMODEL_DIR\}',name).group(1)
    if major!=revision.split('.')[0]:
        if major=='9':revision='9.0.9'  # retained historical baseline, never latest by name
        else:raise ValueError('Model library version needs an explicit sourced download: '+name)
    return dict(url=MODEL_REPOSITORY+'/-/raw/'+revision+'/'+relative,revision=revision,
                license_url=MODEL_REPOSITORY+'/-/blob/'+revision+'/LICENSE.md')


def system_model_root(major):
    if major==library.installed_revision().split('.')[0]:return Path('/usr/share/kicad/3dmodels')
    if major=='9':
        historical=Path('/opt/kicad-library-snapshots/9.0.9/3dmodels')
        if historical.is_dir():return historical
    return None


def body_applicability(fp):
    """Recognize an installed library's bare round copper land, not user waivers.

    Narrow by geometry and native assembly exclusions, never reference/value or
    candidate-authored metadata. Other unmodeled footprints still need a body.
    The installed library is runtime-owned; project library overrides are not used.
    """
    required=dict(required=True,reason='Populated assembly body required')
    if sexpr.children(fp,'model'):return required
    identifier=str(fp[1]).split(':')
    if len(identifier)!=2 or any(not re.fullmatch(r'[A-Za-z0-9_.+\-]+',x) or x in ('.','..') for x in identifier):return required
    source=Path('/usr/share/kicad/footprints')/(identifier[0]+'.pretty')/(identifier[1]+'.kicad_mod')
    if not source.is_file():return required
    original=sexpr.parse(source.read_text())
    def signature(node,installed=False):
        attrs=sexpr.child(node,'attr') or []
        # Schematic-to-PCB synchronization can clear BOM exclusion while
        # retaining the copper land in the electrical BOM. Require the installed
        # source to declare both, but do not invent a body for that BOM row.
        needed={'exclude_from_pos_files','exclude_from_bom'} if installed else {'exclude_from_pos_files'}
        if not needed<=set(attrs[1:]):return None
        if sexpr.children(node,'model'):return None
        pads=sexpr.children(node,'pad')
        if len(pads)!=1:return None
        pad=pads[0]
        if [str(x) for x in pad[2:4]]!=['smd','circle'] or sexpr.child(pad,'drill'):return None
        at=sexpr.child(pad,'at');size=sexpr.child(pad,'size');layers=sexpr.child(pad,'layers')
        if not at or not size or not layers:return None
        xy=[float(x) for x in at[1:3]];dimensions=[float(x) for x in size[1:]]
        if xy!=[0.,0.] or len(dimensions)!=2 or dimensions[0]!=dimensions[1] or not 0<dimensions[0]<100:return None
        side=str((sexpr.child(node,'layer') or ['layer','F.Cu'])[1]).split('.')[0]
        if side not in ('F','B') or set(str(x) for x in layers[1:])!={side+'.Cu',side+'.Mask'}:return None
        return str(pad[1]),dimensions
    expected=signature(original,installed=True)
    if expected is None or signature(fp)!=expected:return required
    return dict(required=False,reason='Installed-library bare copper land; no separate assembly body',
                library_id=':'.join(identifier),source=str(source),source_sha256=v.file_hash(source),
                scope='Body inventory only; connectivity, copper clearance and fixed task acceptance remain independent')


def missing_bodies(records):
    return [r['ref'] for r in records if not r['dnp'] and r['body_applicability']['required']
            and (not r['models'] or any(not m['exists'] for m in r['models']))]


def inventory(board):
    board=Path(board).resolve();root=board.parent
    doc=sexpr.parse(board.read_text());records=[]
    for fp in sexpr.children(doc,'footprint'):
        ref=next((str(p[2]) for p in sexpr.children(fp,'property') if str(p[1])=='Reference'),None)
        if not ref:raise ValueError('Every footprint needs a reference for mechanical identity')
        attr=sexpr.child(fp,'attr') or [];dnp='dnp' in attr
        bodies=[]
        for index,node in enumerate(sexpr.children(fp,'model')):
            name=str(node[1]);show=sexpr.child(node,'hide')
            if show:raise ValueError(ref+': hidden 3D bodies need an explicit corrected assembly model')
            transform={}
            for key,default in [('offset',[0,0,0]),('rotate',[0,0,0]),('scale',[1,1,1])]:
                entry=sexpr.child(node,key);xyz=sexpr.child(entry,'xyz') if entry else None
                values=[float(x) for x in xyz[1:]] if xyz else default
                if len(values)!=3 or any(not math.isfinite(x) for x in values) or key=='scale' and any(x<=0 for x in values):
                    raise ValueError(ref+': invalid 3D transform')
                transform[key]=values
            path=_resolve(name,root)
            bodies.append(dict(index=index,name=name,path=str(path),exists=path.is_file(),
                               sha256=v.file_hash(path) if path.is_file() else 'MISSING',transform=transform))
        records.append(dict(ref=ref,dnp=dnp,models=bodies,body_applicability=body_applicability(fp)))
    if len({r['ref'] for r in records})!=len(records):raise ValueError('Duplicate assembly references')
    return records


def _resolve(name,root):
    root=Path(root).resolve()
    if Path(name).suffix.lower() not in ('.step','.stp','.wrl','.wrz'):
        raise ValueError('Assigned 3D body must be a STEP or VRML file')
    # Project cache takes precedence over machine paths, enabling portable capture.
    match=re.match(r'^\$\{KICAD\d+_3DMODEL_DIR\}/(.+)$',name)
    if match:
        relative=Path(match[1])
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Escaping 3D model path')
        local=root/'.pcb/3dmodels'/relative
        if not local.resolve().is_relative_to(root):raise ValueError('Escaping project 3D model path')
        if local.is_file():return local.resolve()
        manifest=root/'.pcb/3dmodels/sources.json'
        if manifest.is_file() and str(local.relative_to(root)) in json.loads(manifest.read_text()):
            return local.resolve()  # A lost pinned body must not become a different system model.
        major=re.match(r'^\$\{KICAD(\d+)_3DMODEL_DIR\}',name).group(1)
        base=system_model_root(major)
        if base and (base/relative).is_file():return (base/relative).resolve()
        return local.resolve()
    expanded=name.replace('${KIPRJMOD}',str(root))
    if '${' in expanded:raise ValueError('Unresolved 3D model variable: '+name)
    resolved=(root/expanded).resolve()
    if not resolved.is_relative_to(root):raise ValueError('Custom 3D body must be inside the project')
    return resolved


def dependencies(board):
    board=Path(board).resolve();paths={board.parent/'.pcb/3dmodels/sources.json'}
    if board.is_file():
        for record in inventory(board):
            paths.update(Path(m['path']) for m in record['models'])
            if record['body_applicability'].get('source'):paths.add(Path(record['body_applicability']['source']))
    return sorted(paths)


def localize_available(board):
    """Bundle already installed standard bodies; never guess or fetch missing ones."""
    copied=[]
    for record in inventory(board):
        if record['models'] and all(m['exists'] for m in record['models']) and any(
                re.match(r'^\$\{KICAD\d+_3DMODEL_DIR\}/',m['name']) for m in record['models']):
            copied.append(dict(ref=record['ref'],**prepare(board,record['ref'])))
    return copied


def prepare(board,ref,sources=None):
    """Download only the selected footprint's referenced bodies; no guessed package."""
    board=Path(board).resolve();root=board.parent;before=v.file_hash(board)
    record=next((r for r in inventory(board) if r['ref']==ref),None)
    if record is None:raise ValueError('Unknown footprint reference: '+ref)
    if not record['models']:raise ValueError('Footprint has no 3D model assignment; assign a sourced body first')
    supplied=sources or {};manifest=root/'.pcb/3dmodels/sources.json';manifest.parent.mkdir(parents=True,exist_ok=True)
    saved=json.loads(manifest.read_text()) if manifest.is_file() else {}
    downloaded=[];local_paths={}
    for model in record['models']:
        name=model['name'];match=re.match(r'^\$\{KICAD\d+_3DMODEL_DIR\}/(.+)$',name)
        if model['exists']:
            if not Path(model['path']).is_relative_to(root):
                relative=match[1] if match else 'external/'+model['sha256']+'/'+Path(model['path']).name
                target=root/'.pcb/3dmodels'/relative;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(model['path'],target)
                metadata=Path('/opt/pcb-system-3d-models.json')
                if Path(model['path']).is_relative_to('/opt/kicad-library-snapshots/9.0.9/3dmodels'):
                    metadata=Path('/opt/kicad-library-snapshots/9.0.9/3dmodels-source.json')
                source=json.loads(metadata.read_text()) if match and metadata.is_file() else {'origin':'existing_assigned_body','source_path':model['path']}
                key=str(target.relative_to(root));saved[key]=dict(source,sha256=v.file_hash(target),scope='Nominal library geometry; no exact-MPN dimensional qualification')
                temp=manifest.with_suffix('.tmp');temp.write_text(json.dumps(saved,indent=2));temp.replace(manifest)
                local_paths[name]=target
                downloaded.append(dict(path=key,sha256=saved[key]['sha256'],action='copied_from_installed_library' if match else 'copied_assigned_body'));continue
            local_paths[name]=Path(model['path'])
            downloaded.append(dict(path=model['path'],sha256=model['sha256'],action='existing'));continue
        if name in supplied:
            source=supplied[name]
            if not all(source.get(k) for k in ('url','revision','license_url','sha256')):raise ValueError('Custom model needs URL, revision, license and expected hash')
        elif match:
            source=official_model_source(name,match[1])
        else:raise ValueError('Custom model needs an explicit sourced download: '+name)
        target=Path(model['path'])
        if root not in target.parents:raise ValueError('Model downloads must stay inside the workspace')
        target.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
            staged=Path(tmp)/target.name;digest=library._download(source['url'],staged,max_bytes=128*1024*1024)
            if source.get('sha256') and source['sha256']!=digest:raise ValueError('3D model SHA-256 mismatch')
            head=staged.read_bytes()[:256]
            if target.suffix.lower() in ('.step','.stp'):
                if b'ISO-10303-21;' not in head:raise ValueError('Expected STEP model, not an HTML/download error')
            elif target.suffix.lower() in ('.wrl','.vrml'):
                if not head.lstrip().startswith(b'#VRML'):raise ValueError('Expected VRML model')
            else:raise ValueError('Supported model downloads: STEP or VRML')
            shutil.copy2(staged,target)
        key=str(target.relative_to(root));saved[key]=dict(source,sha256=digest,scope='Nominal library geometry; no exact-MPN dimensional qualification')
        # Persist after each completed body so an interrupted intake retains provenance.
        temp=manifest.with_suffix('.tmp');temp.write_text(json.dumps(saved,indent=2));temp.replace(manifest)
        local_paths[name]=target
        downloaded.append(dict(path=key,sha256=digest,action='downloaded'))
    # KiCad itself must resolve the shipped bodies. Merely caching files while
    # leaving KICAD*_3DMODEL_DIR in the board works only through our resolver.
    if v.file_hash(board)!=before:raise ValueError('Board changed during model preparation; acquired files retained')
    doc=sexpr.parse(board.read_text());changes=[]
    fp=next(fp for fp in sexpr.children(doc,'footprint') if any(str(p[1])=='Reference' and str(p[2])==ref for p in sexpr.children(fp,'property')))
    for node in sexpr.children(fp,'model'):
        name=str(node[1]);target=local_paths[name].resolve()
        if not target.is_relative_to(root):raise ValueError('Prepared body escapes project')
        local_name='${KIPRJMOD}/'+target.relative_to(root).as_posix()
        if name!=local_name:
            node[1]=sexpr.Quoted(local_name);changes.append(dict(previous=name,current=local_name))
    if changes:
        with tempfile.NamedTemporaryFile(mode='w',dir=root,prefix='.prepared-model-',delete=False) as f:
            f.write(sexpr.dump(doc)+'\n');staged=Path(f.name)
        staged.chmod(board.stat().st_mode);staged.replace(board)
    return v.result('PASS','Project-local assigned bodies; export and mechanical analysis remain separate',files=downloaded,
                    relinked=changes,source_board_sha256=before,board_sha256=v.file_hash(board))


def export(board,destination,format='glb',refs=None,board_only=False,view='isometric',width_px=1600,height_px=1000):
    board=Path(board).resolve();out=Path(destination).resolve()
    if format not in ('step','glb','png'):raise ValueError('Use step, glb or png')
    if view not in ('top','bottom','isometric'):raise ValueError('Use top, bottom or isometric view')
    if any(type(n) is not int or not 64<=n<=4096 for n in (width_px,height_px)):raise ValueError('Image dimensions must be integers from 64 to 4096 pixels')
    if format!='png' and (view!='isometric' or width_px!=1600 or height_px!=1000):raise ValueError('View and image dimensions apply only to PNG exports')
    if out.exists():raise FileExistsError(out)
    records=inventory(board);selected=[r for r in records if not r['dnp'] and (refs is None or r['ref'] in refs)]
    if refs is not None and set(refs)!={r['ref'] for r in selected}:raise ValueError('Unknown or DNP export reference')
    if refs is not None and any(re.search(r'[,?*]',ref) for ref in refs):raise ValueError('Native component filters require literal non-wildcard references')
    missing=missing_bodies(selected)
    before={str(board):v.file_hash(board)}
    for r in selected:
        before.update({m['path']:m['sha256'] for m in r['models']})
        applicability=r['body_applicability']
        if applicability.get('source'):before[applicability['source']]=applicability['source_sha256']
    out.parent.mkdir(parents=True,exist_ok=True)
    # Resolve models in a private copy. Native transforms/flip/rotation stay with
    # KiCad, avoiding an approximate duplicate transformation implementation.
    with tempfile.TemporaryDirectory(prefix='pcb-3d-') as tmp:
        tmp=Path(tmp);doc=sexpr.parse(board.read_text())
        by_ref={r['ref']:r for r in records}
        for fp in sexpr.children(doc,'footprint'):
            ref=next(str(p[2]) for p in sexpr.children(fp,'property') if str(p[1])=='Reference')
            if by_ref[ref]['dnp']:
                for node in list(sexpr.children(fp,'model')):fp.remove(node)
                continue
            for node,model in zip(sexpr.children(fp,'model'),by_ref[ref]['models']):node[1]=sexpr.Quoted(model['path'])
        native=tmp/board.name;native.write_text(sexpr.dump(doc))
        if board.with_suffix('.kicad_pro').is_file():shutil.copy2(board.with_suffix('.kicad_pro'),native.with_suffix('.kicad_pro'))
        if format=='png':
            if refs is not None or board_only:raise ValueError('PNG exports show the complete modeled assembly')
            argv=['pcb','render','--width',str(width_px),'--height',str(height_px),'--side','bottom' if view=='bottom' else 'top',
                  '--quality','basic','--background','opaque']
            # Leave room for the rotated corners and component height. Native
            # top-view framing at zoom 1 can clip an isometric assembly.
            if view=='isometric':argv+=['--rotate','35,0,35','--perspective','--zoom','0.8']
            argv+=['--output',str(out),str(native)]
        else:
            argv=['pcb','export',format,'--no-dnp','--output',str(out)]
            if board_only:argv+=['--board-only','--cut-vias-in-body']
            # KiCad 9.0.9 can emit an empty assembly with --no-board-body.
            # Keep the board; the solid backend selects the named occurrence.
            elif refs is not None:argv+=['--component-filter',','.join(refs)]
            else:argv+=['--include-tracks','--include-pads','--include-zones','--include-silkscreen','--include-soldermask']
            argv.append(str(native))
        result=kc._run(argv,timeout=600)
    valid=out.is_file() and out.stat().st_size>0
    if valid:
        magic=out.read_bytes()[:256]
        valid=(b'ISO-10303-21;' in magic if format=='step' else magic[:4]==b'glTF' if format=='glb' else magic[:8]==b'\x89PNG\r\n\x1a\n')
    issues=[]
    image_size=None
    if valid and format=='png':
        image_size=dict(zip(('width_px','height_px'),struct.unpack('>II',out.read_bytes()[16:24])))
    if valid and format=='glb' and not board_only:
        try:
            raw=out.read_bytes();length,kind=struct.unpack_from('<II',raw,12)
            if kind!=0x4e4f534a or len(raw)!=struct.unpack_from('<I',raw,8)[0]:raise ValueError('Invalid GLB container')
            scene=json.loads(raw[20:20+length]);nodes=scene['nodes'];meshes=scene['meshes']
            def has_mesh(index,seen):
                if index in seen:raise ValueError('Cyclic GLB node graph')
                node=nodes[index]
                if 'mesh' in node and meshes[node['mesh']].get('primitives'):return True
                return any(has_mesh(child,seen|{index}) for child in node.get('children',[]))
            for record in selected:
                if not record['body_applicability']['required']:continue
                found=[i for i,n in enumerate(nodes) if n.get('name')==record['ref']]
                if len(found)!=1 or not has_mesh(found[0],set()):issues.append(record['ref']+': native GLB lacks a unique modeled occurrence')
        except (ValueError,KeyError,IndexError,struct.error) as error:issues.append('Invalid GLB assembly: '+str(error))
    if missing and not board_only:issues.append('Missing 3D bodies: '+', '.join(missing))
    if result.returncode or not valid:issues.append('Native 3D export failed or returned an invalid file')
    if any((v.file_hash(p) if Path(p).is_file() else 'MISSING')!=h for p,h in before.items()):issues.append('3D dependencies changed during export')
    return v.result('UNKNOWN' if issues else 'PASS','Nominal CAD appearance/geometry only; missing bodies are evidence gaps, not collision acceptance',issues,
                    file=str(out) if valid else None,format=format,view=view if format=='png' else None,image_size=image_size,sources=before,
                    output_sha256=v.file_hash(out) if valid else None,models=records,
                    returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
