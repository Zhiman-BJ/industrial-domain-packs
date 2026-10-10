"""Host-owned numerical backend protocol. Never turns missing coverage into PASS."""
from pathlib import Path
import argparse,json
from tools import numerical,field_solver


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    request=json.loads(Path(args.request).read_text());out=Path(args.output)
    try:
        category=request['category'];parameters=request['parameters']
        if category in ('pi','thermal'):result=numerical.run(category,request['board'],parameters)
        elif category in ('si','emc','rf'):result=field_solver.run('emc' if category=='rf' else category,request['board'],parameters,out.parent/'fdtd')
        else:raise ValueError('No numerical implementation for this category')
    except Exception as e:result=dict(status='UNKNOWN',method='native numerical adapter',measurements={},issues=[type(e).__name__+': '+str(e)])
    # Every successful board solver result is revision-bound.  The analysis
    # adapter independently rechecks these values against the files it passed
    # to us, so stale solver output cannot be promoted to a board verdict.
    result['request_sha256']=request['request_sha256']
    result['board_sha256']=request.get('board_sha256')
    result['schematic_sha256']=request.get('schematic_sha256')
    result['spec_sha256']=request.get('spec_sha256')
    result['conditions_sha256']=request.get('conditions_sha256')
    result.setdefault('evidence_kind','simulation')
    from tools.validation import file_hash
    result['evidence_sha256']={str(p.resolve()):file_hash(p) for p in out.parent.rglob('*') if p.is_file() and p!=out}
    result['evidence_sha256'][str(Path(request['board']).resolve())]=file_hash(request['board'])
    out.write_text(json.dumps(result,indent=2,allow_nan=False))

if __name__=='__main__':main()
