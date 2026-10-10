"""Linux kernel sandbox for model-authored numerical Python extensions.

The model can read candidate inputs and write only a per-call scratch folder.
Landlock restricts filesystem access even through ctypes; seccomp closes the
ABI-1 truncate/chmod holes and blocks process/network/debugging escape routes.
No Python AST blacklist or prompt instruction is treated as an isolation wall.
"""
import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import sys

# These directories belong to the controller, evaluator, or transport layer.
# They are deliberately excluded even when a launcher happens to place them
# below the candidate workspace.  Candidate reports and solver inputs that
# the model is allowed to inspect live under ``.pcb`` and are unaffected.
PRIVATE_WORKSPACE_DIRS = frozenset({
    '.config',
    'independent-validation', 'reports', 'artifacts', 'assessment',
    'model-runs', 'private',
})


def restrict(workspace, scratch, public_requirements=None):
    if sys.platform != 'linux' or platform.machine() not in ('x86_64', 'aarch64'):
        raise RuntimeError('run_python requires the supported Linux kernel sandbox')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 1:
        raise RuntimeError('Landlock unavailable; refusing unrestricted Python')
    # ABI 1 filesystem rights, with newer rights included when available.
    handled = (1 << 13) - 1
    if abi >= 2: handled |= 1 << 13  # REFER
    if abi >= 3: handled |= 1 << 14  # TRUNCATE
    if abi >= 5: handled |= 1 << 15  # IOCTL_DEV
    read_file, read_dir, write_file = 1 << 2, 1 << 3, 1 << 1

    class Ruleset(ctypes.Structure):
        _fields_ = [('handled_access_fs', ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]

    rule = Ruleset(handled)
    fd = libc.syscall(444, ctypes.byref(rule), ctypes.sizeof(rule), 0)
    if fd < 0: raise OSError(ctypes.get_errno(), 'Landlock create_ruleset')

    def allow(path, rights):
        path = Path(path)
        if not path.exists(): return
        handle = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            access = rights if path.is_dir() else rights & (read_file | write_file | 1 | (1 << 14 if abi >= 3 else 0))
            record = PathRule(access, handle)
            if libc.syscall(445, fd, 1, ctypes.byref(record), 0) < 0:
                raise OSError(ctypes.get_errno(), 'Landlock add_rule: ' + str(path))
        finally: os.close(handle)

    try:
        for path in ('/usr', '/lib', '/lib64'):
            allow(path, read_file | read_dir)
        for path in ('/etc/ld.so.cache', '/etc/localtime', '/dev/urandom'):
            allow(path, read_file)
        allow('/dev/null', read_file | write_file)
        # Do not grant a whole-project directory rule: it would also expose
        # controller logs and symlinked objects. Only resolved candidate files
        # and evidence below .pcb are visible. No model code executes yet.
        root = Path(workspace).resolve()
        for base, dirs, files in os.walk(root, followlinks=False):
            excluded = {'session', '.git', '__pycache__', 'extensions'}
            if Path(base) == root:
                excluded.update(PRIVATE_WORKSPACE_DIRS)
            dirs[:] = [d for d in dirs if d not in excluded and not (Path(base) / d).is_symlink()]
            allow(base, read_dir)
            for name in files:
                source = Path(base) / name
                if not source.is_symlink() and source.resolve().is_relative_to(root):
                    allow(source, read_file)
        if public_requirements: allow(public_requirements, read_file)
        # Public tool observations can be larger than an API context budget.
        # Grant only individual stdout/stderr receipts, never the controller
        # directory, request history, checkpoint or evaluator/runtime sources.
        import re
        for source in (root/'session').glob('action-*.*'):
            if (re.fullmatch(r'action-[0-9]+\.(stdout|stderr)',source.name)
                    and source.is_file() and not source.is_symlink()
                    and source.resolve().parent==root/'session'):
                allow(source,read_file)
        scratch = Path(scratch).resolve()
        if not scratch.is_relative_to(root / '.pcb/extensions'):
            raise ValueError('Extension scratch must be a dedicated project subdirectory')
        allow(scratch, handled & ~(1 | (1 << 15)))
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise OSError(ctypes.get_errno(), 'no_new_privs')
        if libc.syscall(446, fd, 0) < 0:
            raise OSError(ctypes.get_errno(), 'Landlock restrict_self')
    finally: os.close(fd)

    # libseccomp is loaded from the read-only runtime. Deny acquiring another
    # process, socket or privileged handle, and all unmediated metadata/size
    # mutations (older Landlock ABIs do not cover truncate/chmod).
    seccomp = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    if not context: raise RuntimeError('seccomp initialization failed')
    try:
        for name in ('socket', 'socketpair', 'connect', 'bind', 'ptrace', 'process_vm_readv', 'process_vm_writev',
                     'fork', 'vfork', 'clone', 'clone3', 'execve', 'execveat', 'kill', 'tkill', 'tgkill',
                     'mount', 'umount2', 'pivot_root', 'chroot', 'unshare', 'setns', 'open_by_handle_at',
                     'chmod', 'fchmod', 'fchmodat', 'fchmodat2', 'chown', 'fchown', 'lchown', 'fchownat',
                     'truncate', 'ftruncate', 'io_uring_setup', 'bpf', 'userfaultfd', 'perf_event_open'):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError('seccomp rule failed: ' + name)
        if seccomp.seccomp_load(context) != 0: raise RuntimeError('seccomp load failed')
    finally: seccomp.seccomp_release(context)
    return {'landlock_abi': abi, 'project': 'read_only', 'scratch': str(scratch),
            'process_network_debugging': 'denied'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--scratch', required=True)
    parser.add_argument('--code', required=True)
    parser.add_argument('--requirements')
    args = parser.parse_args()
    code = Path(args.code).read_text()
    # Preload native numerical packages before thread creation is restricted.
    # Imports run from the immutable system Python, with -I and no project path.
    try:
        import numpy  # noqa: F401
    except ImportError:
        pass
    boundary = restrict(args.workspace, args.scratch, args.requirements)
    os.environ['PCB_EXTENSION_SCRATCH'] = args.scratch
    print(json.dumps({'extension_boundary': boundary}), file=sys.stderr, flush=True)
    exec(compile(code, '<model-extension>', 'exec'), {'__name__': '__main__'})


if __name__ == '__main__':
    main()
