"""Controller-owned tool budgets, shared with the upstream MCP client."""
import os


def limits():
    result = {}
    for name, default, maximum in (
        ('PCB_TOOL_TIMEOUT_SECONDS', 900, 86400),
        ('PCB_PYTHON_TIMEOUT_SECONDS', 120, 3600),
    ):
        raw = os.environ.get(name, str(default))
        if not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) <= maximum:
            raise ValueError(f'{name} must be an integer from 1 to {maximum}')
        result[name] = int(raw)
    return result


def tool_timeout(name):
    return limits()['PCB_PYTHON_TIMEOUT_SECONDS' if name == 'run_python' else 'PCB_TOOL_TIMEOUT_SECONDS']


def client_timeout_ms():
    # Leave time to terminate the child and persist its receipt before transport expires.
    return (max(limits().values()) + 60) * 1000
