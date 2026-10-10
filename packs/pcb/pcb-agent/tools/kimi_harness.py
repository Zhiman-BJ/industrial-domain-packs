"""Launch the pinned, unmodified upstream Kimi Code CLI with only PCB MCP tools."""
import argparse,hashlib,json,os,re,signal,subprocess,threading,time,uuid
from pathlib import Path
from tools import agent_session,workspace as ws,task_contract,session_isolation,kimi_provider
from tools.runtime_limits import client_timeout_ms


def exit_reason(returncode,stream,max_steps):
    """Classify the pinned CLI's terminal record, never model-authored JSON.

    Upstream 1.50.0 prints MaxStepsReached as a plain final line even in
    stream-json mode. A nonzero exit remains unsuccessful, with a precise cause.
    """
    if returncode==0:return 'HARNESS_FINISHED'
    if returncode<0:return 'HARNESS_INTERRUPTED'
    with Path(stream).open('rb') as source:
        source.seek(max(0,source.seek(0,2)-4096))
        lines=source.read().decode('utf-8',errors='replace').splitlines()
    terminal=next((line.strip() for line in reversed(lines) if line.strip()),'')
    matched=re.fullmatch(r'Max number of steps reached: ([0-9]+)',terminal)
    # Only the CLI's terminal error line may name a transport failure. Never
    # derive termination from a model-authored JSON message containing a label.
    failure=re.fullmatch(r'Error: (REQUEST_DEADLINE|SESSION_DEADLINE|COMPACTION_BUDGET_EXHAUSTED|COMPACTION_INCOMPLETE|STREAM_IDLE_TIMEOUT|FIRST_BYTE_TIMEOUT|CONTROLLER_CANCELLED)',terminal)
    if failure:return failure[1]
    return 'STEP_BUDGET' if matched and int(matched[1])==max_steps else 'HARNESS_ERROR'


def run(args):
    root=Path(args.workspace).resolve();os.chdir(root)
    uid,gid=map(int,args.tool_user.split(':'))
    # Reserve new sessions atomically. On resume acquire the existing controller
    # lease before changing any permissions or traversing live candidate files.
    if not args.resume:(root/'session').mkdir(mode=0o755)
    from tools.session_recovery import lease
    with lease(root,'harness') as harness_fd:
        # A stopped CLI can leave an independently running native tool. Its
        # operation lease must also exclude permission preparation on resume.
        with lease(root):
            (root/'.pcb').mkdir(exist_ok=True)
            session_isolation.prepare(root,dict(uid=uid,gid=gid),resume=True)
        from tools import harness_recovery,model_runner
        policy_path=root/'session/automatic-resumes.json'
        history=json.loads(policy_path.read_text()) if policy_path.exists() else []
        while True:
            result=_run(args,root,harness_fd)
            elapsed=sum(json.loads(p.read_text()).get('elapsed_s',0) for p in (root/'session').glob('kimi-round-*.json'))
            if (result.get('native_steps_used',0)>=args.max_steps or
                    not harness_recovery.allowed(result,len(history),getattr(args,'max_auto_resumes',2),args.max_seconds-elapsed)):
                return result
            event=dict(after_round=result['round_id'],provider_error=result['recoverable_provider_error'],
                       remaining_s=args.max_seconds-elapsed,session_id=result['session_id'])
            history.append(event);ws._write(policy_path,history)
            model_runner._log(root/'session/model-trajectory.jsonl','automatic_resume',**event)
            # _run reconciles any pending operation before resuming the SAME native session.
            args=argparse.Namespace(**dict(vars(args),resume=True))


def _run(args,root,harness_fd):
    started=time.monotonic()
    logs=root/'session';logs.mkdir(exist_ok=True);identity=logs/'kimi-identity.json'
    public=task_contract.binding()
    # Design decisions retain thinking. Mechanical context summarization has
    # its own bounded budget and cannot silently consume the closing window.
    transport=dict(timeout=600,idle_timeout=120,compaction_timeout=120,
                   compaction_max_tokens=8192,compaction_thinking=False,recovery_reserve=120)
    binding=dict(model=args.model,task_sha256=hashlib.sha256(Path(args.task).read_bytes()).hexdigest(),
        toolset_sha256=agent_session.TOOLSET_SHA256,requirements=public,
        harness_version='1.50.0',request_options={'enable_thinking':True},transport=transport,
        max_steps=args.max_steps,max_seconds=args.max_seconds,max_auto_resumes=getattr(args,'max_auto_resumes',2))
    if args.resume:
        old=json.loads(identity.read_text())
        if old['binding']!=binding:raise ValueError('Resume requires original task, tools, model and budgets')
        session_id=old['session_id']
    else:
        if identity.exists():raise FileExistsError('Existing Kimi session: use explicit resume')
        session_id=str(uuid.uuid4());ws._write(identity,dict(binding=binding,session_id=session_id))
    skill=(Path(os.environ.get('PCB_SKILLS','/skills'))/'pcb-design-e2e/SKILL.md').read_text()
    system='You are designing a PCB using the public atomic tools. The immutable task contract is available through project_status. Candidate checks are diagnostic for uncompiled briefs. Give concise engineering reasons for decisions and use local repairs. Report design delivery, required simulation and measured hardware qualification separately. Never claim tests that have not executed. Tool names carry an MCP prefix.\n\n'+skill
    from tools import runtime_identity
    runtime_identity.record(logs,system)
    os.environ['PCB_TOOL_IDENTITY']=args.tool_user
    recovery=None
    if args.resume:
        from tools import session_recovery
        recovery=session_recovery.reconcile(root)
    from tools import harness_recovery
    remaining_steps=args.max_steps-harness_recovery.steps_used(logs)
    if remaining_steps<=0:raise ValueError('Cumulative native step budget exhausted')
    (logs/'kimi-system.md').write_text(system)
    (logs/'kimi-agent.yaml').write_text('version: 1\nagent:\n  name: pcb\n  system_prompt_path: ./kimi-system.md\n  tools: []\n')
    os.environ['PCB_TOOL_IDENTITY']=args.tool_user
    round_id=str(time.time_ns());os.environ['PCB_TRACE_ROUND_ID']=round_id
    mcp_env={k:v for k,v in os.environ.items() if k.startswith(('PCB_','XDG_')) or k in ('PYTHONPATH','FREEROUTING_JAR','JAVA','OPENEMS_INSTALL_PATH','CSXCAD_INSTALL_PATH')}
    ws._write(logs/'kimi-mcp.json',{'mcpServers':{'pcb':{'command':'/usr/bin/python3','args':['-m','tools.kimi_mcp'],'env':mcp_env}}})
    request_clock={}
    proxy=kimi_provider.server(args.endpoint,os.environ['MODEL_API_KEY'],args.model,logs/'provider.jsonl',
        **transport,remaining_seconds=lambda:max(0,request_clock.get('deadline',time.monotonic())-time.monotonic()),
        phase=lambda:kimi_provider.native_phase(logs))
    threading.Thread(target=proxy.serve_forever,daemon=True).start()
    # The actual API key is held by the private proxy; it never enters CLI config.
    private=Path('/tmp/pcb-kimi-private');private.mkdir(mode=0o700,exist_ok=True)
    config={'default_model':'pcb','default_thinking':True,
        'providers':{'pcb':{'type':'openai_legacy','base_url':f'http://127.0.0.1:{proxy.server_port}/v1','api_key':'local-adapter','reasoning_key':'reasoning_content'}},
        'models':{'pcb':{'provider':'pcb','model':args.model,'max_context_size':131072,'capabilities':['thinking']}},
        'loop_control':{'max_steps_per_turn':remaining_steps,'max_retries_per_step':6,'reserved_context_size':32768},
        'mcp':{'client':{'tool_call_timeout_ms':client_timeout_ms()}}}
    ws._write(private/'config.json',config)
    os.environ['KIMI_SHARE_DIR']=str(logs/'kimi-native')
    env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('API_KEY','TOKEN','SECRET','PASSWORD'))}
    prompt='Continue the same task. Inspect project_status and existing evidence before a local repair; preserve completed work.' if args.resume else Path(args.task).read_text()
    if recovery:prompt+='\nController recovery report (not design acceptance):\n'+json.dumps(recovery,ensure_ascii=False)
    command=['/opt/kimi/bin/kimi','--config-file',str(private/'config.json'),'--agent-file',str(logs/'kimi-agent.yaml'),
             '--mcp-config-file',str(logs/'kimi-mcp.json'),'--work-dir',str(root),'--session',session_id,
             '--thinking','--print','--output-format','stream-json','--max-steps-per-turn',str(remaining_steps)]
    from tools.session_recovery import durable_write
    # A killed controller used time even if it never wrote its terminal result.
    # Charge the unrecorded wall interval conservatively; never reset its budget.
    for marker in logs.glob('kimi-start-*.json'):
        terminal=logs/marker.name.replace('kimi-start-','kimi-round-')
        if not terminal.exists():
            start=json.loads(marker.read_text())
            elapsed=max(0,time.time()-start['started_at'])
            durable_write(terminal,dict(reason='CONTROLLER_INTERRUPTED',elapsed_s=min(elapsed,args.max_seconds),
                scope='Conservative elapsed wall time; provider outcome is unknown'))
    elapsed_before=sum(json.loads(p.read_text()).get('elapsed_s',0) for p in logs.glob('kimi-round-*.json'))
    remaining=args.max_seconds-elapsed_before-(time.monotonic()-started)
    if remaining<=0:raise ValueError('Cumulative session time budget exhausted')
    request_clock['deadline']=time.monotonic()+remaining
    provider_offset=(logs/'provider.jsonl').stat().st_size if (logs/'provider.jsonl').exists() else 0
    durable_write(logs/f'kimi-start-{round_id}.json',dict(started_at=time.time(),remaining_s=remaining))
    with (logs/f'kimi-stream-{round_id}.jsonl').open('w') as out,(logs/f'kimi-stderr-{round_id}.log').open('w') as err:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=out,stderr=err,env=env,text=True,start_new_session=True,pass_fds=(harness_fd,))
        try:
            process.communicate(prompt,timeout=remaining)
            reason=exit_reason(process.returncode,logs/f'kimi-stream-{round_id}.jsonl',remaining_steps)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            reason='TIME_BUDGET'
    proxy.cancel_requests()
    proxy.shutdown()
    proxy.server_close()
    receipts=[json.loads(p.read_text()) for p in logs.glob('receipt-*.json')]
    result=dict(harness='Kimi Code CLI',harness_version='1.50.0',model=args.model,session_id=session_id,
        round_id=round_id,reason=reason,returncode=process.returncode,
        recoverable_provider_error=harness_recovery.provider_failure(logs/'provider.jsonl',provider_offset),
        native_steps_used=harness_recovery.steps_used(logs),harness_finished=reason=='HARNESS_FINISHED',
        elapsed_s=time.monotonic()-started,request_options={'enable_thinking':True},
        actions=sum(r.get('actor','model')=='model' for r in receipts),
        controller_inspections=sum(r.get('actor')=='controller' for r in receipts),
        usage=[],note='Harness exit is not independent design acceptance')
    ws._write(logs/f'kimi-round-{round_id}.json',result)
    from tools import trajectory_index
    result['trajectory_index']=trajectory_index.build(logs)
    ws._write(logs/'result.json',dict(result,elapsed_s=elapsed_before+result['elapsed_s']))
    print(json.dumps(result),flush=True)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--workspace',default='/workspace');p.add_argument('--task',required=True)
    p.add_argument('--model',required=True);p.add_argument('--endpoint',required=True);p.add_argument('--tool-user',default='1000:1000')
    p.add_argument('--max-steps',type=int,default=100);p.add_argument('--max-seconds',type=int,default=7200);p.add_argument('--resume',action='store_true');p.add_argument('--max-auto-resumes',type=int,choices=range(0,4),default=2)
    result=run(p.parse_args())
    raise SystemExit(0 if result['harness_finished'] else 1)
if __name__=='__main__':main()
