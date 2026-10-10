"""Minimal stdio MCP transport for the canonical PCB tools, no alternate model loop.

The trusted controller owns receipts. Every public operation runs under the
unprivileged tool identity; Python retains the existing kernel sandbox.
"""
import hashlib,json,os,re,signal,subprocess,sys,time,uuid,tempfile
from pathlib import Path
from tools import agent_session as session,workspace as ws,model_runner,task_contract
from tools.argument_schema import error_observation

# An uncertain mutation freezes further edits, but must not block diagnosis.
# These operations inspect CAD/schema only; no engine, Python or export is allowed.
RECOVERY_INSPECTIONS=frozenset(('project_status','inspect_board','inspect_schematic',
    'inspect_tool','list_references','read_reference'))


def execute(name,args,trace_context=None):
    from tools.session_recovery import lease
    with lease(Path.cwd()) as lease_fd:
        return _controlled_execute(name,args,lease_fd,trace_context)


def _controlled_execute(name,args,lease_fd,trace_context=None):
    control=Path.cwd()/'session/demo-control.json'
    if control.is_file():
        import fcntl
        with control.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if json.loads(control.read_text()).get('paused'):
                raise RuntimeError('Human paused this session before the operation; no CAD mutation executed')
            return _execute(name,args,lease_fd,trace_context=trace_context)
    return _execute(name,args,lease_fd,trace_context=trace_context)


def _execute(name,args,lease_fd=None,*,actor='model',trace_context=None):
    trace_context=trace_context or {}
    root=Path.cwd();logdir=root/'session';trace=logdir/'model-trajectory.jsonl'
    if actor not in ('model','controller'):raise ValueError('Invalid operation actor')
    if actor=='controller' and name not in RECOVERY_INSPECTIONS:
        raise ValueError('Controller reconciliation permits read-only inspections only')
    session.validate_call(name,args)
    if actor=='model':
        from tools import observation_guard
        observation_guard.require(root,name,args)
    from tools.runtime_limits import tool_timeout
    timeout=tool_timeout(name)
    pending_records=[json.loads(p.read_text()) for p in sorted(logdir.glob('pending-*.json'))]
    if pending_records and name not in RECOVERY_INSPECTIONS:
        raise RuntimeError('AMBIGUOUS_TOOL_OUTCOME: use project_status, inspect_board or inspect_schematic to locate the current state; edits require controller reconciliation of the pending receipt')
    prior=[int(m[1]) for p in logdir.glob('action-*.*') if (m:=re.fullmatch(r'action-(\d+)\.(json|py|stdout|stderr)',p.name))]
    actions=max([0]+prior+[r.get('action',0) for r in pending_records])+1
    ident=uuid.uuid4().hex;prefix=logdir/f'action-{actions:04d}'
    uid,gid=map(int,os.environ['PCB_TOOL_IDENTITY'].split(':'))
    before=session.snapshot(root);started=time.monotonic()
    pending=logdir/f'pending-{ident}.json'
    from tools.session_recovery import durable_write
    durable_write(pending,dict(state='started',recovery_version=1,actor=actor,action=actions,tool=name,arguments=args,before=before,trace_context=trace_context))
    if name=='run_python':
        if set(args)!={'code'} or not isinstance(args['code'],str):
            pending.unlink();raise ValueError('run_python requires a code string')
        script=prefix.with_suffix('.py');script.write_text(args['code'])
        scratch=root/'.pcb/extensions'/ident;scratch.mkdir(parents=True);os.chown(scratch,uid,gid)
        command=[sys.executable,'-I',str(Path(__file__).with_name('python_extension.py')),
                 '--code',str(script),'--workspace',str(root),'--scratch',str(scratch)]
        bound=task_contract.binding()
        if bound:command.extend(['--requirements',bound['requirements_path']])
        stdin=None
    else:
        command=[sys.executable,'-m','tools.workspace'];stdin=json.dumps(dict(name=name,arguments=args))
        prefix.with_suffix('.json').write_text(stdin)
    # Use an allowlist: neither provider credentials nor private harness config
    # paths are propagated to any public tool process.
    allowed={'PATH','PYTHONPATH','PCB_AGENT_HOME','PCB_SKILLS','FREEROUTING_JAR','JAVA',
             'OPENEMS_INSTALL_PATH','CSXCAD_INSTALL_PATH','PCB_PUBLIC_REQUIREMENTS',
             'PCB_REQUIREMENTS_SHA256','PCB_REQUIREMENTS_REQUIRED','PCB_DOWNLOAD_PREFIXES',
             'PCB_ANALYSIS_BACKENDS','DISPLAY','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME'}
    env={k:v for k,v in os.environ.items() if k in allowed}
    env.update(PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    # Native KiCad needs a writable user profile even in a headless subprocess.
    # Do not inherit the controller's home, credentials or user configuration.
    with tempfile.TemporaryDirectory(prefix='pcb-tool-profile-') as profile:
        os.chown(profile,uid,gid)
        env.update(HOME=profile,XDG_CONFIG_HOME=profile+'/config',
                   XDG_CACHE_HOME=profile+'/cache',XDG_DATA_HOME=profile+'/data')
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,cwd=root,env=env,user=uid,group=gid,extra_groups=[],start_new_session=True,
            # Never give user-authored Python an open controller descriptor.
            # Its kernel sandbox is read-only for all candidate source files.
            pass_fds=() if lease_fd is None or name=='run_python' else (lease_fd,))
        try:stdout,stderr=process.communicate(stdin,timeout=timeout);rc=process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);stdout,stderr=process.communicate();rc=124
            stderr+='\nTool timeout. Inspect persisted artifacts before retrying.'
    prefix.with_suffix('.stdout').write_text(stdout);prefix.with_suffix('.stderr').write_text(stderr)
    observation=dict(returncode=rc,stdout=stdout,stderr=stderr,before=before,after=session.snapshot(root),
                     elapsed_s=time.monotonic()-started,timeout_s=timeout,
                     stdout_path=str(prefix.with_suffix('.stdout')),stderr_path=str(prefix.with_suffix('.stderr')))
    # The extension has no CAD verdict; expose an execution-only status so its
    # observation cannot be mistaken for a design check or acceptance result.
    if name=='run_python':
        observation.update(status='PASS' if rc==0 else 'ERROR', scope='sandboxed_python_execution',
                           verification_role='observation_only',
                           next_action='Use named CAD tools for edits and rerun the affected check; Python output is diagnostic only.')
    observation['operation_outcome']=session.operation_outcome(observation)
    observation['trace_context']=trace_context
    if pending_records:
        observation['recovery']={'status':'INSPECTION_ONLY','pending_actions':[
            {'action':r.get('action'),'tool':r.get('tool'),
             'observed_changes':session.operation_outcome({'before':r.get('before',{}),'after':observation['after']})}
            for r in pending_records],
            'next_action':'Controller must reconcile the interrupted action before CAD edits resume. Inspection does not clear a pending receipt.'}
    if name=='run_python':observation.update(extension_policy='kernel_sandbox_readonly_candidate',scratch_path=str(scratch))
    if name in ('verify_design','finalize_claims'):
        captured=session.first_verification_snapshot(root,actions,name)
        if captured:model_runner._log(trace,'first_verification_snapshot',**captured)
    durable_write(logdir/f'receipt-{ident}.json',dict(state='completed',actor=actor,action=actions,tool=name,arguments=args,observation=observation,trace_context=trace_context))
    pending.unlink()
    model_runner._log(trace,'tool_action' if actor=='model' else 'controller_inspection',actor=actor,
                      tool=name,arguments=args,observation=observation,action=actions,**trace_context)
    if actor=='model':
        try:
            from tools import visualize
            visualize.capture(root,actions,name,args,observation)
        except Exception as error:model_runner._log(trace,'frame_error',error=str(error))
    return observation


def main():
    for line in sys.stdin:
        request={}
        try:
            request=json.loads(line);method=request['method'];params=request.get('params',{})
            if 'id' not in request:continue
            if method=='initialize':result={'protocolVersion':'2024-11-05','capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'pcb-atomic-tools','version':'1.0.0'}}
            elif method=='tools/list':result={'tools':[{'name':x['function']['name'],'description':x['function']['description'],'inputSchema':x['function']['parameters']} for x in session.TOOLS]}
            elif method=='tools/call':
                try:
                    context={'round_id':os.environ.get('PCB_TRACE_ROUND_ID'),'mcp_request_id':request['id']}
                    observation=execute(params['name'],params.get('arguments',{}),context)
                    delivered=session.compact_observation(observation)
                    from tools import observation_guard
                    from tools.session_recovery import lease
                    with lease(Path.cwd()):observation_guard.record(Path.cwd(),params['name'],delivered)
                    result={'content':[{'type':'text','text':json.dumps(delivered,ensure_ascii=False)}],'isError':observation['returncode']!=0}
                    if params['name']=='view_design' and not result['isError'] and os.environ.get('PCB_MODEL_VISION')=='1':
                        import base64
                        records=json.loads(observation['stdout']).get('images',[])
                        for record in records:
                            path=(Path.cwd()/record['image']).resolve()
                            if not path.is_relative_to(Path.cwd()/'.pcb/views') or path.suffix!='.png':raise ValueError('Invalid visual observation path')
                            data=path.read_bytes()
                            if hashlib.sha256(data).hexdigest()!=record['image_sha256'] or len(data)>4*1024*1024:raise ValueError('Visual observation hash/size mismatch')
                            result['content'].append(dict(type='image',mimeType='image/png',data=base64.b64encode(data).decode()))
                        model_runner._log(Path.cwd()/'session/model-trajectory.jsonl','model_visual_observation',images=records)
                except Exception as error:
                    context={'round_id':os.environ.get('PCB_TRACE_ROUND_ID'),'mcp_request_id':request.get('id')}
                    observation={'returncode':1,'stdout':json.dumps(error_observation(error,params.get('name'))),'stderr':'','trace_context':context}
                    model_runner._log(Path.cwd()/'session/model-trajectory.jsonl','dispatch_error',tool=params.get('name'),arguments=params.get('arguments',{}),error=str(error),observation=observation,request_id=request.get('id'),**context)
                    result={'content':[{'type':'text','text':json.dumps(session.compact_observation(observation),ensure_ascii=False)}],'isError':True}
                model_runner._log(Path.cwd()/'session/model-trajectory.jsonl','model_observation',
                                  round_id=context['round_id'],mcp_request_id=context['mcp_request_id'],content=result['content'],isError=result['isError'])
            elif method=='ping':result={}
            else:raise ValueError('Unsupported method: '+method)
            response={'jsonrpc':'2.0','id':request['id'],'result':result}
        except Exception as error:response={'jsonrpc':'2.0','id':request.get('id'),'error':{'code':-32600,'message':str(error)}}
        print(json.dumps(response,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
