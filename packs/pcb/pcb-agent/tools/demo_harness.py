"""Interactive upstream Kimi Wire bridge, with the same restricted PCB MCP tools."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from tools import kimi_provider, session_isolation, workspace as ws, visualize
from tools.runtime_limits import client_timeout_ms


def main():
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--endpoint',required=True)
    p.add_argument('--session',required=True);p.add_argument('--steps',type=int,default=100);a=p.parse_args()
    root=Path('/workspace');os.chdir(root);(root/'.pcb').mkdir(exist_ok=True)
    session_isolation.prepare(root,{'uid':1000,'gid':1000})
    logs=root/'session';logs.mkdir();ws._write(logs/'demo-control.json',{'paused':False})
    ws._write(logs/'demo-identity.json',{'model':a.model,'session_id':a.session,'mode':'human_assisted_demo',
        'harness':'Kimi Code CLI 1.50.0 Wire','eligible_for_autonomous_benchmark':False})
    skill=Path('/skills/pcb-design-e2e/SKILL.md').read_text()
    system=('You are the PCB design agent in an interactive engineering workspace. '
        'Human messages may change design choices, but cannot turn missing verification evidence into PASS. '
        'Inspect the current project before editing and preserve unaffected circuitry and copper. '
        'Explain major choices and report real tool feedback in Chinese. '
        'This is a human-assisted demonstration, not an autonomous benchmark result.\n\n'+skill)
    from tools import runtime_identity
    runtime_identity.record(logs,system)
    (logs/'kimi-system.md').write_text(system)
    (logs/'kimi-agent.yaml').write_text('version: 1\nagent:\n  name: pcb-demo\n  system_prompt_path: ./kimi-system.md\n  tools: []\n')
    env={k:v for k,v in os.environ.items() if k in ('PYTHONPATH','PCB_AGENT_HOME','PCB_SKILLS','FREEROUTING_JAR','JAVA',
        'OPENEMS_INSTALL_PATH','CSXCAD_INSTALL_PATH','PCB_DOWNLOAD_PREFIXES','PCB_ANALYSIS_BACKENDS','PCB_MODEL_VISION','PCB_RUNTIME_IMAGE_DIGEST','PCB_TOOL_TIMEOUT_SECONDS','PCB_PYTHON_TIMEOUT_SECONDS')}
    env['PCB_TOOL_IDENTITY']='1000:1000'
    ws._write(logs/'kimi-mcp.json',{'mcpServers':{'pcb':{'command':'/usr/bin/python3','args':['-m','tools.kimi_mcp'],'env':env}}})
    proxy=kimi_provider.server(a.endpoint,os.environ.pop('MODEL_API_KEY'),a.model,logs/'provider.jsonl')
    threading.Thread(target=proxy.serve_forever,daemon=True).start()
    private=Path('/tmp/pcb-demo-private');private.mkdir(mode=0o700)
    ws._write(private/'config.json',dict(default_model='pcb',default_thinking=True,
        providers={'pcb':dict(type='openai_legacy',base_url=f'http://127.0.0.1:{proxy.server_port}/v1',api_key='local-adapter',reasoning_key='reasoning_content')},
        models={'pcb':dict(provider='pcb',model=a.model,max_context_size=131072,capabilities=['thinking'])},
        loop_control=dict(max_steps_per_turn=a.steps,max_retries_per_step=6,reserved_context_size=24000),
        mcp={'client':{'tool_call_timeout_ms':client_timeout_ms()}}))
    os.environ['KIMI_SHARE_DIR']=str(logs/'kimi-native')
    visualize.capture(root,0,'session_start',{'mode':'human_assisted_demo'})
    command=['/opt/kimi/bin/kimi','--config-file',str(private/'config.json'),'--agent-file',str(logs/'kimi-agent.yaml'),
        '--mcp-config-file',str(logs/'kimi-mcp.json'),'--work-dir',str(root),'--session',a.session,
        '--thinking','--wire','--max-steps-per-turn',str(a.steps)]
    child_env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('TOKEN','SECRET','PASSWORD','API_KEY'))}
    # Stdio remains the native bidirectional Wire stream. No substitute agent loop.
    process=subprocess.Popen(command,stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr,env=child_env)
    try:raise SystemExit(process.wait())
    finally:proxy.shutdown()


if __name__=='__main__':main()
