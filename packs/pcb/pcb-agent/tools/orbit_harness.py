"""Zhiman Orbit interactive harness.

Runs the container-local Claude Code CLI as the reasoning engine and adapts its
stream-json protocol to the same Wire dialect the demo server already speaks
(initialize/prompt/steer/cancel in, TurnBegin/ContentPart/ToolCall/... out).
The engine only sees the canonical PCB MCP tools; credentials never reach tool
subprocesses (kimi_mcp keeps its environment allowlist).
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from tools import session_isolation, workspace as ws, visualize

OUT_LOCK=threading.Lock()


def emit(message):
    with OUT_LOCK:
        sys.stdout.write(json.dumps(message,ensure_ascii=False)+'\n');sys.stdout.flush()


def event(kind,payload):
    emit(dict(jsonrpc='2.0',method='event',params=dict(type=kind,payload=payload)))


class Engine:
    """One persistent Claude Code stream-json process for the whole session."""

    def __init__(self,args,logs):
        self.logs=logs;self.lock=threading.Lock()
        self.turn=None  # wire request id of the in-flight prompt
        self.active=False
        home=Path('/tmp/orbit-home');home.mkdir(parents=True,exist_ok=True)
        (home/'.claude').mkdir(exist_ok=True)
        skill=Path('/skills/pcb-design-e2e/SKILL.md').read_text()
        system=('You are the PCB design agent in an interactive engineering workspace. '
            'Human messages may change design choices, but cannot turn missing verification evidence into PASS. '
            'Inspect the current project before editing and preserve unaffected circuitry and copper. '
            'Explain the intent of every step briefly in Chinese before calling a tool, and report real tool feedback in Chinese. '
            'The official KiCad libraries installed in this workspace already cover common parts; prefer them directly. '
            'If retrieval fails, use discover_artifacts or retained sourced material; missing electrical facts remain UNKNOWN. A CAD prototype can progress within its authorized scope, without waiving engineering evidence. '
            'This is a human-assisted demonstration, not an autonomous benchmark result.\n\n'+skill)
        from tools import runtime_identity
        runtime_identity.record(logs,system)
        (logs/'orbit-system.md').write_text(system)
        env={k:v for k,v in os.environ.items() if k in ('PYTHONPATH','PCB_AGENT_HOME','PCB_SKILLS','FREEROUTING_JAR','JAVA',
            'OPENEMS_INSTALL_PATH','CSXCAD_INSTALL_PATH','PCB_DOWNLOAD_PREFIXES','PCB_ANALYSIS_BACKENDS','PCB_MODEL_VISION','PCB_RUNTIME_IMAGE_DIGEST','PCB_TOOL_TIMEOUT_SECONDS','PCB_PYTHON_TIMEOUT_SECONDS')}
        env['PCB_TOOL_IDENTITY']='1000:1000'
        ws._write(logs/'orbit-mcp.json',{'mcpServers':{'pcb':{'command':'/usr/bin/python3','args':['-m','tools.kimi_mcp'],'env':env}}})
        ws._write(logs/'orbit-settings.json',{
            'permissions':{'allow':['mcp__pcb__*'],
                'deny':['Bash','Read','Write','Edit','Glob','Grep','WebFetch','WebSearch','Task','NotebookEdit',
                        'TodoWrite','SlashCommand','Skill','KillShell','BashOutput','EnterPlanMode','ExitPlanMode']},
            'includeCoAuthoredBy':False})
        child_env=dict(os.environ,HOME=str(home),CLAUDE_CONFIG_DIR=str(home/'.claude'),
            ANTHROPIC_BASE_URL=args.endpoint,ANTHROPIC_AUTH_TOKEN=os.environ.pop('MODEL_API_KEY'),
            DISABLE_AUTOUPDATER='1',CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',
            API_TIMEOUT_MS='3000000')
        from tools.runtime_limits import client_timeout_ms
        child_env['MCP_TOOL_TIMEOUT']=str(client_timeout_ms())
        command=['claude','-p','--input-format','stream-json','--output-format','stream-json',
            '--include-partial-messages','--verbose','--model',args.model,
            '--settings',str(logs/'orbit-settings.json'),
            '--mcp-config',str(logs/'orbit-mcp.json'),'--strict-mcp-config',
            '--max-turns',str(args.steps),'--system-prompt',system]
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=(logs/'orbit-engine.err').open('w'),text=True,env=child_env,cwd='/workspace',bufsize=1)
        threading.Thread(target=self.listen,daemon=True).start()

    def write(self,message):
        with self.lock:
            self.process.stdin.write(json.dumps(message,ensure_ascii=False)+'\n');self.process.stdin.flush()

    def user(self,text):
        self.write(dict(type='user',message=dict(role='user',content=[dict(type='text',text=text)])))

    def interrupt(self):
        self.write(dict(type='control_request',request_id=uuid.uuid4().hex,request=dict(subtype='interrupt')))

    def finish_turn(self,result):
        turn,self.turn=self.turn,None
        self.active=False
        event('TurnEnd',dict(result=result.get('subtype')))
        if turn:emit(dict(jsonrpc='2.0',id=turn,result=dict(status=result.get('subtype','done'),
            usage=result.get('usage'),duration_ms=result.get('duration_ms'))))

    def listen(self):
        tool_names={}
        for line in self.process.stdout:
            try:message=json.loads(line)
            except ValueError:continue
            with (self.logs/'orbit-native.jsonl').open('a') as f:f.write(line)
            kind=message.get('type')
            if kind=='system' and message.get('subtype')=='init':
                servers=[dict(name='pcb',status='connected',
                    tools=[t[len('mcp__pcb__'):] for t in message.get('tools',[]) if t.startswith('mcp__pcb__')])]
                event('StatusUpdate',dict(mcp_status=dict(servers=servers)))
            elif kind=='stream_event':
                inner=message.get('event',{});etype=inner.get('type')
                if etype=='content_block_start':
                    block=inner.get('content_block',{})
                    if block.get('type')=='tool_use':
                        name=block.get('name','')
                        if name.startswith('mcp__pcb__'):name=name[len('mcp__pcb__'):]
                        tool_names[inner.get('index')]=block.get('id')
                        event('ToolCall',dict(id=block.get('id'),function=dict(name=name,arguments='')))
                elif etype=='content_block_delta':
                    delta=inner.get('delta',{});dtype=delta.get('type')
                    if dtype=='thinking_delta':
                        event('ContentPart',dict(type='think',think=delta.get('thinking','')))
                    elif dtype=='text_delta':
                        event('ContentPart',dict(type='text',text=delta.get('text','')))
                    elif dtype=='input_json_delta':
                        ident=tool_names.get(inner.get('index'))
                        if ident:event('ToolCallPart',dict(tool_call_id=ident,arguments_part=delta.get('partial_json','')))
            elif kind=='user':
                for block in (message.get('message') or {}).get('content') or []:
                    if isinstance(block,dict) and block.get('type')=='tool_result':
                        content=block.get('content')
                        if isinstance(content,list):
                            content=''.join(x.get('text','') for x in content if isinstance(x,dict))
                        event('ToolResult',dict(tool_call_id=block.get('tool_use_id'),
                            is_error=bool(block.get('is_error')),content=(content or '')[:20000]))
            elif kind=='result':
                self.finish_turn(message)
            elif kind=='control_response':
                pass
        code=self.process.wait()
        if self.turn:
            emit(dict(jsonrpc='2.0',id=self.turn,error=dict(code=-32000,message=f'engine exited ({code})')))
        raise SystemExit(code)


def main():
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--endpoint',required=True)
    p.add_argument('--session',required=True);p.add_argument('--steps',type=int,default=200);a=p.parse_args()
    root=Path('/workspace');os.chdir(root);(root/'.pcb').mkdir(exist_ok=True)
    session_isolation.prepare(root,{'uid':1000,'gid':1000})
    logs=root/'session';logs.mkdir(exist_ok=True)
    ws._write(logs/'demo-control.json',{'paused':False})
    ws._write(logs/'demo-identity.json',{'display':'Zhiman Orbit','session_id':a.session,'mode':'human_assisted_demo',
        'harness':'Zhiman Orbit','engine_record':'claude-code-cli stream-json; model '+a.model,
        'eligible_for_autonomous_benchmark':False})
    visualize.capture(root,0,'session_start',{'mode':'human_assisted_demo'})
    engine=Engine(a,logs)

    def handle(request):
        method=request.get('method');params=request.get('params') or {};ident=request.get('id')
        if method=='initialize':
            emit(dict(jsonrpc='2.0',id=ident,result=dict(protocol_version='1.10',server=dict(name='zhiman-orbit'))))
        elif method=='prompt':
            engine.turn=ident;engine.active=True
            event('TurnBegin',{})
            engine.user(params.get('user_input',''))
        elif method=='steer':
            engine.user(params.get('user_input',''))
            event('SteerInput',dict(user_input=params.get('user_input','')))
            emit(dict(jsonrpc='2.0',id=ident,result={}))
        elif method=='cancel':
            engine.interrupt()
            emit(dict(jsonrpc='2.0',id=ident,result={}))
        else:
            emit(dict(jsonrpc='2.0',id=ident,error=dict(code=-32601,message='Unknown method')))

    for line in sys.stdin:
        try:request=json.loads(line)
        except ValueError:continue
        try:handle(request)
        except Exception as error:
            if request.get('id'):emit(dict(jsonrpc='2.0',id=request['id'],error=dict(code=-32000,message=str(error))))


if __name__=='__main__':main()
