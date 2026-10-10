"""Portable copies of a candidate project; never a verification or CAM export."""
from pathlib import Path
import json
import re
import shutil
import tempfile
from tools import library, sexpr, validation


def save(destination, project_name):
    root = Path.cwd().resolve(); target = (root/destination).resolve()
    if (not target.is_relative_to(root) or target == root or
            target.relative_to(root).parts[0] in ('session', '.pcb') or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', project_name)):
        raise ValueError('Use a new candidate subdirectory and a simple project name')
    if target.exists():
        raise FileExistsError('Project copy already exists; select a new destination')
    required = [root/('board'+ext) for ext in ('.kicad_pro', '.kicad_sch', '.kicad_pcb')]
    if not all(p.is_file() for p in required):
        raise ValueError('Generate all three native CAD files before copying a project')
    paths = library.project_source_paths(root)
    before = {str(p.relative_to(root)):validation.file_hash(p) for p in paths}
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent,prefix='.project-copy-') as directory:
        staged = Path(directory)/'project'; staged.mkdir()
        for source in paths:
            relative = source.relative_to(root)
            if relative.name.startswith('board.') and len(relative.parts)==1:
                relative = Path(project_name+relative.name[len('board'):])
            dest = staged/relative; dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dest)
        def rename(node):
            if not isinstance(node,list):return
            if len(node)>1 and node[0]=='project' and node[1]=='board':node[1]=sexpr.Quoted(project_name)
            for item in node:rename(item)
        for path in staged.rglob('*.kicad_sch'):
            doc=sexpr.parse(path.read_text());rename(doc);path.write_text(sexpr.dump(doc)+'\n')
        project=staged/(project_name+'.kicad_pro');doc=json.loads(project.read_text())
        if isinstance(doc.get('meta'),dict) and 'filename' in doc['meta']:doc['meta']['filename']=project.name
        project.write_text(json.dumps(doc,indent=2)+'\n')
        if before != {str(p.relative_to(root)):validation.file_hash(p) for p in library.project_source_paths(root)}:
            raise ValueError('Candidate changed during copy')
        staged.rename(target)
    return {'status':'COPIED','verification':'REQUIRED','project':str(target.relative_to(root)),
            'project_name':project_name,'source_hashes':before,
            'files':{str(p.relative_to(target)):validation.file_hash(p) for p in target.rglob('*') if p.is_file()}}
