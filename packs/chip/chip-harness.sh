#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python="$root/eda-harness/.venv/bin/python"
if [ ! -x "$python" ]; then
  echo 'Run ./install.sh first.' >&2
  exit 1
fi
exec "$python" -m eda_harness.cli "$@"
