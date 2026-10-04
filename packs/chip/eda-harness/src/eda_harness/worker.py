"""Detached process: job ownership is independent of the CLI/MCP client session."""

import sys

from eda_harness.core.service import Harness

if __name__ == "__main__":
    Harness(sys.argv[1]).execute(sys.argv[2])
