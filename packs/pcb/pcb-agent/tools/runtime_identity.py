"""Controller-owned runtime provenance; never inferred from mutable image tags."""
import hashlib
import json
import os
from pathlib import Path


def manifest():
    from tools import agent_session
    from tools.runtime_limits import limits
    def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    root=Path(__file__).parent
    skill=Path(os.environ.get('PCB_SKILLS','/skills'))/'pcb-design-e2e'
    return dict(image_digest=os.environ.get('PCB_RUNTIME_IMAGE_DIGEST'),
        tool_budgets=limits(),
        toolset_sha256=agent_session.TOOLSET_SHA256,
        tools={p.name:digest(p) for p in sorted(root.glob('*.py'))},
        skill={str(p.relative_to(skill)):digest(p) for p in sorted(skill.rglob('*')) if p.is_file()},
        backend_config_sha256=digest(Path('/opt/pcb-analysis-backends.json')) if Path('/opt/pcb-analysis-backends.json').is_file() else None)


def record(logdir,system):
    from tools import workspace
    result=manifest();result['system_prompt_sha256']=hashlib.sha256(system.encode()).hexdigest()
    path=Path(logdir)/'runtime-identity.json'
    if path.is_file() and json.loads(path.read_text())!=result:raise ValueError('Runtime identity changed; use the frozen environment to resume')
    if not path.exists():workspace._write(path,result)
    return result
