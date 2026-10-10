"""Optional native CAD observations for vision-capable model adapters."""
import base64,hashlib,subprocess,uuid
from pathlib import Path
from tools import kicad_cli


def observe(root, out_dir=None):
    import cairosvg
    root=Path(root);out=Path(out_dir) if out_dir else root/'session/observations'/uuid.uuid4().hex;out.mkdir(parents=True)
    content=[];records=[]
    for name,kind in [('board.kicad_sch','schematic'),('board.kicad_pcb','pcb')]:
        source=root/name
        if not source.is_file():continue
        target=out/kind;target.mkdir()
        args=['sch','export','svg','--output',str(target)+'/',str(source)] if kind=='schematic' else [
            'pcb','export','svg','--layers','F.Cu,F.Silkscreen,Edge.Cuts','--output',str(target/'board.svg'),str(source)]
        result=kicad_cli._run(args,timeout=60)
        if not result.ok:raise RuntimeError('Native visual observation failed: '+result.stderr[-500:])
        svgs=sorted(target.glob('*.svg'))
        if not svgs:raise ValueError('Native export produced no SVG')
        png=target/'view.png';cairosvg.svg2png(url=str(svgs[0]),write_to=str(png),output_width=1200)
        data=png.read_bytes()
        if len(data)>4*1024*1024:raise ValueError('Visual observation exceeds 4 MiB')
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        content.extend([{'type':'text','text':f'Current {kind}: {name}, SHA-256 {digest}. This is a partial page/layer view, not acceptance.'},
                        {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(data).decode()}}])
        records.append({'source':name,'source_sha256':digest,'image':str(png.relative_to(root)),
                        'image_sha256':hashlib.sha256(data).hexdigest()})
    return content,records
