"""Bounded continuation policy derived only from trusted provider/wire events."""
import json
from pathlib import Path

TRANSIENT = frozenset(('FIRST_BYTE_TIMEOUT','STREAM_IDLE_TIMEOUT','REQUEST_DEADLINE',
    'INCOMPLETE_STREAM','UPSTREAM_STREAM_ERROR','IncompleteRead','TimeoutError',
    'ConnectionResetError','RemoteDisconnected','URLError'))


def provider_failure(log, start_offset):
    path=Path(log)
    if not path.exists():return None
    with path.open('rb') as stream:
        stream.seek(start_offset);data=stream.read()
    terminals=[]
    for line in data.splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if event.get('event') in ('provider_response','provider_error') and event.get('error')!='CONTROLLER_CANCELLED':
            terminals.append(event)
    if not terminals or terminals[-1].get('event')!='provider_error':return None
    last=terminals[-1];status=last.get('status')
    if (last.get('error') in TRANSIENT and (status is None or status<400)) or status in (408,429,500,502,503,504):
        return {k:last.get(k) for k in ('request_id','error','status')}
    return None


def steps_used(logs):
    """Count native StepBegin records across persisted turns, never model text."""
    count=0
    for path in Path(logs).glob('kimi-native/sessions/*/*/wire.jsonl'):
        with path.open() as stream:
            for line in stream:
                try:row=json.loads(line)
                except ValueError:continue
                if row.get('message',{}).get('type')=='StepBegin':count+=1
    return count


def allowed(result, used_resumes, maximum, remaining):
    return (result.get('returncode',0)>0 and result.get('reason') not in
        ('TIME_BUDGET','STEP_BUDGET','SESSION_DEADLINE','COMPACTION_BUDGET_EXHAUSTED')
        and bool(result.get('recoverable_provider_error')) and used_resumes<maximum and remaining>30)
