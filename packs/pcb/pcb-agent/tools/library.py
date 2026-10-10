"""General local CAD libraries and provenance-tracked downloads, independent of tasks.

Fetch archives from an explicit public upstream revision. Never execute contents,
never rewrite format versions to pretend compatibility, and retain license files.
"""
from __future__ import annotations
import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
import ipaddress
import socket
from tools.sexpr import parse, children, child


def create_symbol(library_id: str, pins: list[dict], *, description: str = '') -> dict:
    """Create an unqualified workspace symbol draft using validated pin geometry."""
    from tools import authoring
    return authoring.create_symbol(library_id, pins, description)


def create_footprint(library_id: str, pads: list[dict], *, description: str = '') -> dict:
    """Create an unqualified workspace footprint draft using validated pad geometry."""
    from tools import authoring
    return authoring.create_footprint(library_id, pads, description)


def roots():
    """Explicit PCB_LIBRARY_ROOT first, then standard and locally installed libraries."""
    env=[Path(p) for p in os.environ.get('PCB_LIBRARY_ROOT','').split(os.pathsep) if p]
    local=Path(__file__).resolve().parents[2]/'parts'/'installed'
    installed=sorted(local.glob('*'),reverse=True) if local.is_dir() else []
    workspace=sorted((Path(os.environ.get('PCB_WORKSPACE',str(Path.cwd())))/'.pcb/libraries').glob('qualified-*'))
    return env+workspace+installed+[Path('/usr/share/kicad'),Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport')]


def symbol_dirs():return [str(p/'symbols') for p in roots() if (p/'symbols').is_dir()]
def footprint_dirs():return [str(p/'footprints') for p in roots() if (p/'footprints').is_dir()]


def refresh():
    """Refresh process-local search indexes after a qualified component is installed."""
    from tools import schematic, datasheet_search
    schematic.SYMBOL_DIRS = symbol_dirs()
    datasheet_search.SYMBOL_DIRS = symbol_dirs()
    datasheet_search.FOOTPRINT_DIRS = footprint_dirs()
    for name in ('_symbols', '_footprints'):
        fn = getattr(datasheet_search, name, None)
        if hasattr(fn, 'cache_clear'): fn.cache_clear()


def write_project_tables(spec, out_dir):
    """Build native library tables from the same per-identity resolver as Python."""
    from tools import schematic
    from tools.sexpr import dump, Quoted
    out=Path(out_dir).resolve();cache=out/'.pcb/cad-libraries';symbols={};footprints={}
    for part in spec['parts']:
        lib,name=part['symbol'].split(':',1)
        symbols.setdefault(lib,{})[name]=schematic.resolved_symbol(lib,name)
        identity=part.get('footprint')
        if identity and not part.get('schematic_only'):
            flib,fname=identity.split(':',1)
            source=next((Path(d)/(flib+'.pretty')/(fname+'.kicad_mod') for d in footprint_dirs()
                         if (Path(d)/(flib+'.pretty')/(fname+'.kicad_mod')).is_file()),None)
            if source is None:raise FileNotFoundError(identity)
            footprints.setdefault(flib,{})[fname]=source
    entries={'sym_lib_table':{},'fp_lib_table':{}}
    for lib,nodes in symbols.items():
        target=cache/'symbols'/(lib+'.kicad_sym');target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(dump(['kicad_symbol_lib',['version','20241209'],['generator',Quoted('pcb-agent')],*nodes.values()]))
        entries['sym_lib_table'][lib]=target
    for lib,files in footprints.items():
        directory=cache/'footprints'/(lib+'.pretty');directory.mkdir(parents=True,exist_ok=True)
        for name,source in files.items():shutil.copy2(source,directory/(name+'.kicad_mod'))
        entries['fp_lib_table'][lib]=directory
    for kind,libs in entries.items():
        target=out/('sym-lib-table' if kind=='sym_lib_table' else 'fp-lib-table')
        # Preserve unrelated project-specific libraries while refreshing managed ones.
        existing=children(parse(target.read_text()),'lib') if target.is_file() else []
        rows=[entry for entry in existing if str(child(entry,'name')[1]) not in libs]
        rows += [['lib',['name',Quoted(lib)],['type',Quoted('KiCad')],['uri',Quoted('${KIPRJMOD}/'+str(path.relative_to(out))) ],
                  ['options',Quoted('')],['descr',Quoted('project CAD resolved by pcb-agent')]] for lib,path in libs.items()]
        target.write_text(dump([kind,['version','7'],*rows])+'\n')


def copy_project_libraries(source_dir, destination_dir):
    """Retain project-relative CAD dependencies when copying a design artifact."""
    from tools import hierarchy
    for schematic in Path(source_dir).glob('*.kicad_sch'):
        hierarchy.copy_sheets(schematic,destination_dir)
    source=Path(source_dir)/'.pcb/cad-libraries'
    if source.is_dir():shutil.copytree(source,Path(destination_dir)/'.pcb/cad-libraries',dirs_exist_ok=True)


def project_source_paths(root):
    """Portable CAD and declared solver inputs for revision snapshots."""
    root=Path(root).resolve()
    names=('spec.json','board.kicad_sch','board.kicad_pcb','board.kicad_pro','board.kicad_dru',
           'sym-lib-table','fp-lib-table','schematics','.pcb/cad-libraries','.pcb/models','.pcb/libraries','.pcb/3dmodels','.pcb/authoring')
    paths=set()
    for name in names:
        source=root/name
        for path in source.rglob('*') if source.is_dir() else [source]:
            if path.is_file():paths.add(path)
    if (root/'spec.json').is_file():
        from tools import analysis,task_contract
        spec=json.loads((root/'spec.json').read_text())
        for effective in (spec,task_contract.effective_spec(spec,task_contract.load())):
            for name in analysis.input_hashes(effective,root/'board.kicad_sch',root/'board.kicad_pcb',root,include_backends=False):
                path=Path(name)
                if path.is_absolute() and path.is_relative_to(root) and path.is_file():paths.add(path)
    for path in list(paths):
        if not path.resolve().is_relative_to(root):raise ValueError('Candidate dependency escapes workspace')
        # Preserve the caller's lexical spelling for symlinked temporary
        # directories on macOS (``/var`` vs ``/private/var``). The canonical
        # path remains the containment check; the alias is only a portable
        # snapshot key for the same file.
        if str(path).startswith('/private/'):
            lexical=Path(str(path)[len('/private'):])
            if lexical.is_file():paths.add(lexical)
    return sorted(paths)


def _public_url(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use an HTTPS public upstream URL without credentials')
    if parsed.port not in (None,443):raise ValueError('Artifact downloads require the standard HTTPS port')
    decoded=urllib.parse.unquote(parsed.path)
    if '\\' in decoded or '%' in decoded or any(part in ('.','..') for part in decoded.split('/')):
        raise ValueError('Artifact URL path cannot contain traversal or ambiguous encoding')
    addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Artifact source resolves to a nonpublic address')
    # Controller-configured prefixes may restrict a comparison to approved
    # manufacturer/library sources. Never load this policy from model spec.
    raw=os.environ.get('PCB_DOWNLOAD_PREFIXES')
    if raw:
        prefixes=json.loads(raw)
        if not isinstance(prefixes,list) or not prefixes:raise ValueError('Empty download allowlist')
        if not any(isinstance(p,str) and p.endswith('/') and url.startswith(p) for p in prefixes):
            raise ValueError('Artifact URL is outside controller-approved sources')


class _PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        _public_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def _download(url,destination,max_bytes=512*1024*1024):
    _public_url(url)
    digest=hashlib.sha256();total=0
    request=urllib.request.Request(url,headers={'User-Agent':'pcb-design-e2e/1.0 (artifact acquisition)'})
    with urllib.request.build_opener(_PublicRedirect()).open(request,timeout=60) as r,open(destination,'wb') as f:
        _public_url(r.url)
        # A CDN may return a bounded fragment even without a Range request.
        # A hash of those bytes identifies the fragment, not the source document.
        if r.status!=200 or r.headers.get('Content-Range'):
            raise ValueError('Artifact download requires a complete HTTP 200 response')
        length=r.headers.get('Content-Length')
        if length is not None:
            if not re.fullmatch(r'[0-9]+',length.strip()):raise ValueError('Invalid artifact Content-Length')
            length=int(length)
            if length>max_bytes:raise ValueError('Archive exceeds download limit')
        while True:
            data=r.read(1024*1024)
            if not data:break
            total+=len(data)
            if total>max_bytes:raise ValueError('Archive exceeds download limit')
            digest.update(data);f.write(data)
        if not total or (length is not None and total!=length):
            raise ValueError('Incomplete artifact download: received bytes differ from declared length or are empty')
    return digest.hexdigest()


def install_archive(url, destination, kind, revision, license_url, expected_sha256=None, max_files=100000):
    """Stage a symbol/footprint tar archive safely; no install over existing files.

    The result is syntax-checked CAD, not a KiCad-runtime-qualified component set.
    Caller supplies source/revision/license identity. For immutable upstream
    commits use the commit hash in the download URL and revision field.
    """
    if kind not in ('symbols','footprints'):raise ValueError('kind must be symbols or footprints')
    if not revision or not license_url:raise ValueError('Revision and license source required')
    dest=Path(destination).resolve()
    if dest.exists():raise FileExistsError(dest)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.library-',dir=dest.parent) as tmp:
        tmp=Path(tmp);archive=tmp/'upstream.tar.gz';digest=_download(url,archive)
        if expected_sha256 and digest!=expected_sha256:raise ValueError('Archive hash mismatch')
        stage=tmp/'stage';stage.mkdir();count=0;size=0;license_files=[]
        with tarfile.open(archive,'r:*') as tar:
            for m in tar:
                parts=Path(m.name).parts
                if not parts or Path(m.name).is_absolute() or '..' in parts:raise ValueError('Unsafe archive path')
                if m.isdir():continue
                if not m.isfile():raise ValueError('Links and special archive entries are not accepted')
                count+=1;size+=m.size
                if count>max_files or size>2*1024**3:raise ValueError('Archive extraction limits exceeded')
                # Preserve the whole archive including licensing/docs under a normalized root.
                rel=Path(*parts[1:]) if len(parts)>1 else Path(parts[0])
                target=stage/rel
                if target.exists():raise ValueError('Duplicate archive entry')
                target.parent.mkdir(parents=True,exist_ok=True)
                src=tar.extractfile(m)
                with src,open(target,'wb') as f:shutil.copyfileobj(src,f)
                if any(k in target.name.lower() for k in ('license','copying','copyright')):license_files.append(str(rel))
        pattern='*.kicad_sym' if kind=='symbols' else '*.kicad_mod'
        files=sorted(stage.rglob(pattern))
        if not files:raise ValueError('No expected CAD files in archive')
        if not license_files:raise ValueError('Archive has no license/copyright files; preserve upstream license evidence first')
        count_entities=0;versions=set();content=hashlib.sha256()
        for p in files:
            b=p.read_bytes();node=parse(b.decode())
            expected='kicad_symbol_lib' if kind=='symbols' else 'footprint'
            if node[0]!=expected:raise ValueError(f'{p.name}: unsupported CAD root {node[0]}')
            version=child(node,'version');versions.add(str(version[1]) if version else 'unspecified')
            count_entities+=len(children(node,'symbol')) if kind=='symbols' else 1
            content.update(str(p.relative_to(stage)).encode()+b'\0'+hashlib.sha256(b).digest())
        manifest={'schema_version':1,'source_url':url,'revision':revision,'archive_sha256':digest,
                  'cad_content_sha256':content.hexdigest(),'license_url':license_url,'license_files':license_files,
                  'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'kind':kind,
                  'cad_files':len(files),'entities':count_entities,'format_versions':sorted(versions),
                  'validation':{'syntax':'PASS','kicad_runtime':'UNKNOWN','datasheet_geometry':'UNKNOWN'}}
        (stage/'library-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        os.replace(stage,dest)
    return manifest


def installed_revision():
    """Read the immutable image's native version; retain the pre-receipt baseline."""
    receipt=Path('/opt/kicad-version.json')
    revision=json.loads(receipt.read_text())['kicad_version'] if receipt.is_file() else '9.0.9'
    if not re.fullmatch(r'\d+\.\d+\.\d+',revision):raise ValueError('Invalid image KiCad version')
    return revision


def install_official(destination, revision=None):
    """Fetch official KiCad libraries pinned to each tag's immutable commit."""
    revision=revision or installed_revision()
    dest=Path(destination);dest.mkdir(parents=True,exist_ok=True);records={}
    for kind in ('symbols','footprints'):
        folder=dest/kind
        if folder.exists():
            manifest=folder/'library-manifest.json'
            if not manifest.exists():raise ValueError(f'{folder} exists without provenance')
            record=json.loads(manifest.read_text())
            if record.get('requested_tag')!=revision:raise ValueError('Installed tag differs')
            records[kind]=record;continue
        project=f'kicad/libraries/kicad-{kind}'
        api='https://gitlab.com/api/v4/projects/'+urllib.parse.quote(project,safe='')
        with urllib.request.urlopen(api+'/repository/tags/'+urllib.parse.quote(revision,safe=''),timeout=30) as r:meta=json.load(r)
        commit=meta['commit']['id']
        record=install_archive(api+'/repository/archive.tar.gz?sha='+commit,folder,kind,commit,'https://gitlab.com/'+project+'/-/blob/'+commit+'/LICENSE.md')
        record['requested_tag']=revision;(folder/'library-manifest.json').write_text(json.dumps(record,indent=2))
        records[kind]=record
        print(json.dumps({'installed':kind,'entities':record['entities'],'commit':commit}),flush=True)
    return records


def fetch_artifact(url, destination, kind, revision, license_url, expected_sha256=None, archive_member=None, archive_sha256=None):
    """Download a missing CAD file or datasheet with provenance; do not install it blindly.

    kind: symbol, footprint, datasheet, spice_model. S-expression syntax is checked before
    publication. Runtime format, inherited dependencies, pin mapping and package
    geometry still require validation. No file is overwritten.
    """
    if kind not in ('symbol','footprint','datasheet','spice_model'): raise ValueError('Unsupported artifact kind')
    if not revision or not license_url: raise ValueError('Source revision and license URL required')
    target=Path(destination).resolve()
    metadata=Path(str(target)+'.source.json')
    if target.is_file() and metadata.is_file() and expected_sha256:
        saved=json.loads(metadata.read_text())
        if (hashlib.sha256(target.read_bytes()).hexdigest()==expected_sha256==saved.get('sha256')
                and all(saved.get(k)==value for k,value in dict(source_url=url,kind=kind,revision=revision,license_url=license_url).items())
                and saved.get('archive_member')==archive_member and saved.get('archive_sha256')==archive_sha256):
            return dict(saved,cache_hit=True)
    if target.exists(): raise FileExistsError(target)
    if metadata.exists(): raise FileExistsError(metadata)
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.download-',dir=target.parent) as tmp:
        staged=Path(tmp)/'artifact';digest=_download(url,staged,max_bytes=64*1024*1024)
        archive_record={}
        if archive_member is not None:
            if not archive_sha256 or digest!=archive_sha256:raise ValueError('Matching ZIP archive hash required')
            if Path(archive_member).is_absolute() or '..' in Path(archive_member).parts:raise ValueError('Unsafe ZIP member')
            with zipfile.ZipFile(staged) as archive:
                matches=[m for m in archive.infolist() if m.filename==archive_member]
                if len(matches)!=1 or matches[0].is_dir() or matches[0].file_size>64*1024*1024:raise ValueError('One bounded regular ZIP member required')
                data=archive.read(matches[0])
            archive_record={'archive_sha256':digest,'archive_member':archive_member}
            staged.write_bytes(data);digest=hashlib.sha256(data).hexdigest()
        elif archive_sha256:raise ValueError('archive_sha256 requires archive_member')
        if expected_sha256 and digest!=expected_sha256: raise ValueError('Artifact hash mismatch')
        if kind in ('symbol','footprint'):
            node=parse(staged.read_text());expected='kicad_symbol_lib' if kind=='symbol' else 'footprint'
            if node[0]!=expected: raise ValueError('Unexpected CAD format')
        elif kind=='spice_model':
            text=staged.read_text()
            if not re.search(r'^\s*\.(model|subckt)\s',text,re.I|re.M):raise ValueError('Expected SPICE .model or .subckt')
            if re.search(r'^\s*\.(control|include|inc|lib|shell|exec|quit|end)\b',text,re.I|re.M):raise ValueError('Only flat SPICE model packages without commands/includes are supported')
        elif not staged.read_bytes().startswith(b'%PDF-'): raise ValueError('Datasheet response is not PDF; use discover_artifacts on the landing-page URL, then select a sourced PDF link')
        record={'source_url':url,'revision':revision,'license_url':license_url,'sha256':digest,**archive_record,
                'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'kind':kind,
                'validation':{'syntax':'PASS','runtime':'UNKNOWN','datasheet_geometry':'UNKNOWN'}}
        os.replace(staged,target);metadata.write_text(json.dumps(record,indent=2))
    return record


def discover_artifacts(url, out_dir):
    """Read bounded public HTML and retain link evidence; never execute scripts."""
    from html.parser import HTMLParser
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=False)
    page=out/'source.html'
    digest=_download(url,page,max_bytes=2*1024*1024)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.urls=[]
        def handle_starttag(self,tag,attrs):
            if tag not in ('a','link','iframe','embed'):return
            values=dict(attrs);href=values.get('href') or values.get('src')
            if href:self.urls.append(urllib.parse.urljoin(url,href))
    parser=Links();parser.feed(page.read_text(errors='replace'))
    prefixes=json.loads(os.environ.get('PCB_DOWNLOAD_PREFIXES','[]'));links=[]
    for link in dict.fromkeys(parser.urls):
        parsed=urllib.parse.urlparse(link)
        if parsed.scheme!='https' or parsed.username or parsed.password:continue
        if not re.search(r'\.(pdf|zip|lib|cir|spice|kicad_sym|kicad_mod)(?:$|[?#])|datasheet|download',link,re.I):continue
        links.append(dict(url=link,source_allowed=not prefixes or any(link.startswith(p) for p in prefixes)))
        if len(links)>=40:break
    report=dict(status='FOUND' if links else 'NO_LINKS',source_url=url,source_path=str(page),source_sha256=digest,
        candidates=links,next_action='Select the matching revision/document and fetch_artifact; a discovered link is not qualified evidence')
    (out/'links.json').write_text(json.dumps(report,indent=2));return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--destination',required=True);p.add_argument('--revision');a=p.parse_args()
    print(json.dumps(install_official(a.destination,a.revision),indent=2))
