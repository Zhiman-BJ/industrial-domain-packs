"""Provider-neutral model loop with public tools, progress, retries and checkpoints.

Tool subprocesses run without credentials. Only failed model HTTP requests may
be retried automatically; an interrupted tool with unknown outcome is not replayed.
Container permissions define isolation. Session completion is not CAD acceptance.
"""
import argparse,hashlib,json,os,re,shutil,signal,socket,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
from tools import model_runner,workspace as ws,task_contract
from tools.argument_schema import decode_arguments, error_observation, validate as validate_arguments, InputError

PYTHON_TOOL={'type':'function','function':{'name':'run_python','description':'Sandboxed Python calculation. Candidate files are read-only; write derived data only under os.environ["PCB_EXTENSION_SCRATCH"]. Runtime/evaluator source, subprocesses and networking are unavailable. Use named tools to edit CAD, retrieve parts and run supported solvers.',
 'parameters':{'type':'object','properties':{'code':{'type':'string'}},'required':['code'],'additionalProperties':False}}}


def build_tools(groups=None):
    """Build the model-visible registry for a session.

    Empty selection preserves the historical full toolset.  ``run_python`` is
    always present as a read-only calculator, but remains unable to mutate CAD
    or inspect controller/evaluator sources.
    """
    return ws.definitions(groups=groups)+[PYTHON_TOOL]


def configured_groups():
    value=os.environ.get('PCB_TOOL_GROUPS','')
    return ws.parse_tool_groups(value) if value.strip() else None


TOOLS=build_tools(configured_groups())
TOOLSET_SHA256=hashlib.sha256(json.dumps(TOOLS,sort_keys=True).encode()).hexdigest()


def validate_call(name,args):
    """Reject public transport mistakes before receipts or mutations begin."""
    definition=next((t['function'] for t in TOOLS if t['function']['name']==name),None)
    try:
        if definition is None:
            raise InputError('UNKNOWN_TOOL','name',name,{'source':'public tool registry'},'Select a name from the advertised tool definitions.')
        validate_arguments(args,definition['parameters'])
    except InputError as error:
        error.operation_executed=False
        raise


def snapshot(root):
    """Hash candidate source and generated CAD inputs, pruning runtime state.

    3D models are source dependencies, so they must participate in progress and
    stale-candidate detection while session/reports remain excluded.
    """
    extensions={'.json','.kicad_sch','.kicad_pcb','.kicad_pro','.kicad_dru','.xml','.csv','.cir','.ses','.dsn',
                '.step','.stp','.wrl','.wrz','.glb','.gltf','.obj','.fcstd','.iges','.igs','.png',
                '.lib','.spice','.ibs','.s1p','.s2p','.s4p','.kicad_sym','.kicad_mod','.yaml','.yml'}
    ignored={'session','.config','independent-validation','reports','artifacts','__pycache__'}
    dependencies={'cad-libraries','models','libraries','3dmodels','authoring'}
    result={}
    for base,dirs,files in os.walk(root):
        dirs[:]=sorted(d for d in dirs if d not in ignored)
        relative_base=Path(base).relative_to(root)
        if relative_base==Path('.pcb'):dirs[:]=[d for d in dirs if d in dependencies]
        for name in sorted(files):
            path=Path(base)/name
            in_dependency=len(relative_base.parts)>1 and relative_base.parts[0]=='.pcb' and relative_base.parts[1] in dependencies
            if relative_base==Path('.pcb'):continue
            if not in_dependency and path.suffix.lower() not in extensions and name not in ('sym-lib-table','fp-lib-table'):continue
            if not path.resolve().is_relative_to(root.resolve()):raise ValueError('Candidate dependency escapes workspace')
            rel=str(path.relative_to(root))
            result[rel]=hashlib.sha256(path.read_bytes()).hexdigest()
    # Declared solver inputs may be PDFs or other source formats, and may live
    # outside the standard library folders. Keep them across candidate copies
    # and recovery, while retaining the controller/report isolation boundary.
    spec_path=root/'spec.json'
    if spec_path.is_file():
        try:spec=json.loads(spec_path.read_text())
        except (ValueError,UnicodeError):spec={}
        plan=spec.get('analysis',{}) if isinstance(spec,dict) else {}
        tests=plan.get('tests',[]) if isinstance(plan,dict) else []
        from tools.analysis import declared_input_paths
        from tools import task_contract
        public=task_contract.load()
        fixed=public.get('requirements',{}).get('analysis',{}) if public else {}
        for path in declared_input_paths({'tests':tests+fixed.get('tests',[])},root).values():
            if path.is_file():
                result[str(path.relative_to(root.resolve()))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def first_verification_snapshot(root, action, tool):
    """Preserve the first explicit final-check submission for later private scoring.

    This contains only the candidate, never a reference design or a checker.
    The receipt hashes let offline scoring detect later candidate-copy changes.
    """
    # TemporaryDirectory on macOS commonly returns ``/var/...`` while
    # ``Path.resolve()`` returns ``/private/var/...``.  Normalize both ends
    # before deriving a relative path so dependency snapshots (including
    # ``.pcb/3dmodels``) are portable across hosts.
    root_path=Path(root)
    # Keep the caller's spelling for output paths (important for tests and
    # symlinked temporary directories), while using the canonical spelling for
    # containment and relative-path calculations.
    root_path=root_path.absolute()
    root=root_path.resolve()
    destination=root_path/'session/first-verification'
    marker=root_path/'session/first-verification.json'
    if destination.exists() or marker.exists():return None
    destination.mkdir(parents=True)
    hashes={}
    from tools.library import project_source_paths
    for source in project_source_paths(root):
        source=Path(source).resolve()
        relative=source.relative_to(root)
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
        target.chmod(0o644)
        hashes[str(relative)]=hashlib.sha256(target.read_bytes()).hexdigest()
    data={'action':action,'tool':tool,'files':hashes,'definition':'First explicit verify_design/finalize_claims candidate; offline original-contract scoring excludes final claims'}
    ws._write(marker,data)
    return data


def request_with_retry(payload,endpoint,log,deadline,attempts=4,request_timeout=900):
    """Retry transient transport/HTTP failures only; preserve every attempt and deadline."""
    if attempts<1 or request_timeout<=0:raise ValueError('Positive API attempt/timeout values required')
    adapter=os.environ.get('MODEL_COMMAND')
    if adapter:
        command=json.loads(adapter)
        if not isinstance(command,list) or not command or not all(isinstance(x,str) for x in command):raise ValueError('MODEL_COMMAND must be a JSON argv array')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Session time budget exhausted')
        result=model_runner.run_command(command,json.dumps(payload),log,timeout=min(request_timeout,remaining))
        if result['status']!='PASS':raise RuntimeError('Model adapter failed; inspect model trajectory')
        return json.loads(result['stdout'])
    for attempt in range(attempts):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Session time budget exhausted')
        started=time.monotonic()
        model_runner._log(log,'model_request',attempt=attempt,timeout_s=min(request_timeout,remaining),payload=payload)
        req=urllib.request.Request(endpoint,json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ.get('MODEL_API_KEY','')})
        try:
            with urllib.request.urlopen(req,timeout=min(request_timeout,remaining)) as response:
                raw=json.load(response)
            model_runner._log(log,'model_response',response=raw);return raw
        except (OSError,urllib.error.URLError,ValueError) as e:
            detail={}
            if isinstance(e,urllib.error.HTTPError):
                try:
                    body=e.read(8192).decode('utf-8',errors='replace')
                    for key,value in os.environ.items():
                        if value and any(tag in key.upper() for tag in ('KEY','TOKEN','SECRET','PASSWORD')):
                            body=body.replace(value,'[REDACTED]')
                    detail={'http_status':e.code,'response_body':body,
                            'request_id':e.headers.get('x-request-id') if e.headers else None}
                except (OSError,AttributeError):detail={'http_status':e.code}
            transient=(isinstance(e,urllib.error.HTTPError) and e.code in (408,429,500,502,503,504)) or (not isinstance(e,urllib.error.HTTPError) and isinstance(e,(TimeoutError,socket.timeout,urllib.error.URLError,ConnectionError)))
            delay=min(60,2**attempt)
            if isinstance(e,urllib.error.HTTPError) and e.code==429:
                delay=min(60,15*2**attempt)
                try:delay=max(delay,float(e.headers.get('Retry-After',0)))
                except (TypeError,ValueError,AttributeError):pass
            retry=transient and attempt+1<attempts and deadline-time.monotonic()>delay
            model_runner._log(log,'model_error',error=str(e),attempt=attempt,retry=retry,retry_delay_s=delay if retry else None,elapsed_s=time.monotonic()-started,**detail)
            if not retry:raise
            time.sleep(min(delay,max(0,deadline-time.monotonic())))


def _display(value):
    """Summarize with out-of-band omission counts, never fake object entries."""
    if isinstance(value,dict):
        result={};omitted={}
        for k,v in value.items():
            if (k=='objects' and value.get('collection')=='schematic_objects') or k in ('public_requirements','test_results') or (k=='findings' and value.get('check_view')=='findings') or (k=='items' and value.get('collection') in ('copper','pads','parts','drawings')):
                result[k]=v
            elif k=='report' and isinstance(v,dict):
                result[k]=_display({rk:rv for rk,rv in v.items() if rk in ('counts','violations','report_path','report_valid','report_errors','returncode','raw_stderr')})
            elif k in ('data','sources','artifacts'):result[k]={'stored_in_full_observation':True}
            else:
                result[k]=_display(v)
                if isinstance(v,list) and len(v)>15:omitted[k]=len(v)-15
        if omitted:result['omitted_fields']=omitted
        return result
    if isinstance(value,list):return [_display(v) for v in value[:15]]
    return value


def operation_outcome(observation):
    """Separate subprocess exit, persisted changes and the engineering verdict.

    An abnormal exit after a write is never safe to blindly replay. This
    reports facts from controller hashes, not an inferred successful commit.
    """
    before=observation.get('before',{});after=observation.get('after',{})
    changed=sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k))
    rc=observation.get('returncode')
    try:result=json.loads(observation.get('stdout',''))
    except (ValueError,TypeError):result={}
    abnormal=rc!=0
    observed='before' in observation and 'after' in observation
    rejected=isinstance(result,dict) and result.get('operation_executed') is False
    uncertain=not observed and not rejected
    return dict(process_status='ERROR' if abnormal else 'SUCCESS',
        mutation_status='UNKNOWN_REVIEW_REQUIRED' if uncertain else 'CHANGED_REVIEW_REQUIRED' if abnormal and changed else 'CHANGED' if changed else 'UNCHANGED',
        changed_files=changed,board_sha256=after.get('board.kicad_pcb'),
        reported_status=result.get('status') if isinstance(result,dict) else None,
        next_action='Do not replay the edit. Inspect current CAD/UUIDs and rerun the affected native check before another mutation.' if uncertain or (abnormal and changed) else None)


def compact_observation(observation, limit_bytes=18000):
    """Keep actionable verdicts when large solver evidence exceeds context limits.

    Full stdout stays in the controller receipt. The sandbox can read returned
    tool stdout/stderr and candidate reports; other controller records stay private.
    """
    from tools import diagnostics
    shown=dict(observation)
    shown['operation_outcome']=operation_outcome(observation)
    before=shown.pop('before',{});after=shown.pop('after',{})
    shown['changed_files']=[k for k in sorted(set(before)|set(after)) if before.get(k)!=after.get(k)]
    stdout=shown.pop('stdout','')
    try:
        parsed=json.loads(stdout);shown['result']=_display(parsed)
        diagnostics.refresh_omissions(parsed,shown['result'])
    except ValueError:parsed=None;shown.update(stdout=stdout[-12000:],stdout_truncated=len(stdout)>12000)
    if shown.get('stdout_path'):
        shown['stdout_format']='raw_tool_result_json' if 'result' in shown else 'text'
        if isinstance(parsed,dict):shown['stdout_root_keys']=list(parsed)[:24]
        shown['stdout_read_hint']='stdout_path contains the raw tool result at the JSON root, without this observation envelope or its outer result key.' if 'result' in shown else 'stdout_path contains the original text, not a JSON observation envelope.'
    stderr=shown.get('stderr','').splitlines()
    filtered=[line for line in stderr if not re.fullmatch(r"\d{2}:\d{2}:\d{2}: Debug: Adding duplicate image handler for '[^']+'",line)]
    shown['stderr']='\n'.join(filtered)[-6000:]
    if len(filtered)!=len(stderr):shown['suppressed_debug_lines']=len(stderr)-len(filtered)
    if len(json.dumps(shown,ensure_ascii=False).encode())<=limit_bytes:return shown
    if isinstance(parsed,dict) and parsed.get('collection') == 'schematic_objects':
        from tools.schematic_observation import update_page
        result=dict(parsed,objects=list(parsed['objects']))
        shown['result']=result;shown['truncated']=True
        while len(result['objects'])>1 and len(json.dumps(shown,ensure_ascii=False).encode())>limit_bytes:
            result['objects'].pop();result['page_complete']=False;update_page(result)
        if len(json.dumps(shown,ensure_ascii=False).encode())>limit_bytes:
            shown['byte_limit_exceeded']=True
            shown['instruction']='One native object exceeds the byte budget; retained complete. Follow next_call for remaining objects.'
        return shown
    if isinstance(parsed,dict) and parsed.get('collection') in ('copper','pads','parts','drawings') and isinstance(parsed.get('items'),list):
        # Retain complete typed objects. Shrink the page, then recompute the
        # cursor from the actual delivered count so no IDs disappear silently.
        from tools.board_observation import update_page
        result=dict(parsed,items=list(parsed['items']))
        shown['result']=result;shown['truncated']=True
        while len(result['items'])>1 and len(json.dumps(shown,ensure_ascii=False).encode())>limit_bytes:
            result['items'].pop();result['page_complete']=False;update_page(result)
        if len(json.dumps(shown,ensure_ascii=False).encode())>limit_bytes:
            shown['byte_limit_exceeded']=True
            shown['instruction']='One native object exceeds the observation budget; this complete object is retained rather than silently shortened.'
        return shown
    if isinstance(parsed,dict) and parsed.get('collection_queries'):
        # A broad native overview should retain part identities and useful
        # query links even when a large pin inventory exceeds the byte budget.
        shown['result']={k:parsed[k] for k in ('status','scope','next_action','verification_role','board_sha256','coordinate_units','layers','inspection_hint','measurement_scope','collection_queries') if k in parsed}
        shown['result']['parts']=[{k:p[k] for k in ('id','ref','value','at','rotation','side','pad_count','children_query') if k in p} for p in parsed.get('parts',[])[:15]]
        shown['result']['total_parts']=len(parsed.get('parts',[]))
        shown['result']['overview_only']=True;shown['truncated']=True
        while shown['result']['parts'] and len(json.dumps(shown,ensure_ascii=False).encode())>limit_bytes:
            shown['result']['parts'].pop()
        shown['result']['omitted_fields']={'part_details':'Use collection_queries or each children_query for complete geometry and pads.',
                                           'parts':len(parsed.get('parts',[]))-len(shown['result']['parts'])}
        return shown
    if (isinstance(parsed,dict) and parsed.get('check_stage') and parsed.get('check_view') in ('tests','findings')
            and isinstance(parsed.get('findings' if parsed['check_view']=='findings' else 'test_results'),list)):
        key='findings' if parsed['check_view']=='findings' else 'test_results'
        result=dict(parsed)
        result[key]=list(parsed[key]);shown['result']=result;shown['truncated']=True
        while len(result[key])>1 and len(json.dumps(shown,ensure_ascii=False).encode())>limit_bytes:
            result[key].pop()
        delivered=len(result[key]);offset=parsed['offset'];total=parsed['total_items']
        if delivered<len(parsed[key]):
            result.update(page_complete=False,section_complete=False,has_more=offset+delivered<total,
                          next_offset=offset+delivered)
            query={'check_stage':parsed['check_stage'],'check_view':parsed['check_view'],
                   'offset':offset+delivered,'limit':min(10,max(1,delivered))}
            if parsed.get('item_id'):query['item_id']=parsed['item_id']
            result['next_call']={'tool':'project_status','arguments':query}
        if len(json.dumps(shown,ensure_ascii=False).encode())>limit_bytes:
            shown['byte_limit_exceeded']=True
            shown['instruction']='A single complete check item exceeds the observation budget; follow next_call or inspect report_path.'
        return shown
    nodes=[200]
    def bounded(value, depth=0):
        nodes[0]-=1
        if isinstance(value,str):return value[:1500]
        if depth>8 or nodes[0]<0:return '<omitted: read full report>'
        if isinstance(value,list):return [bounded(x,depth+1) for x in value[:6]]
        if isinstance(value,dict):return {k[:160]:bounded(v,depth+1) for k,v in list(value.items())[:16]}
        return value
    keys=('status','cad_status','electrical_status','scoped_status','simulation_status',
          'full_engineering_status','scope','saved','blocked','error_type','message',
          'input_error','operation_executed','tool_name','schema_hint',
          'issues','status_basis','electrical_qualification_gaps','next_action','report_path','attempt_path','feedback',
          'task','public_requirements','repair_context','checks','analysis_readiness','functional_coverage','inspection_hint',
          'tests_executed','applicability_basis','circuit_function_success',
          'measurement_scope','connectivity_status','simulation_source','pcb_copper_extracted',
          'check_kind','does_not_establish','board_sha256','object','omitted_fields','check_stage','fresh','total_items','offset','has_more','page_complete','section_complete','next_offset','next_call')
    # Reuse the display filter: starting again from raw evidence reintroduced
    # huge native netlists and consumed the node budget before blocker causes.
    displayed=shown.get('result',{})
    check_index=diagnostics.check_summary(parsed,parsed.get('report_path') or observation.get('stdout_path')) if isinstance(parsed,dict) else None
    has_checks=check_index is not None and check_index['total_checks']>1
    result={k:bounded(displayed[k]) for k in keys if k in displayed and not (has_checks and k=='checks')} if isinstance(displayed,dict) else {}
    if has_checks:
        result['check_summary']=check_index
    if isinstance(parsed,dict) and parsed.get('test_results'):
        result['test_results']=parsed['test_results'][:10]
        if len(parsed['test_results'])>10:
            result['test_results_omitted']=len(parsed['test_results'])-10
            result['test_results_next_action']=f"Use project_status(check_stage=<stage in checks>, offset={parsed.get('offset',0)+10}, limit=10) for remaining test receipts."
    if isinstance(parsed,dict) and isinstance(parsed.get('evidence'),dict):
        result['observed_statuses']={k:bounded(parsed['evidence'][k]) for k in
            ('cad_status','electrical_status','task_status','scope','narrative_status') if k in parsed['evidence']}
    if isinstance(parsed,dict) and isinstance(parsed.get('tool_contract'),dict):
        result['tool_name']=parsed['tool_contract'].get('name')
    compact={'returncode':observation.get('returncode'),'result':result,'truncated':True,
            'operation_outcome':shown['operation_outcome'],
            'recovery':observation.get('recovery'),'trace_context':observation.get('trace_context'),
            'changed_files':shown['changed_files'][:20],
            'stdout_path':observation.get('stdout_path'),'stderr_path':observation.get('stderr_path'),
            'stdout_format':shown.get('stdout_format'),'stdout_root_keys':shown.get('stdout_root_keys'),
            'stdout_read_hint':shown.get('stdout_read_hint'),
            'stderr':shown['stderr'][-1500:],
            'instruction':'Use candidate report_path/attempt_path or the returned action stdout/stderr files for full tool feedback. Other controller records remain private. For argument errors inspect_tool gives the schema; keep JSON arrays/objects as native values, not encoded strings. Truncation never implies PASS.'}
    # A breadth/depth cap alone can still grow exponentially. Reduce the
    # largest compound field first, keeping status/path keys and JSON validity.
    def recount():
        diagnostics.refresh_omissions(parsed,compact['result'])
        summary=compact['result'].get('check_summary')
        if summary:
            summary['returned_checks']=len(summary['checks'])
            summary['omitted_checks']=summary['total_checks']-summary['returned_checks']
    recount()
    while len(json.dumps(compact,ensure_ascii=False).encode())>limit_bytes:
        candidates=[]
        def visit(value,path):
            if isinstance(value,dict):
                for key,child in value.items():
                    if key not in ('path','full_report_path','report_path','stdout_path','stderr_path','next_call'):
                        visit(child,path+[key])
            elif isinstance(value,list) and len(value)>1:candidates.append((len(json.dumps(value).encode()),path,'list'))
            elif isinstance(value,list):
                for index,child in enumerate(value):visit(child,path+[index])
            elif isinstance(value,str) and len(value)>240:candidates.append((len(value.encode()),path,'text'))
        visit(compact,[])
        if not candidates:break
        _,path,kind=max(candidates,key=lambda x:x[0]);parent=compact
        for key in path[:-1]:parent=parent[key]
        value=parent[path[-1]]
        parent[path[-1]]=value[:max(1,len(value)//2)] if kind=='list' else value[:max(240,len(value)//2)]
        recount()
    # The byte cap can shorten even a pre-paged receipt array. Advance only by
    # entries actually delivered, never the original ten-entry slice; otherwise
    # the next call silently skips tests while claiming page completeness.
    if isinstance(parsed,dict) and isinstance(parsed.get('test_results'),list):
        returned=len(compact['result'].get('test_results',[]))
        total=parsed.get('total_items',len(parsed['test_results']))
        offset=parsed.get('offset',0)
        if returned<len(parsed['test_results']):
            result=compact['result']
            result.update(page_complete=False,section_complete=False,has_more=offset+returned<total,
                          test_results_omitted=len(parsed['test_results'])-returned)
            if parsed.get('check_stage'):
                result.update(next_offset=offset+returned,
                    next_call={'tool':'project_status','arguments':{'check_stage':parsed['check_stage'],
                              'offset':offset+returned,'limit':min(10,max(1,returned))}})
            result['test_results_next_action']='The observation byte limit shortened this page; follow next_call or read report_path for complete measurements.'
    return compact


def context_messages(messages,limit_bytes=180000):
    """Retain fixed instructions and recent complete tool exchanges.

    Raw requests, reasoning and receipts remain in the controller trajectory.
    Never cut a call away from its tool results or shorten the original brief.
    """
    prefix=messages[:2];groups=[]
    for item in messages[2:]:
        if item.get('role')=='assistant' or not groups:groups.append([])
        groups[-1].append(item)
    def size(items):return len(json.dumps(items,ensure_ascii=False).encode())
    budget=limit_bytes-size(prefix)-1000;kept=[]
    for group in reversed(groups):
        if kept and size(group)>budget:break
        kept.insert(0,group);budget-=size(group)
    omitted=len(groups)-len(kept)
    if omitted:
        prefix=prefix+[{'role':'user','content':f'Context window: {omitted} older exchanges omitted. Candidate files and reports remain authoritative. Inspect current state when needed; no omitted decision is assumed correct.'}]
    return prefix+[item for group in kept for item in group],omitted


def run(task,workspace,model,endpoint,max_turns=36,max_seconds=3600,max_tokens=8192,resume=False,request_timeout=900,api_attempts=4,tool_timeout=900,python_timeout=120,record_frames=False,request_options=None,tool_identity=None,vision=False):
    if min(request_timeout,api_attempts,tool_timeout,python_timeout,max_turns,max_seconds,max_tokens)<=0:raise ValueError('Positive runtime budgets required')
    request_options={} if request_options is None else request_options
    reserved={'model','messages','tools','stream','max_tokens','api_key','authorization','headers'}
    if not isinstance(request_options,dict) or reserved&{str(k).lower() for k in request_options}:
        raise ValueError('Provider options must be an object without protocol or credential overrides')
    request_options=json.loads(json.dumps(request_options,allow_nan=False))
    root=Path(workspace).resolve();root.mkdir(parents=True,exist_ok=True);os.chdir(root)
    # The controller creates Python scratch directories as root.  Seed the
    # shared project state before permission isolation so named tool processes
    # retain ownership of .pcb even when run_python is the first action.
    pcb_state=root/'.pcb'
    if pcb_state.is_symlink():raise ValueError('PCB state directory cannot be a symlink')
    pcb_state.mkdir(exist_ok=True)
    bound_requirements=task_contract.binding()
    isolation={'recording':'SAME_UID'}
    if tool_identity is not None:
        from tools.session_isolation import prepare
        isolation=prepare(root,tool_identity,resume)
    logdir=root/'session';checkpoint=logdir/'checkpoint.json'
    start=time.monotonic()
    skillroot=Path(os.environ.get('PCB_SKILLS','/skills'))/'pcb-design-e2e'
    # Source checkouts do not have the image's ``/skills`` mount.  Keep the
    # runtime default unchanged, but resolve the repository copy when a local
    # test or desktop invocation runs outside the container.
    if not (skillroot/'SKILL.md').is_file():
        checkout_skill=Path(__file__).resolve().parents[2]/'skills'/'pcb-design-e2e'
        if (checkout_skill/'SKILL.md').is_file():skillroot=checkout_skill
    if resume:
        cp=json.loads(checkpoint.read_text())
        if cp['model']!=model or cp['task']!=task:raise ValueError('Resume must preserve model and original task')
        if cp.get('toolset_sha256')!=TOOLSET_SHA256:raise ValueError('Tool interface changed; resume with the original frozen environment or start a new session')
        if cp.get('request_options',{})!=request_options:raise ValueError('Resume must preserve provider request options')
        if cp.get('vision',False)!=vision:raise ValueError('Resume must preserve visual observation mode')
        if cp.get('tool_identity')!=tool_identity:raise ValueError('Resume must preserve tool process identity')
        if cp.get('requirements_binding',{})!=bound_requirements:raise ValueError('Resume must preserve immutable task requirements')
        messages=cp['messages'];usage=cp['usage'];actions=cp['actions'];turn_start=cp['turn'];elapsed=cp['elapsed_s'];pending=cp.get('pending')
        if cp.get('finished'):raise ValueError('Session already finished; start a new workspace')
    else:
        logdir.mkdir(exist_ok=False)
        system=('You are a PCB engineering agent in an isolated Linux KiCad container. spec.json is your editable design intent. Immutable public requirements are available from project_status and /task/public_requirements.json when this is a bound task; verify_schematic, run_analysis and verify_design enforce compiled requirements. For diagnostic briefs, complete textual coverage stays UNKNOWN; candidate-authored analyses are diagnostics. set_requirements changes your intent and additional diagnostics, never the task acceptance target. Calls execute in listed order with individual receipts. Never invent evidence. Use list_references/read_reference for skill documents. run_python is a sandboxed calculator: candidate files are read-only, write derived data only in PCB_EXTENSION_SCRATCH, no evaluator source, network or subprocess access. Edit CAD through named tools. Submit finalize_claims only when required by the fixed task, in its authorized scope; otherwise finish after the requested checks and factual report.\n\n'+(skillroot/'SKILL.md').read_text())
        messages=[{'role':'system','content':system},{'role':'user','content':task}];usage=[];actions=0;turn_start=0;elapsed=0;pending=None
        (logdir/'task.txt').write_text(task)
    log=logdir/'model-trajectory.jsonl';deadline=start+max(0,max_seconds-elapsed)
    if record_frames and not resume:
        from tools import visualize
        visualize.capture(root,0,'session_start',{})
    def save_checkpoint(turn,pending=None,finished=False):
        ws._write(checkpoint,{'model':model,'task':task,'messages':messages,'usage':usage,'actions':actions,'turn':turn,
                            'elapsed_s':elapsed+time.monotonic()-start,'pending':pending,'finished':finished,'toolset_sha256':TOOLSET_SHA256,'request_options':request_options,'tool_identity':tool_identity,'requirements_binding':bound_requirements,'vision':vision,
                            'runtime':{'request_timeout_s':request_timeout,'api_attempts':api_attempts,'tool_timeout_s':tool_timeout,'python_timeout_s':python_timeout,'max_seconds':max_seconds,'max_turns':max_turns,'record_frames':record_frames}})
    save_checkpoint(turn_start,pending)
    stagnant=0;previous=snapshot(root);reason='TURN_BUDGET';final='';turn=turn_start
    def execute_call(call,index):
        nonlocal actions
        fn=call.get('function') or {};name=fn.get('name');raw=fn.get('arguments','{}')
        args=decode_arguments(raw)
        validate_call(name,args)
        ident=hashlib.sha256(json.dumps([turn,index,call],sort_keys=True).encode()).hexdigest()
        receipt=logdir/('receipt-'+ident+'.json')
        if receipt.exists():
            stored=json.loads(receipt.read_text())
            if stored['state']!='completed':raise RuntimeError('AMBIGUOUS_TOOL_OUTCOME: inspect interrupted action; automatic replay disabled')
            actions=max(actions,stored.get('action',actions))
            return stored['observation']
        actions+=1;prefix=logdir/f'action-{actions:03d}';before=snapshot(root)
        ws._write(receipt,{'state':'started','tool':name,'arguments':args,'action':actions})
        if name=='run_python':
            validate_arguments(args,PYTHON_TOOL['function']['parameters'])
            script=prefix.with_suffix('.py');script.write_text(args['code'])
            scratch=root/'.pcb/extensions'/ident;scratch.mkdir(parents=True,exist_ok=False)
            if tool_identity is not None:
                os.chown(scratch,tool_identity['uid'],tool_identity['gid'])
            command=[sys.executable,'-I',str(Path(__file__).with_name('python_extension.py')),
                     '--code',str(script),'--workspace',str(root),'--scratch',str(scratch)]
            if bound_requirements:command.extend(['--requirements',bound_requirements['requirements_path']])
            stdin=None
        else:
            command=[sys.executable,'-m','tools.workspace'];stdin=json.dumps({'name':name,'arguments':args})
            prefix.with_suffix('.json').write_text(stdin)
        env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('KEY','TOKEN','SECRET','PASSWORD','MODEL_ENDPOINT'))}
        if name=='run_python':
            env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
        per_tool_timeout=min(python_timeout if name=='run_python' else tool_timeout,tool_timeout)
        effective_timeout=min(per_tool_timeout,max(.001,deadline-time.monotonic()))
        action_started=time.monotonic()
        credentials={} if tool_identity is None else dict(user=tool_identity['uid'],group=tool_identity['gid'],extra_groups=[])
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 cwd=root,env=env,text=True,start_new_session=True,**credentials)
        try:
            stdout,stderr=process.communicate(input=stdin,timeout=effective_timeout)
            rc=process.returncode
        except subprocess.TimeoutExpired:
            # Also stop spawned searches/solvers so they cannot outlive the receipt.
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            stdout,stderr=process.communicate()
            stderr+='\nTool timeout: inspect persisted artifacts before retrying. Use read_reference for skill documentation.'
            rc=124
        prefix.with_suffix('.stdout').write_text(stdout);prefix.with_suffix('.stderr').write_text(stderr)
        observation={'returncode':rc,'stdout':stdout,'stderr':stderr,'before':before,'after':snapshot(root),
                     'stdout_path':str(prefix.with_suffix('.stdout')),'stderr_path':str(prefix.with_suffix('.stderr')),'elapsed_s':time.monotonic()-action_started,'timeout_s':effective_timeout}
        observation['operation_outcome']=operation_outcome(observation)
        if name=='run_python':
            observation['extension_policy']='kernel_sandbox_readonly_candidate'
            observation['scratch_path']=str(scratch)
        if name in ('verify_design','finalize_claims'):
            try:
                captured=first_verification_snapshot(root,actions,name)
                if captured:model_runner._log(log,'first_verification_snapshot',**captured)
            except (OSError,ValueError) as error:
                model_runner._log(log,'checkpoint_error',phase='first_verification',error=str(error))
        ws._write(receipt,{'state':'completed','action':actions,'observation':observation})
        model_runner._log(log,'tool_action',tool=name,arguments=args,observation=observation,tool_call_id=call.get('id'),turn=turn)
        if record_frames:
            from tools import visualize
            capture_started=time.monotonic()
            try:
                visualize.capture(root,actions,name,args,observation)
                model_runner._log(log,'visual_snapshot',action=actions,elapsed_s=time.monotonic()-capture_started)
            except Exception as error:model_runner._log(log,'checkpoint_error',phase='visual_snapshot',error=str(error))
        return observation
    while turn<max_turns:
        if time.monotonic()>=deadline:reason='TIME_BUDGET';break
        if pending is None:
            status=ws.project_status()
            feedback={'turn':turn+1,'turns_remaining':max_turns-turn,'seconds_remaining':round(deadline-time.monotonic()),'files':list(snapshot(root)),
                      'consecutive_turns_without_artifact_change':stagnant,'checks':status.get('checks',{})}
            feedback['repair_context']=status.get('repair_context',[])
            if stagnant>=3:feedback['guidance']='No design artifact changes in several turns. If verification failed, inspect its current findings and affected objects, test a local correction and rerun the affected check. Keep unrelated layout and copper. If evidence is missing, identify that dependency; recreating the board will not supply it.'
            model_runner._log(log,'progress',**feedback)
            feedback['checks']=_display(feedback['checks'])
            context,omitted=context_messages(messages)
            if omitted:model_runner._log(log,'context_window',omitted_exchanges=omitted,retained_messages=len(context))
            payload={'model':model,'messages':context+[{'role':'user','content':'Runtime progress (not engineering acceptance): '+json.dumps(feedback)}],
                     'tools':TOOLS,'stream':False,'max_tokens':max_tokens,**request_options}
            try:raw=request_with_retry(payload,endpoint,log,deadline,attempts=api_attempts,request_timeout=request_timeout)
            except Exception as e:reason='TIME_BUDGET' if time.monotonic()>=deadline else 'API_ERROR';break
            usage.append(raw.get('usage',{}));choice=(raw.get('choices') or [{}])[0];message=choice.get('message') or {}
            if message.get('role')!='assistant':reason='INVALID_RESPONSE';break
            messages.append(message);calls=message.get('tool_calls') or []
            if not calls:
                final=message.get('content') or '';reason='MODEL_FINISHED' if choice.get('finish_reason')!='length' else 'OUTPUT_TRUNCATED';break
            pending={'calls':calls,'next_index':0};save_checkpoint(turn,pending)
        for index in range(pending['next_index'],len(pending['calls'])):
            if time.monotonic()>=deadline:reason='TIME_BUDGET';break
            call=pending['calls'][index]
            try:observation=execute_call(call,index)
            except RuntimeError as e:
                if str(e).startswith('AMBIGUOUS_TOOL_OUTCOME'):reason='AMBIGUOUS_TOOL_OUTCOME';break
                observation={'returncode':1,'stderr':str(e),'stdout':json.dumps(error_observation(e,(call.get('function') or {}).get('name')))}
                model_runner._log(log,'tool_action',tool=(call.get('function') or {}).get('name'),arguments=(call.get('function') or {}).get('arguments'),observation=observation,tool_call_id=call.get('id'),turn=turn,error_phase='dispatch')
            except Exception as e:
                observation={'returncode':1,'stderr':f'{type(e).__name__}: {e}','stdout':json.dumps(error_observation(e,(call.get('function') or {}).get('name')))}
                model_runner._log(log,'tool_action',tool=(call.get('function') or {}).get('name'),arguments=(call.get('function') or {}).get('arguments'),observation=observation,tool_call_id=call.get('id'),turn=turn,error_phase='dispatch')
            shown=compact_observation(observation)
            messages.append({'role':'tool','tool_call_id':call.get('id'),'content':json.dumps(shown,ensure_ascii=False)})
            pending['next_index']=index+1;save_checkpoint(turn,pending)
        if reason in ('AMBIGUOUS_TOOL_OUTCOME','TIME_BUDGET'):break
        if vision and any(c.get('function',{}).get('name') in ('generate_schematic','inspect_board','verify_design','export_3d') for c in pending['calls']):
            try:
                from tools.model_vision import observe
                content,records=observe(root)
                if content:messages.append({'role':'user','content':content})
                model_runner._log(log,'model_visual_observation',images=records)
            except Exception as error:
                model_runner._log(log,'visual_observation_error',error=type(error).__name__+': '+str(error))
        now=snapshot(root);stagnant=stagnant+1 if now==previous else 0;previous=now
        pending=None;turn+=1;save_checkpoint(turn)
        print(json.dumps({'model':model,'turn':turn,'actions':actions,'elapsed_s':round(elapsed+time.monotonic()-start,1)}),flush=True)
    report={'model':model,'reason':reason,'actions':actions,'elapsed_s':elapsed+time.monotonic()-start,'usage':usage,'final':final,
            'toolset_sha256':TOOLSET_SHA256,'request_options':request_options,'isolation':isolation,
            'artifacts':snapshot(root),'status':'UNASSESSED','note':'Termination is not acceptance; independent evaluator must inspect original requirements.'}
    ws._write(logdir/'result.json',report);(logdir/'final.md').write_text(final)
    save_checkpoint(turn,pending,finished=reason in ('MODEL_FINISHED','OUTPUT_TRUNCATED'))
    model_runner._log(log,'session_stop',reason=reason,actions=actions)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--workspace',required=True);p.add_argument('--model',required=True)
    p.add_argument('--endpoint',default=os.environ.get('MODEL_ENDPOINT'));p.add_argument('--max-turns',type=int,default=36)
    p.add_argument('--max-seconds',type=int,default=3600);p.add_argument('--max-tokens',type=int,default=8192);p.add_argument('--resume',action='store_true')
    p.add_argument('--request-timeout',type=float,default=900);p.add_argument('--api-attempts',type=int,default=4);p.add_argument('--tool-timeout',type=float,default=900);p.add_argument('--python-timeout',type=float,default=120)
    p.add_argument('--record-frames',action='store_true',help='Capture CAD after every action for the separate live/replay viewer')
    p.add_argument('--vision',action='store_true',help='Send native CAD PNG observations to a vision-capable model')
    p.add_argument('--request-options',type=json.loads,default={},help='Provider request fields as JSON, e.g. enable_thinking; frozen across resume')
    from tools.session_isolation import parse_identity
    p.add_argument('--tool-user',type=parse_identity,help='Linux container tool UID:GID; controller remains root and protects session records')
    a=p.parse_args()
    if not a.endpoint and not os.environ.get('MODEL_COMMAND'):p.error('--endpoint, MODEL_ENDPOINT or MODEL_COMMAND required')
    r=run(Path(a.task).read_text(),a.workspace,a.model,a.endpoint,a.max_turns,a.max_seconds,a.max_tokens,a.resume,a.request_timeout,a.api_attempts,a.tool_timeout,a.python_timeout,a.record_frames,a.request_options,a.tool_user,a.vision)
    print(json.dumps({k:v for k,v in r.items() if k not in ('artifacts','usage','final')},indent=2))
    raise SystemExit(0 if r['reason']=='MODEL_FINISHED' else 2)
