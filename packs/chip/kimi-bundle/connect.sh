#!/usr/bin/env bash
# Add only Chip MCP/Skill to an existing official native Kimi; no CLI replacement.
set -Eeuo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
tag=$(basename -- "$source_dir")
prefix="${XDG_DATA_HOME:-$HOME/.local/share}/kimi-chip-bundles/$tag"
kimi_home="${KIMI_CODE_HOME:-$HOME/.kimi-code}"
kimi_bin=''
image=''
skip_image=false
fail() { printf 'Connection failed: %s\n' "$*" >&2; exit 1; }
while (( $# )); do
  case "$1" in
    --prefix|--kimi-bin|--image)
      (( $# >= 2 )) && [[ -n "$2" ]] || fail "Missing value for $1"
      case "$1" in --prefix) prefix=$2;; --kimi-bin) kimi_bin=$2;; --image) image=$2;; esac
      shift 2 ;;
    --skip-image) skip_image=true; shift ;;
    --help|-h)
      printf 'Usage: bash connect.sh [--kimi-bin ABSOLUTE_EXECUTABLE] [--prefix ABSOLUTE_DIR] [--image EXISTING_IMAGE | --skip-image]\n'
      printf 'Connects Chip MCP and native Skill to KIMI_CODE_HOME (default ~/.kimi-code). Requires existing official Kimi Code 2.1.1.\n'
      exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'This runtime supports native Linux x86_64.'
[[ -n "$kimi_bin" ]] || kimi_bin=$(command -v kimi || true)
[[ "$kimi_bin" == /* && -f "$kimi_bin" && -x "$kimi_bin" ]] || fail 'Existing kimi not found. Add it to PATH or pass --kimi-bin ABSOLUTE_EXECUTABLE.'
[[ "$prefix" == /* && "$kimi_home" == /* ]] || fail 'Use absolute prefix and KIMI_CODE_HOME paths.'
[[ "$prefix" != / && "$prefix" != "$HOME" && "$prefix" != "$kimi_home" ]] || fail 'Choose a dedicated runtime directory.'
[[ ! -L "$prefix" && ! -L "$kimi_home" ]] || fail 'Runtime and native home directories must not be symbolic links.'
if "$skip_image" && [[ -n "$image" ]]; then fail '--image and --skip-image cannot be combined.'; fi
export PYTHONNOUSERSITE=1
unset PYTHONHOME PYTHONPATH
# Reject old Python CLI and unqualified versions before touching any native data.
"$source_dir/python/bin/python3" -c \
  'import importlib.util,sys; s=importlib.util.spec_from_file_location("connect",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print("Existing native Kimi:",m.check_kimi(__import__("pathlib").Path(sys.argv[2])))' \
  "$source_dir/connect-configure.py" "$kimi_bin"
mkdir -p -m 700 -- "$kimi_home"
mkdir -p -- "$(dirname -- "$prefix")"
exec 9>"$kimi_home/.chip-connect.lock"
flock -n 9 || fail 'Another connector is updating this Kimi home.'
"$source_dir/python/bin/python3" "$source_dir/configure.py" verify "$source_dir"
if [[ "$source_dir" != "$prefix" ]]; then
  if [[ -e "$prefix" ]]; then
    cmp -s "$source_dir/bundle.json" "$prefix/bundle.json" || fail 'A different runtime already exists at --prefix.'
  else
    scratch=$(mktemp -d "$(dirname -- "$prefix")/.kimi-chip-copy.XXXXXX")
    trap 'rm -rf -- "$scratch"' EXIT
    cp -a -- "$source_dir/." "$scratch/"
    mv -- "$scratch" "$prefix"
    trap - EXIT
  fi
fi
python="$prefix/python/bin/python3"
"$python" "$prefix/configure.py" verify "$prefix"
# Host setup may have needed a temporary Docker-group bridge. Keep it scoped to
# the domain MCP, so users still launch their own native Kimi without a wrapper.
access_dir="${KIMI_CHIP_DOCKER_ACCESS_BIN:-${XDG_DATA_HOME:-$HOME/.local/share}/kimi-chip/docker-access}"
docker_access=''
if [[ -f "$access_dir/docker" && ! -L "$access_dir/docker" ]] && grep -Fxq '# Kimi Chip Docker group helper' "$access_dir/docker"; then
  docker_access=$access_dir
  export KIMI_CHIP_DOCKER_ACCESS_BIN="$access_dir"
  export PATH="$access_dir:$PATH"
fi
PYTHONPATH="$prefix/python-libs" PYTHONNOUSERSITE=1 "$python" "$prefix/mcp-smoke.py"
image_id=''
if ! "$skip_image"; then
  command -v docker >/dev/null || fail 'Docker is missing. Use the public connector for automatic host setup.'
  [[ $(docker info --format '{{.OSType}}/{{.Architecture}}') =~ ^linux/(amd64|x86_64)$ ]] || fail 'Use an accessible native Linux amd64 Docker daemon.'
  if [[ -z "$image" ]]; then
    image=$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["defaultImage"])' "$prefix/bundle.json")
    recipe_hash=$(sha256sum "$prefix/chip/eda-harness/Dockerfile.tools")
    recipe_hash=${recipe_hash%% *}
    if docker image inspect "$image" >/dev/null 2>&1; then
      [[ $(docker image inspect "$image" --format '{{index .Config.Labels "org.zhiman.kimi-chip.recipe"}}') == "$recipe_hash" ]] || fail 'Default image tag is unmanaged. Supply an explicit --image.'
    else
      printf 'Building the pinned Chip tool image; first use downloads large upstream images.\n'
      docker build --platform linux/amd64 --label "org.zhiman.kimi-chip.recipe=$recipe_hash" \
        -f "$prefix/chip/eda-harness/Dockerfile.tools" -t "$image" "$prefix/chip/eda-harness"
    fi
  fi
  "$prefix/bin/eda-chip" tools --image "$image" > "$kimi_home/chip-tool-inventory.json"
  image_id=$(docker image inspect "$image" --format '{{.Id}}')
  [[ $(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}') == linux/amd64 ]] || fail 'Tool image must be Linux amd64.'
fi
"$python" "$prefix/connect-configure.py" "$prefix" --home "$kimi_home" --kimi-bin "$kimi_bin" \
  --image "$image" --image-id "$image_id" --docker-access "$docker_access"
printf '\nConnected. Restart Kimi, then continue using your existing command: %s\n' "$kimi_bin"
printf 'Native configuration, credentials and sessions remain in: %s\n' "$kimi_home"
printf 'In a new interactive session: /mcp to check Chip, then /skill:chip-design\n'
printf 'Batch: kimi -p "TASK" --output-format stream-json\n'
