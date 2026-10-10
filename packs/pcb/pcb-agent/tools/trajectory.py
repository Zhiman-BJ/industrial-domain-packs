"""Export exact observed transitions; never infer rewards from missing post-states."""
import argparse
import json
from pathlib import Path


def load_run(run_dir):
    d=Path(run_dir)
    if (d/'latest.json').exists():d=Path(json.loads((d/'latest.json').read_text())['attempt'])
    traj=d/'trajectory.jsonl';summary=d/'result.json';spec=d/'spec.json'
    return {'dir':str(d),'trajectory':[json.loads(l) for l in traj.read_text().splitlines() if l.strip()] if traj.exists() else [],
            'verify':json.loads(summary.read_text()) if summary.exists() else {},
            'spec':json.loads(spec.read_text()) if spec.exists() else None}


def rows_for(run):
    """Retain chronological observations and explicit actions with scoped outcomes."""
    final={'status':run['verify'].get('status','UNKNOWN'),'pass':run['verify'].get('pass',False),'reason':run['verify'].get('reason')}
    rows=[]
    for index,s in enumerate(run['trajectory']):
        rows.append({'run':run['dir'],'step':index,'kind':s.get('step'),
                     'state':{'sha256':s.get('before_sha256')},
                     'action':s.get('action') or ({'tool':s['tool']} if 'tool' in s else None),
                     'next_state':dict(s.get('next_state') or {},sha256=s.get('after_sha256')),
                     'observation':s,'reward':None,'final':final})
    return rows


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('runs',nargs='+');p.add_argument('--out',required=True);a=p.parse_args(argv)
    count=0
    with open(a.out,'w') as f:
        for path in a.runs:
            for row in rows_for(load_run(path)):
                f.write(json.dumps(row,ensure_ascii=False)+'\n');count+=1
    print(f'{count} observations exported; rewards intentionally unset')
    return 0

if __name__=='__main__':raise SystemExit(main())
