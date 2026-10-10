"""Separate model edits from controller-owned trajectories in Linux containers."""
import os
import stat
from pathlib import Path


def prepare(root,identity,resume=False):
    if os.geteuid()!=0:raise ValueError('Tool identity isolation requires a root controller inside the container')
    if not isinstance(identity,dict) or set(identity)!={'uid','gid'} or any(type(v)!=int or v<=0 for v in identity.values()):
        raise ValueError('Distinct positive tool uid and gid required')
    root=Path(root).resolve()
    if root==Path('/'):raise ValueError('An isolated project directory is required')
    session=root/'session'
    if session.is_symlink():raise ValueError('Session directory cannot be a symlink')
    if session.exists() and not resume:raise FileExistsError('Existing session; explicitly resume or use a new project')
    if resume and (not session.is_dir() or session.stat().st_uid!=0 or session.stat().st_mode&0o022):
        raise ValueError('Resume requires controller-owned recording state')
    os.umask(0o022)
    # The sticky parent prevents tools from renaming/deleting the protected
    # session directory, while candidate CAD remains writable by the tool UID.
    os.chown(root,0,0);root.chmod(0o1777)
    for base,dirs,files in os.walk(root,followlinks=False):
        if Path(base)==root:dirs[:]=[d for d in dirs if d!='session']
        for name in dirs+files:
            path=Path(base)/name
            if path.is_symlink():continue
            mode=stat.S_IMODE(path.stat().st_mode)&0o777
            path.chmod(mode|(0o700 if path.is_dir() else 0o600))
            os.chown(path,identity['uid'],identity['gid'])
    return dict(controller_uid=0,tool_uid=identity['uid'],tool_gid=identity['gid'],recording='MODEL_WRITE_PROTECTED')


def parse_identity(value):
    try:uid,gid=map(int,value.split(':'))
    except (ValueError,TypeError):raise ValueError('Expected UID:GID')
    return {'uid':uid,'gid':gid}
