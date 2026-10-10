"""Controller-only pause protocol, sharing the MCP operation boundary lock."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time
import uuid


def set_paused(root, paused, timeout=30):
    if os.geteuid()!=0:raise PermissionError('Demo control requires the trusted container controller')
    if type(paused) is not bool or not 0<timeout<=60:raise ValueError('Invalid pause request')
    session=Path(root)/'session';path=session/'demo-control.json';lock=session/'demo-control.lock'
    if session.is_symlink() or path.is_symlink() or lock.is_symlink():raise ValueError('Control paths cannot be symlinks')
    if session.stat().st_uid!=0 or session.stat().st_mode&0o022:raise PermissionError('Controller-owned session required')
    deadline=time.monotonic()+timeout
    with lock.open('a') as stream:
        while True:
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise TimeoutError('Operation boundary busy; pause not applied, retry after current tool completes')
                time.sleep(.05)
        data=json.loads(path.read_text());data['paused']=paused
        tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:tmp.write_text(json.dumps(data));tmp.replace(path)
        finally:tmp.unlink(missing_ok=True)
    return dict(paused=paused,boundary='BETWEEN_TOOL_OPERATIONS')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('pause','resume'))
    parser.add_argument('--workspace',default='/workspace');parser.add_argument('--timeout',type=float,default=30)
    args=parser.parse_args()
    print(json.dumps(set_paused(args.workspace,args.action=='pause',args.timeout)))
