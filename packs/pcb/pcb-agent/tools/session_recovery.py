"""Controller-only reconciliation of interrupted MCP actions, never replay.

An inherited flock keeps an orphaned tool from racing a new controller. Native
inspection establishes readability, not acceptance: a board with DRC failures
must remain editable so the model can repair the particular objects in place.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time


@contextlib.contextmanager
def lease(root, name='operation'):
    logs=Path(root)/'session'
    if logs.is_symlink() or not logs.is_dir() or logs.stat().st_uid!=0 or logs.stat().st_mode&0o022:
        raise ValueError('Recovery requires controller-owned session records')
    fd=os.open(logs/(name+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Session operation is still active; do not reconcile or replay') from error
        yield fd
    finally:
        # Do not LOCK_UN: a surviving child inherits this open file description.
        # It must retain the lease until its own exit after controller SIGKILL.
        os.close(fd)


def durable_write(path, value):
    from tools import workspace
    path=Path(path);workspace._write(path,value)
    with path.open('rb') as stream:os.fsync(stream.fileno())
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def snapshot_hash(files):
    return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def reconcile(root):
    """Called by a stopped-session controller after frozen identity validation.

    Validate actual CAD using the unprivileged tools, archive pending journals,
    and return facts to the continued model. A failed inspection, changed source
    or lost dependency leaves the pending records intact. No CAD is rewritten.
    """
    from tools import agent_session as session, kimi_mcp, model_runner
    root=Path(root).resolve();logs=root/'session'
    if Path.cwd().resolve()!=root:raise ValueError('Recovery must run in its original workspace')
    with lease(root) as fd:
        paths=sorted(logs.glob('pending-*.json'))
        if not paths:return None
        records=[]
        for path in paths:
            if path.is_symlink() or path.stat().st_uid!=0 or path.stat().st_mode&0o022:
                raise ValueError('Untrusted pending receipt')
            record=json.loads(path.read_text())
            if type(record.get('action'))!=int or record['action']<=0 or not isinstance(record.get('before'),dict):
                raise ValueError('Malformed pending receipt')
            # Older releases did not keep a process-held lease. Their orphaned
            # tools cannot be disproved by this release: preserve their freeze.
            if record.get('recovery_version')!=1:
                raise ValueError('Legacy interruption lacks a process lease; inspect using its frozen runtime')
            records.append((path,record))
        before=session.snapshot(root)
        checks=[]
        missing=sorted({name for _,r in records for name in r['before'] if name not in before})
        if missing:
            raise ValueError('Recovery source files disappeared; restore the retained candidate first: '+', '.join(missing))
        # Native readers run as the tool user. Controller does not import an
        # untrusted PCB/schematic/parser input under its privileged identity.
        inspections=[('project_status',{})]
        for name in sorted(before):
            if name.endswith('.kicad_sch'):
                inspections.append(('inspect_schematic',{'sheet':name}))
        if 'board.kicad_pcb' in before:inspections.append(('inspect_board',{'include_copper':True}))
        for name,args in inspections:
            result=kimi_mcp._execute(name,args,lease_fd=fd,actor='controller')
            checks.append(dict(tool=name,arguments=args,returncode=result['returncode']))
            if result['returncode']!=0:
                raise ValueError('Interrupted candidate is not readable: '+name+'; pending evidence retained; '+result.get('stderr','')[-2000:])
        after=session.snapshot(root)
        if before!=after:raise ValueError('Candidate changed during recovery inspection')
        recovered=[]
        for path,record in records:
            ident=path.stem.removeprefix('pending-');receipt=logs/('receipt-'+ident+'.json')
            if receipt.is_file():
                existing=json.loads(receipt.read_text())
                for key in ('action','tool','arguments'):
                    if existing.get(key)!=record.get(key):raise ValueError('Completed receipt differs from pending action')
                outcome=existing.get('observation',{})
                if outcome.get('after')!=after or outcome.get('before')!=record['before']:
                    raise ValueError('Completed receipt does not match current candidate')
                status='COMPLETED_RECEIPT_RETAINED'
            else:
                outcome=dict(returncode=None,stdout='',stderr='Controller interrupted; original process outcome is unknown.',
                             before=record['before'],after=after)
                outcome['operation_outcome']=session.operation_outcome(outcome)
                status='INTERRUPTED_OUTCOME_RECONCILED'
                durable_write(receipt,dict(state='interrupted',actor=record.get('actor','model'),action=record['action'],tool=record['tool'],
                    arguments=record.get('arguments',{}),observation=outcome))
            recovered.append(dict(action=record['action'],tool=record['tool'],status=status,
                                  operation_outcome=session.operation_outcome(outcome)))
        notice=dict(status='RECOVERED_FOR_LOCAL_REPAIR',recovered=recovered,inspections=checks,
            snapshot_sha256=snapshot_hash(after),board_sha256=after.get('board.kicad_pcb'),
            acceptance_status='NOT_EVALUATED',
            next_action='Do not replay interrupted edits. Inspect the listed changes and current UUIDs; rerun affected ERC/DRC/parity checks, then make the smallest local repair. Completed tool receipts are not design acceptance.')
        archive=logs/'recovery'/str(time.time_ns());archive.mkdir(parents=True)
        durable_write(archive/'reconciliation.json',notice)
        for path,record in records:
            durable_write(archive/path.name,record)
            path.unlink()
        durable_write(logs/'recovery-latest.json',notice)
        model_runner._log(logs/'model-trajectory.jsonl','controller_recovery',**notice)
        return notice
