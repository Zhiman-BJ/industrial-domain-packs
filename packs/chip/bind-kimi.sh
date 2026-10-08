#!/bin/sh
set -eu
if [ "$#" -ne 2 ]; then
  echo 'Usage: ./bind-kimi.sh PROJECT_DIR NEW_OUTPUT_DIR' >&2
  exit 2
fi
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python="$root/eda-harness/.venv/bin/python"
if [ ! -x "$python" ]; then
  echo 'Run ./install.sh first.' >&2
  exit 1
fi
exec "$python" "$root/eda-harness/scripts/build_adapters.py" --project "$1" --output "$2"
