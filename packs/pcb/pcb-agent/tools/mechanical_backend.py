"""Exact nominal STEP solid checks behind the trusted external-analysis protocol."""
from pathlib import Path
import argparse,hashlib,json,math,re
from tools import mechanical


def load(path):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    reader=STEPControl_Reader()
    if reader.ReadFile(str(path))!=IFSelect_RetDone or reader.TransferRoots()==0:
        raise ValueError('STEP transfer failed: '+str(path))
    shape=reader.OneShape()
    if shape.IsNull() or not TopExp_Explorer(shape,TopAbs_SOLID).More() or not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError('STEP requires valid solid geometry: '+str(path))
    return shape


def occurrence(path,ref):
    """Select a native named occurrence, including its KiCad placement transform."""
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDataStd import TDataStd_Name
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    doc=TDocStd_Document(TCollection_ExtendedString('MDTV-XCAF'))
    reader=STEPCAFControl_Reader();reader.SetNameMode(True)
    if reader.ReadFile(str(path))!=IFSelect_RetDone or not reader.Transfer(doc):raise ValueError('STEP assembly transfer failed')
    tool=XCAFDoc_DocumentTool.ShapeTool_s(doc.Main());roots=TDF_LabelSequence();tool.GetFreeShapes(roots)
    if roots.Length()!=1:raise ValueError('Expected one KiCad root assembly')
    root=roots.Value(1)
    if not tool.GetLocation_s(root).IsIdentity():raise ValueError('Unexpected transformed assembly root')
    components=TDF_LabelSequence();tool.GetComponents_s(root,components,False);matches=[]
    for i in range(1,components.Length()+1):
        label=components.Value(i);name=TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID_s(),name) and name.Get().ToExtString()==ref:
            matches.append(tool.GetShape_s(label))
    if len(matches)!=1:raise ValueError(ref+': native STEP occurrence missing or ambiguous')
    shape=matches[0]
    if shape.IsNull() or not TopExp_Explorer(shape,TopAbs_SOLID).More() or not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError(ref+': invalid/non-solid STEP occurrence')
    return shape


def bounds(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box=Bnd_Box();BRepBndLib.AddOptimal_s(shape,box,False,False)
    if box.IsVoid() or box.IsOpen():raise ValueError('Unbounded/empty STEP shape')
    values=box.Get()
    if not all(math.isfinite(v) for v in values):raise ValueError('Nonfinite STEP bounds')
    return list(values)


def pair(a,b):
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    distance=BRepExtrema_DistShapeShape(a,b);distance.Perform()
    if not distance.IsDone():raise ValueError('STEP distance calculation did not complete')
    common=BRepAlgoAPI_Common(a,b);common.Build()
    if not common.IsDone():raise ValueError('STEP intersection calculation did not complete')
    properties=GProp_GProps();BRepGProp.VolumeProperties_s(common.Shape(),properties)
    return dict(clearance_mm=distance.Value(),overlap_mm3=abs(properties.Mass()))


def evaluate(request,out):
    params=request['parameters'];board=Path(request['board']).resolve();out=Path(out);out.mkdir(parents=True,exist_ok=False)
    inventory=mechanical.inventory(board);actual={r['ref']:r for r in inventory if not r['dnp'] and r['body_applicability']['required']}
    expected=params['model_sha256']
    if set(expected)!=set(actual):raise ValueError('Pin model hashes for every populated body; DNP and copper-only applicability follow actual CAD and installed libraries')
    for ref,r in actual.items():
        hashes=[m['sha256'] for m in r['models']]
        if not hashes or 'MISSING' in hashes or hashes!=expected[ref]:
            raise ValueError(ref+': 3D model coverage/hash differs from original mechanical contract')
    shapes={};exports={}
    def component(ref):
        if ref in shapes:return shapes[ref]
        if ref!='@board' and ref not in actual:raise ValueError('Unknown STEP object: '+ref)
        # Safe deterministic filename even for unusual legal KiCad references.
        path=out/(hashlib.sha256(ref.encode()).hexdigest()[:16]+'.step')
        result=mechanical.export(board,path,'step',refs=None if ref=='@board' else [ref],board_only=ref=='@board')
        if result['status']!='PASS':raise ValueError('Incomplete STEP export: '+ref+' '+str(result['issues'])+' '+result['stderr'][-1200:]+' '+result['stdout'][-1200:])
        shapes[ref]=load(path) if ref=='@board' else occurrence(path,ref)
        exports[ref]=result;return shapes[ref]
    for name,item in params.get('external_solids',{}).items():
        if not name.startswith('@') or name=='@board':raise ValueError('External object names start with @, except @board')
        path=(board.parent/item['file']).resolve()
        if (not path.is_file() or mechanical.v.file_hash(path)!=item['sha256']
                or request['design_sources'].get(str(path))!=item['sha256']):
            raise ValueError('External STEP must be hash-pinned in test inputs: '+name)
        # External fixtures/enclosures must already share the KiCad STEP frame.
        # Do not invent translation, orientation, scale or millimetre conversion.
        if item.get('coordinate_frame')!='kicad_step_mm':raise ValueError('Declare external STEP in kicad_step_mm coordinates')
        shapes[name]=load(path)
    values={};detail={};checks=params['checks']
    if not isinstance(checks,dict) or not checks:raise ValueError('Explicit named mechanical checks required')
    for name,check in checks.items():
        if not re.fullmatch('[A-Za-z_][A-Za-z_0-9]*',name):raise ValueError('Invalid mechanical check ID')
        if check['kind']=='pair':
            a,b=check['a'],check['b']
            if a==b:raise ValueError('Two distinct objects required')
            def get(ref):return shapes[ref] if ref in shapes else component(ref)
            result=pair(get(a),get(b));detail[name]=dict(a=a,b=b,**result)
            values.update({name+'_'+k:v for k,v in result.items()})
        elif check['kind']=='bounds':
            ref=check['object'];shape=shapes[ref] if ref in shapes else component(ref)
            result=dict(zip(('xmin_mm','ymin_mm','zmin_mm','xmax_mm','ymax_mm','zmax_mm'),bounds(shape)))
            detail[name]=dict(object=ref,**result);values.update({name+'_'+k:v for k,v in result.items()})
        else:raise ValueError('Supported mechanical checks: pair or bounds')
    if set(values)!=set(request['assertions']):raise ValueError('Bound every computed clearance, overlap and envelope metric')
    if not all(math.isfinite(x) for x in values.values()):raise ValueError('Nonfinite mechanical measurement')
    return dict(status='PASS',method='KiCad STEP export + OpenCascade solid distance/intersection',
                measurements=values,details=detail,model_sha256=expected,body_inventory=inventory,
                exports={ref:dict(file=r['file'],sha256=r['output_sha256'],sources=r['sources']) for ref,r in exports.items()},
                scope='Only declared pairs/envelopes in nominal STEP geometry; no unspecified pairs, tolerances, insertion motion, heat or electrical/EMC claim')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--output',required=True);a=parser.parse_args()
    request=json.loads(Path(a.request).read_text())
    try:result=evaluate_bound(request,Path(a.output).parent/'mechanical')
    except Exception as error:result=dict(status='UNKNOWN',method='nominal STEP assembly check',issues=[type(error).__name__+': '+str(error)])
    result.update({k:request.get(k) for k in ('request_sha256','board_sha256','schematic_sha256','spec_sha256','conditions_sha256')})
    result.setdefault('evidence_sha256',{})[str(Path(a.request).resolve())]=mechanical.v.file_hash(a.request)
    result['evidence_kind']='geometry'
    Path(a.output).write_text(json.dumps(result,indent=2,allow_nan=False))


def evaluate_bound(request,out):
    """Retain the exact native inputs and STEP outputs checked by this solver."""
    from tools import design
    payload={k:v for k,v in request.items() if k!='request_sha256'}
    if hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()!=request.get('request_sha256'):
        raise ValueError('Mechanical request hash mismatch')
    if design.fingerprint(request['spec'])!=request['spec_sha256']:
        raise ValueError('Mechanical specification hash mismatch')
    dependencies={k:v for k,v in request['design_sources'].items() if k!='spec'}
    for key in ('board','schematic'):
        dependencies[str(Path(request[key]).resolve())]=request[key+'_sha256']
    def unchanged():
        for name,digest in dependencies.items():
            path=Path(name)
            actual=mechanical.v.file_hash(path) if path.is_file() else 'MISSING'
            if actual!=digest:raise ValueError('Mechanical input changed: '+name)
    unchanged()
    result=evaluate(request,out)
    unchanged()
    evidence={k:v for k,v in dependencies.items() if v!='MISSING'}
    for record in result['exports'].values():
        evidence[record['file']]=record['sha256']
        evidence.update(record['sources'])
    if any(not Path(name).is_file() or mechanical.v.file_hash(name)!=digest for name,digest in evidence.items()):
        raise ValueError('Mechanical retained evidence changed')
    result['evidence_sha256']=evidence
    return result


if __name__=='__main__':main()
