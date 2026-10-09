#!/usr/bin/env bash
# Public wget entry: register Chip Pack with the user's already-installed Kimi.
set -Eeuo pipefail
BUNDLE_URL='@BUNDLE_URL@'
BUNDLE_SHA256='@BUNDLE_SHA256@'
skip_image=false
kimi_bin=''
args=()
scratch=''
fail() { printf 'Connection failed: %s\n' "$*" >&2; exit 1; }
cleanup() { if [[ -n "$scratch" ]]; then rm -rf -- "$scratch"; fi; }
trap cleanup EXIT
while (( $# )); do
  case "$1" in
    --prefix|--kimi-bin)
      (( $# >= 2 )) && [[ "$2" == /* ]] || fail "$1 requires an absolute path"
      [[ "$1" != --kimi-bin ]] || kimi_bin=$2
      args+=("$1" "$2"); shift 2 ;;
    --image)
      (( $# >= 2 )) && [[ -n "$2" ]] || fail '--image requires an existing image'
      args+=("$1" "$2"); shift 2 ;;
    --skip-image) skip_image=true; args+=("$1"); shift ;;
    --help|-h)
      cat <<'HELP'
Connect Chip Domain Pack to existing official Kimi Code 2.1.1 on Linux amd64.
Usage: bash connect-kimi-chip.sh [--kimi-bin ABSOLUTE_EXECUTABLE] [--prefix ABSOLUTE_DIR] [--image EXISTING_IMAGE | --skip-image]
Uses KIMI_CODE_HOME when set, otherwise ~/.kimi-code; preserves native credentials and sessions.
Installs private Python, direct Chip MCP and a native Skill. It never installs/replaces Kimi.
Reuses the same verified runtime archive as the standalone bundle.
On Ubuntu/Debian, missing host tools and Docker can be prepared automatically.
--skip-image connects CLI/MCP/Skill only; engineering execution still requires EDA tools.
HELP
      exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'Use native Linux x86_64.'
[[ -n "$kimi_bin" ]] || kimi_bin=$(command -v kimi || true)
[[ "$kimi_bin" == /* && -f "$kimi_bin" && -x "$kimi_bin" ]] || fail 'Existing kimi not found. Add it to PATH or pass --kimi-bin ABSOLUTE_EXECUTABLE.'
version=$("$kimi_bin" --version) || fail 'Could not inspect existing Kimi version.'
[[ "$version" =~ (^|[^[:alnum:].])2\.1\.1([^[:alnum:].-]|$) ]] || fail 'Only official native Kimi Code 2.1.1 is currently qualified. This connector never upgrades your CLI.'
for program in wget sha256sum mktemp tar flock; do
  command -v "$program" >/dev/null || fail "Missing base utility: $program"
done
scratch=$(mktemp -d)
printf 'Downloading and checking the pinned Chip runtime\n'
wget --https-only --tries=3 --timeout=30 "$BUNDLE_URL" -O "$scratch/bundle.tar.gz"
actual=$(sha256sum "$scratch/bundle.tar.gz")
[[ "${actual%% *}" == "$BUNDLE_SHA256" ]] || fail 'Bundle checksum mismatch; downloaded code was not executed.'
tar -xzf "$scratch/bundle.tar.gz" --no-same-owner -C "$scratch"
setup_args=(--system-only)
if "$skip_image"; then setup_args+=(--skip-image); fi
bash "$scratch/@RELEASE_TAG@/host-setup.sh" "${setup_args[@]}"
bash "$scratch/@RELEASE_TAG@/connect.sh" "${args[@]}"
