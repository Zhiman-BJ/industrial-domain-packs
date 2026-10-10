"""Provider-neutral model action bridge with append-only trajectory capture.

The model may be an OpenAI-compatible HTTP endpoint or a local command. The
bridge never assumes a provider, stores API keys, or fabricates rewards. Each
request, response, tool action and resulting observation is JSONL logged beside
the PCB loop trajectory.
"""
from pathlib import Path
import datetime
import json
import os
import subprocess
import urllib.request
import urllib.error


def _log(path, event, **data):
    row={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event':event, **data}
    with Path(path).open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
    return row


def request(messages, tools=None, model=None, endpoint=None, log_path=None, timeout=600):
    """Call an OpenAI-compatible `/chat/completions` endpoint and log raw evidence."""
    endpoint=endpoint or os.environ.get('MODEL_ENDPOINT')
    model=model or os.environ.get('MODEL_NAME','')
    log_path=log_path or 'model-trajectory.jsonl'
    if not endpoint or not model:return {'status':'UNKNOWN','issues':['MODEL_ENDPOINT and MODEL_NAME are required']}
    payload={'model':model,'messages':messages,'stream':False}
    if tools is not None:payload['tools']=tools
    body=json.dumps(payload).encode()
    req=urllib.request.Request(endpoint,body,headers={'Content-Type':'application/json'})
    token=os.environ.get('MODEL_API_KEY')
    if token:req.add_header('Authorization','Bearer '+token)
    _log(log_path,'model_request',model=model,endpoint=endpoint,payload=payload)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:data=json.loads(response.read())
        _log(log_path,'model_response',response=data)
        return {'status':'PASS','response':data}
    except (OSError,urllib.error.URLError,TimeoutError,json.JSONDecodeError) as e:
        _log(log_path,'model_error',error=str(e));return {'status':'UNKNOWN','issues':[str(e)]}


def run_command(command, input_text, log_path, timeout=600):
    """Run a local model adapter without shell interpolation and log its action."""
    if not command:return {'status':'UNKNOWN','issues':['MODEL_COMMAND is required']}
    _log(log_path,'model_request',command=command,input=input_text)
    try:
        p=subprocess.run(command,input=input_text,text=True,capture_output=True,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired) as e:
        _log(log_path,'model_error',error=str(e));return {'status':'UNKNOWN','issues':[str(e)]}
    _log(log_path,'model_response',returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
    return {'status':'PASS' if p.returncode==0 else 'FAIL','stdout':p.stdout,'stderr':p.stderr,'returncode':p.returncode}


def record_tool(log_path, tool, arguments, observation):
    """Record a model-selected tool call and the exact returned observation."""
    return _log(log_path,'tool_action',tool=tool,arguments=arguments,observation=observation)


def execute_tool_calls(response, registry, log_path):
    """Execute function tool calls from a compatible response through an allowlist.

    `registry` maps public names such as ``design.add_net`` to Python callables.
    Arguments must be a JSON object. Every call and exact return value is
    recorded; unknown tools and malformed arguments are returned as failures.
    """
    calls=(((response or {}).get('choices') or [{}])[0].get('message') or {}).get('tool_calls') or []
    results=[]
    for call in calls:
        fn=call.get('function') or {};name=fn.get('name');raw=fn.get('arguments','{}')
        args=raw
        try:
            args=json.loads(raw) if isinstance(raw,str) else raw
            if not isinstance(args,dict):raise ValueError('tool arguments must be a JSON object')
            if name not in registry:raise LookupError('tool is not in the explicit allowlist: '+str(name))
            observation=registry[name](**args)
            record_tool(log_path,name,args,observation)
            results.append({'tool_call_id':call.get('id'),'tool':name,'status':'PASS','observation':observation})
        except Exception as e:
            observation={'status':'UNKNOWN','issues':[f'{type(e).__name__}: {e}']}
            record_tool(log_path,name,args if 'args' in locals() else raw,observation)
            results.append({'tool_call_id':call.get('id'),'tool':name,'status':'UNKNOWN','observation':observation})
    return results
