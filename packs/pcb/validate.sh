#!/bin/sh
exec node "$(dirname "$0")/scripts/validate-image.cjs" "$@"
