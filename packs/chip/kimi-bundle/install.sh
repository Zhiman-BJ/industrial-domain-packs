#!/usr/bin/env bash
# Install the unpacked Linux bundle without altering upstream Kimi.
set -Eeuo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
tag=$(basename -- "$source_dir")
prefix="${XDG_DATA_HOME:-$HOME/.local/share}/kimi-chip-bundles/$tag"
bin_dir="$HOME/.local/bin"
kimi_home="${KIMI_CODE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/kimi-chip}"
skip_image=false
image=''
while (( $# )); do
  case "$1" in
    --prefix|--bin-dir|--image)
      (( $# >= 2 )) && [[ -n "$2" ]] || { printf 'Missing value for %s\n' "$1" >&2; exit 64; }
      case "$1" in --prefix) prefix=$2;; --bin-dir) bin_dir=$2;; --image) image=$2;; esac
      shift 2;;
    --skip-image) skip_image=true; shift;;
    --help|-h)
      printf 'Usage: bash install.sh [--prefix ABSOLUTE_DIR] [--bin-dir ABSOLUTE_DIR] [--image EXISTING_IMAGE | --skip-image]\n'
      exit 0;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 64;;
  esac
done
fail() { printf 'Installation failed: %s\n' "$*" >&2; exit 1; }
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'Use native Linux x86_64.'
[[ "$prefix" == /* && "$bin_dir" == /* && "$kimi_home" == /* ]] || fail 'Use absolute paths.'
[[ "$prefix" != / && "$prefix" != "$HOME" && "$prefix" != "$kimi_home" && "$prefix" != "$bin_dir" ]] || fail 'Choose a dedicated bundle directory.'
[[ ! -L "$prefix" && ! -L "$kimi_home" && ! -L "$bin_dir" ]] || fail 'Installation directories must not be symbolic links.'
if "$skip_image" && [[ -n "$image" ]]; then fail '--image and --skip-image cannot be combined.'; fi
mkdir -p -m 700 -- "$kimi_home"
mkdir -p -- "$(dirname -- "$prefix")"
exec 9>"$kimi_home/.bundle-install.lock"
flock -n 9 || fail 'Another installer is updating this Kimi home.'
"$source_dir/python/bin/python3" "$source_dir/configure.py" verify "$source_dir"
if [[ "$source_dir" != "$prefix" ]]; then
  if [[ -e "$prefix" ]]; then
    cmp -s "$source_dir/bundle.json" "$prefix/bundle.json" || fail 'Different bundle already exists at --prefix.'
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
"$prefix/upstream/kimi" --version
PYTHONPATH="$prefix/python-libs" PYTHONNOUSERSITE=1 "$python" "$prefix/mcp-smoke.py"
image_id=''
if ! "$skip_image"; then
  command -v docker >/dev/null || fail 'Docker is missing; use the public bootstrap for automatic host setup.'
  [[ $(docker info --format '{{.OSType}}/{{.Architecture}}') =~ ^linux/(amd64|x86_64)$ ]] || fail 'Use an accessible native Linux amd64 Docker daemon.'
  if [[ -z "$image" ]]; then
    image=$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["defaultImage"])' "$prefix/bundle.json")
    recipe_hash=$(sha256sum "$prefix/chip/eda-harness/Dockerfile.tools")
    recipe_hash=${recipe_hash%% *}
    if docker image inspect "$image" >/dev/null 2>&1; then
      [[ $(docker image inspect "$image" --format '{{index .Config.Labels "org.zhiman.kimi-chip.recipe"}}') == "$recipe_hash" ]] || fail 'Default image tag is unmanaged. Supply an explicit existing --image or remove the conflicting tag.'
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
"$python" "$prefix/configure.py" configure "$prefix" --home "$kimi_home" --bin-dir "$bin_dir" --image "$image" --image-id "$image_id"
printf '\nInstalled: %s/kimi-chip\n' "$bin_dir"
printf 'Native config and trajectories: %s\n' "$kimi_home"
printf 'Sign in: %s/kimi-chip login\n' "$bin_dir"
printf 'Run from your project directory. Native batch flags: -p "TASK" --output-format stream-json\n'
