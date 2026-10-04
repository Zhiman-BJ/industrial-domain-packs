#!/bin/sh
exec node "$(dirname "$0")/packs/chip/scripts/validate-image.cjs" "$@"
