"""Private loopback transport; upstream Kimi Code owns reasoning and tools.

Relay successful responses unchanged. Bound header, stream-idle and total time
independently; never record credentials, prompts or upstream error bodies here.
"""
import hashlib
import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tools.model_runner import _log


def native_phase(logs):
    """Use native wire events, not prompt text or absence of tools, for phase."""
    wires=list(logs.glob('kimi-native/sessions/*/*/wire.jsonl'))
    if not wires:return 'agent'
    path=max(wires,key=lambda p:p.stat().st_mtime_ns)
    with path.open('rb') as source:
        source.seek(max(0,source.seek(0,2)-65536))
        lines=source.read().decode('utf-8',errors='replace').splitlines()
    for line in reversed(lines):
        try:kind=json.loads(line).get('message',{}).get('type')
        except ValueError:continue
        if kind=='CompactionBegin':return 'compaction'
        if kind in ('CompactionEnd','StepBegin','TurnBegin'):return 'agent'
    return 'agent'


def request_budget(stage, remaining, *, timeout, compaction_timeout, recovery_reserve):
    """Leave a closing window; once inside it, let the actor actually use it."""
    limit=min(timeout,compaction_timeout) if stage=='compaction' else timeout
    mode=stage
    if remaining is not None:
        remaining=max(0,remaining)
        if stage=='compaction':
            # Summarization must not consume the actor's final reporting window
            # or most of the time left for the actual task, even after retries.
            limit=min(limit,remaining/3,max(0,remaining-recovery_reserve))
        elif remaining<=2*recovery_reserve:
            mode='closing'
            # The old remaining/2 rule repeatedly withheld the closing budget
            # from the request that needed it. Reserve only receipt flush time.
            limit=min(limit,max(0,remaining-min(2,remaining*.02)))
        else:
            limit=min(limit,max(0,remaining-recovery_reserve))
    return max(0,limit),mode


def runtime_notice(stage, mode, remaining):
    if stage=='compaction':
        return ('Runtime compaction budget: produce a concise continuation summary. Preserve the immutable task scope, current board revision, exact unresolved object IDs and report paths, completed checks and pending actions. Do not repeat full tool inventories or infer missing evidence. This summary is not an engineering verdict.')
    if remaining is None:return None
    notice=f'Runtime time remaining: {max(0,remaining):.1f} seconds. This is a time budget, not engineering acceptance.'
    if mode=='closing':
        notice+=' Closing window: preserve current work, perform only essential checks that fit, then give a concise factual final report of completed work and unresolved evidence. Do not expand scope or repeat unchanged passing checks. Submit claims only if the fixed task requires them.'
    return notice


def server(endpoint,key,model,log,*,max_tokens=16384,timeout=600,
           idle_timeout=120,compaction_timeout=120,compaction_max_tokens=8192,
           remaining_seconds=None,phase=None,recovery_reserve=120,compaction_thinking=False):
    if min(max_tokens,timeout,idle_timeout,compaction_timeout,compaction_max_tokens)<=0 or recovery_reserve<0:
        raise ValueError('Positive provider budgets required')
    stopping=threading.Event()
    active=threading.Condition();active_ids=set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def do_POST(self):
            if self.path!='/v1/chat/completions':self.send_error(404);return
            try:payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            except (ValueError,KeyError):self.send_error(400);return
            if payload.get('model')!=model:self.send_error(400,'Model mismatch');return
            stage=phase() if phase else 'agent'
            token_limit=compaction_max_tokens if stage=='compaction' else max_tokens
            remaining=remaining_seconds() if remaining_seconds else None
            total_limit,budget_phase=request_budget(stage,remaining,timeout=timeout,
                compaction_timeout=compaction_timeout,recovery_reserve=recovery_reserve)
            notice=runtime_notice(stage,budget_phase,remaining)
            if notice:
                messages=list(payload.get('messages') or [])
                if messages and messages[0].get('role')=='system' and isinstance(messages[0].get('content'),str):
                    messages[0]=dict(messages[0],content=messages[0]['content']+'\n\n'+notice)
                else:messages.insert(0,{'role':'system','content':notice})
                payload['messages']=messages
            thinking=compaction_thinking if stage=='compaction' else True
            payload['enable_thinking']=thinking
            payload['max_tokens']=token_limit
            payload.pop('reasoning_effort',None)
            raw=json.dumps(payload).encode()
            ident=uuid.uuid4().hex
            started=time.monotonic()
            deadline=started+total_limit
            tool_names=[x.get('function',{}).get('name') for x in payload.get('tools',[])]
            _log(log,'provider_request',request_id=ident,phase=stage,model=model,
                 enable_thinking=thinking,max_tokens=token_limit,total_timeout_s=total_limit,
                 idle_timeout_s=idle_timeout,remaining_session_s=remaining,
                 budget_phase=budget_phase,runtime_notice=notice,
                 payload_sha256=hashlib.sha256(raw).hexdigest(),stream=payload.get('stream'),
                 tool_names=tool_names)
            events=queue.Queue(maxsize=8)
            cancelled=threading.Event()
            holder={}
            status=None;sent=False;received=0;first=None;last=None;upstream_id=None

            def publish(item):
                while not cancelled.is_set():
                    try:events.put(item,timeout=.1);return
                    except queue.Full:pass

            def fetch():
                try:
                    request=urllib.request.Request(endpoint,data=raw,headers={
                        'Authorization':'Bearer '+key,'Content-Type':'application/json'})
                    with urllib.request.urlopen(request,timeout=max(.01,min(idle_timeout,total_limit))) as response:
                        holder['response']=response
                        publish(('headers',(response.status,dict(response.headers))))
                        while not cancelled.is_set():
                            chunk=response.read1(65536)
                            if not chunk:break
                            publish(('data',chunk))
                        publish(('end',None))
                except urllib.error.HTTPError as error:
                    publish(('http_error',error.code));error.close()
                except Exception as error:
                    publish(('error',type(error).__name__))

            def headers(code,content_type):
                self.send_response(code)
                self.send_header('Content-Type',content_type)
                self.send_header('Connection','close')
                self.end_headers()

            worker=threading.Thread(target=fetch,daemon=True)
            content_type='application/json';body=[]
            sse_pending=b'';saw_done=False;content_chars=0;finish_reason=None
            with active:active_ids.add(ident)
            try:
                if total_limit<=0:
                    raise TimeoutError('COMPACTION_BUDGET_EXHAUSTED' if stage=='compaction' and remaining and remaining>0 else 'SESSION_DEADLINE')
                worker.start()
                while True:
                    now=time.monotonic()
                    if stopping.is_set():raise TimeoutError('CONTROLLER_CANCELLED')
                    if now>=deadline:raise TimeoutError('REQUEST_DEADLINE')
                    if now-(last if last is not None else started)>=idle_timeout:
                        raise TimeoutError('STREAM_IDLE_TIMEOUT' if first is not None else 'FIRST_BYTE_TIMEOUT')
                    try:kind,value=events.get(timeout=min(.1,deadline-now))
                    except queue.Empty:continue
                    if kind=='headers':
                        status,upstream_headers=value
                        upstream_headers={k.lower():v for k,v in upstream_headers.items()}
                        content_type=upstream_headers.get('content-type','text/event-stream' if payload.get('stream') else 'application/json')
                        upstream_id=upstream_headers.get('x-request-id') or upstream_headers.get('request-id')
                        _log(log,'provider_headers',request_id=ident,phase=stage,status=status,
                             upstream_request_id=upstream_id,elapsed_s=time.monotonic()-started)
                    elif kind=='data':
                        last=time.monotonic();received+=len(value)
                        if first is None:
                            first=last
                            _log(log,'provider_first_byte',request_id=ident,phase=stage,elapsed_s=first-started)
                        if payload.get('stream'):
                            sse_pending+=value
                            while b'\n' in sse_pending:
                                line,sse_pending=sse_pending.split(b'\n',1)
                                if not line.startswith(b'data:'):continue
                                data=line[5:].strip()
                                if data==b'[DONE]':
                                    if stage=='compaction' and (not content_chars or finish_reason=='length'):
                                        raise RuntimeError('COMPACTION_INCOMPLETE')
                                    saw_done=True;continue
                                try:event=json.loads(data)
                                except ValueError:continue
                                if event.get('error'):raise RuntimeError('UPSTREAM_STREAM_ERROR')
                                for choice in event.get('choices',[]):
                                    content_chars+=len(choice.get('delta',{}).get('content') or '')
                                    finish_reason=choice.get('finish_reason') or finish_reason
                            if not sent:headers(status,content_type);sent=True
                            self.wfile.write(value);self.wfile.flush()
                        else:body.append(value)
                    elif kind=='end':
                        if payload.get('stream') and not saw_done:raise RuntimeError('INCOMPLETE_STREAM')
                        if not sent:headers(status or 200,content_type);sent=True
                        if body:self.wfile.write(b''.join(body));self.wfile.flush()
                        break
                    elif kind=='http_error':
                        status=value;raise RuntimeError('UPSTREAM_HTTP_ERROR')
                    else:raise RuntimeError(value)
                _log(log,'provider_response',request_id=ident,phase=stage,status=status,
                     elapsed_s=time.monotonic()-started,bytes_received=received,
                     finish_reason=finish_reason,content_chars=content_chars,
                     first_byte_s=first-started if first is not None else None,
                     last_byte_s=last-started if last is not None else None,
                     upstream_request_id=upstream_id)
            except (OSError,TimeoutError,RuntimeError) as error:
                failure=str(error) if isinstance(error,(TimeoutError,RuntimeError)) else type(error).__name__
                _log(log,'provider_error',request_id=ident,phase=stage,status=status,error=failure,
                     elapsed_s=time.monotonic()-started,bytes_received=received,
                     finish_reason=finish_reason,content_chars=content_chars,
                     first_byte_s=first-started if first is not None else None,
                     last_byte_s=last-started if last is not None else None,
                     upstream_request_id=upstream_id)
                message=json.dumps({'error':{'type':'provider_transport_error','message':failure,'request_id':ident}}).encode()
                try:
                    if not sent:headers(status if status and status>=400 else 504,'application/json');self.wfile.write(message)
                    elif payload.get('stream'):
                        # SSE errors raise in the client, preventing successful
                        # adoption of a truncated call or compaction summary.
                        self.wfile.write(b'\n\ndata: '+message+b'\n\n')
                    self.wfile.flush()
                except OSError:pass
            finally:
                cancelled.set()
                response=holder.get('response')
                if response is not None:
                    try:response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                    except (AttributeError,OSError):pass
                self.close_connection=True
                with active:active_ids.discard(ident);active.notify_all()

    proxy=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    def cancel_requests():
        stopping.set()
        with active:active.wait_for(lambda:not active_ids,timeout=2)
    proxy.cancel_requests=cancel_requests
    return proxy
