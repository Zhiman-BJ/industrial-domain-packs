"""Export a bounded regular artifact from tmpfs, without following symlinks."""
import os
import stat
import sys

limits = {'tool.log': 2 * 1024 * 1024, 'report.json': 65536, 'wave.vcd': 8 * 1024 * 1024}
name = sys.argv[1]
if name not in limits:
    sys.exit(64)
fd = os.open('/work/' + name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
info = os.fstat(fd)
if not stat.S_ISREG(info.st_mode) or info.st_size > limits[name]:
    sys.exit(65)
remaining = limits[name]
while remaining:
    chunk = os.read(fd, min(65536, remaining))
    if not chunk:
        break
    sys.stdout.buffer.write(chunk)
    remaining -= len(chunk)
os.close(fd)
