#!/usr/bin/env bash
# Header for a self-extracting Chip CLI installer. Build with package-linux-chip-installer.cjs.
set -Eeuo pipefail

PAYLOAD_SHA256='@PAYLOAD_SHA256@'
PAYLOAD_DIRECTORY='@PAYLOAD_DIRECTORY@'
RELEASE_TAG='@RELEASE_TAG@'
prefix="${XDG_DATA_HOME:-$HOME/.local/share}/industrial-harness/@RELEASE_TAG@"
bin_dir="$HOME/.local/bin"
image='eda-harness-tools:@RELEASE_TAG@'
skip_image=false
scratch=''
stage='preflight'

usage() {
  cat <<'HELP'
Industrial Harness Chip Linux installer
Usage: bash industrial-harness-chip-linux-install.run [options]
  --prefix DIRECTORY  Private package/runtime directory (absolute path)
  --bin-dir DIRECTORY Launcher directory (default: ~/.local/bin)
  --skip-image        Install CLI/MCP only; do not build/check the EDA image
  --help              Show this help

Requires x86-64 Linux, bash, curl, tar, sha256sum, awk, flock, bubblewrap, ripgrep and usable Docker.
Installs private Node 24.12.0, uv 0.11.6, Python 3.13, bundled Kimi Code 2.1.1 and Chip Pack.
Does not change model credentials, projects, shell profiles or the Docker engine.
HELP
}
fail() { printf 'Installation failed: %s\n' "$*" >&2; exit 1; }
cleanup() {
  local status=$?
  if [[ -n "$scratch" ]]; then rm -rf -- "$scratch"; fi
  if (( status != 0 )); then
    printf 'Stopped during %s. Fix the reported problem and rerun the same installer.\n' "$stage" >&2
  fi
}
trap cleanup EXIT
while (( $# )); do
  case "$1" in
    --prefix|--bin-dir)
      (( $# >= 2 )) && [[ -n "$2" && "$2" != --* ]] || fail "$1 requires a directory"
      if [[ "$1" == --prefix ]]; then prefix=$2; else bin_dir=$2; fi
      shift 2 ;;
    --skip-image) skip_image=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'Use native x86-64 Linux.'
[[ "$prefix" == /* && "$bin_dir" == /* ]] || fail 'Use absolute installation paths.'
[[ "$prefix" != / && "$prefix" != "$HOME" && "$prefix" != "$bin_dir" ]] || fail 'Choose a dedicated installation directory.'
[[ ! -L "$prefix" && ! -L "$bin_dir" ]] || fail 'Installation directories cannot be symbolic links.'
for program in curl tar sha256sum awk flock tail mktemp bwrap rg; do
  command -v "$program" >/dev/null || fail "Missing prerequisite: $program"
done
bwrap --unshare-user --unshare-pid --ro-bind / / --proc /proc --dev /dev -- /bin/true \
  || fail 'Required user/PID namespaces are unavailable; protected Agent execution cannot start.'
if ! "$skip_image"; then
  command -v docker >/dev/null || fail 'Install Docker Engine first: https://docs.docker.com/engine/install/ubuntu/'
  daemon_arch=$(docker info --format '{{.Architecture}}') || fail 'Current user cannot access Docker; check docker info.'
  [[ "$daemon_arch" == x86_64 || "$daemon_arch" == amd64 ]] || fail 'Docker daemon must be native amd64.'
fi
mkdir -p -- "$(dirname -- "$prefix")"
exec 9>"${prefix}.install.lock"
flock -n 9 || fail 'Another installer is using this directory.'
if [[ -e "$prefix" ]]; then
  [[ -f "$prefix/.harness-linux-payload" ]] || fail 'Existing installation directory is not managed by this installer.'
  [[ $(cat "$prefix/.harness-linux-payload") == "$PAYLOAD_SHA256" ]] || fail 'Different package already installed here; choose another --prefix.'
fi
launcher="$bin_dir/industrial-harness-chip"
if [[ -e "$launcher" || -L "$launcher" ]]; then
  [[ ! -L "$launcher" && -f "$launcher" ]] || fail 'Existing launcher is not a managed regular file.'
  grep -Fxq '# Industrial Harness Chip managed launcher' "$launcher" || fail 'Refusing to replace an existing unmanaged launcher.'
fi
scratch=$(mktemp -d "$(dirname -- "$prefix")/.harness-install.XXXXXX")
verify() {
  local actual
  actual=$(sha256sum "$1")
  [[ "${actual%% *}" == "$2" ]] || fail "Checksum mismatch: $(basename -- "$1")"
}
download() {
  local url=$1 destination=$2 digest=$3
  if [[ ! -f "$destination" ]]; then
    curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fL --retry 3 --connect-timeout 30 \
      "$url" -o "${destination}.part"
    verify "${destination}.part" "$digest"
    mv -- "${destination}.part" "$destination"
  fi
  verify "$destination" "$digest"
}

stage='payload verification'
payload_line=$(awk '$0 == "__HARNESS_ARCHIVE_BELOW__" {print NR + 1; exit}' "$0")
[[ "$payload_line" =~ ^[0-9]+$ ]] || fail 'Missing bundled CLI archive; use the generated .run file.'
tail -n "+$payload_line" "$0" > "$scratch/payload.tar.gz"
verify "$scratch/payload.tar.gz" "$PAYLOAD_SHA256"
if [[ ! -e "$prefix" ]]; then
  tar -xzf "$scratch/payload.tar.gz" --no-same-owner -C "$scratch"
  [[ -f "$scratch/$PAYLOAD_DIRECTORY/industrial-harness.cjs" ]] || fail 'Invalid Chip CLI archive.'
  mkdir -- "$prefix"
  printf '%s\n' "$PAYLOAD_SHA256" > "$prefix/.harness-linux-payload"
  mv -- "$scratch/$PAYLOAD_DIRECTORY" "$prefix/cli"
fi
[[ -f "$prefix/cli/industrial-harness.cjs" ]] || fail 'Incomplete package directory.'
mkdir -p -- "$prefix/runtime" "$prefix/cache" "$prefix/logs"
cli="$prefix/cli"
pack="$cli/domain-packs/chip"

stage='Node installation'
printf '[1/5] Preparing private Node 24.12.0\n'
node_archive="$prefix/cache/node-v24.12.0-linux-x64.tar.gz"
download 'https://nodejs.org/dist/v24.12.0/node-v24.12.0-linux-x64.tar.gz' "$node_archive" \
  '6159227e0af7d7c3c6bb2fa900452b04a6cb8841a702a79acc613209d70b04d0'
node_dir="$prefix/runtime/node-v24.12.0-linux-x64"
if [[ ! -x "$node_dir/bin/node" ]]; then tar -xzf "$node_archive" --no-same-owner -C "$prefix/runtime"; fi
[[ $("$node_dir/bin/node" --version) == v24.12.0 ]] || fail 'Private Node version is incorrect.'

stage='uv installation'
printf '[2/5] Preparing private uv 0.11.6\n'
uv_archive="$prefix/cache/uv-0.11.6-linux-x64.tar.gz"
download 'https://github.com/astral-sh/uv/releases/download/0.11.6/uv-x86_64-unknown-linux-gnu.tar.gz' "$uv_archive" \
  '0c6bab77a67a445dc849ed5e8ee8d3cb333b6e2eba863643ce1e228075f27943'
uv_dir="$prefix/runtime/uv-x86_64-unknown-linux-gnu"
if [[ ! -x "$uv_dir/uv" ]]; then tar -xzf "$uv_archive" --no-same-owner -C "$prefix/runtime"; fi
[[ $("$uv_dir/uv" --version) == 'uv 0.11.6'* ]] || fail 'Private uv version is incorrect.'
export PATH="$node_dir/bin:$uv_dir:$PATH"
export UV_CACHE_DIR="$prefix/cache/uv"
export UV_PYTHON_INSTALL_DIR="$prefix/runtime/python"
export UV_NO_PROGRESS=1

stage='Python, MCP and bundled Kimi verification'
printf '[3/5] Installing Chip MCP dependencies and bundled Kimi Code 2.1.1\n'
(cd "$pack/eda-harness"; uv sync --frozen --no-dev --managed-python --python 3.13)
"$pack/eda-harness/.venv/bin/python" "$pack/scripts/mcp-smoke.py" > "$prefix/logs/mcp-smoke.log" 2>&1
export KIMI_EXECUTABLE="$(node - "$cli" <<'JS'
const path = require('node:path');
const entry = require.resolve('@industrial-agent-harness/agent-kimi', {paths:[process.argv[2]]});
process.stdout.write(require(path.join(path.dirname(entry), 'code-session.cjs')).bundledExecutable());
JS
)"
node "$KIMI_EXECUTABLE" --version

image_id=''
if ! "$skip_image"; then
  stage='EDA image build'
  printf '[4/5] Building native EDA image (first run downloads large upstream images)\n'
  builder=(docker)
  if ! docker buildx version >/dev/null 2>&1; then
    daemon_host=${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}
    [[ "$daemon_host" == unix://* ]] || fail 'Install Docker Buildx for this Docker context, then retry.'
    plugin_dir="$prefix/runtime/docker-config/cli-plugins"
    mkdir -p -- "$plugin_dir"
    download 'https://github.com/docker/buildx/releases/download/v0.25.0/buildx-v0.25.0.linux-amd64' \
      "$plugin_dir/docker-buildx" '4104d79a791a8744c0b43fd5bd0a6172dff29040c5229946a1cdb2d27b0b5bfa'
    chmod 755 "$plugin_dir/docker-buildx"
    builder=(docker --config "$prefix/runtime/docker-config" --host "$daemon_host")
  fi
  # Build from a fresh package copy; omit installed Python environments from context.
  tar -xzf "$scratch/payload.tar.gz" --no-same-owner -C "$scratch"
  context="$scratch/$PAYLOAD_DIRECTORY/domain-packs/chip/eda-harness"
  "${builder[@]}" build --platform linux/amd64 -f "$context/Dockerfile.tools" -t "$image" "$context" \
    > "$prefix/logs/image-build.log" 2>&1 || { tail -n 40 "$prefix/logs/image-build.log" >&2; fail 'EDA image build failed.'; }
  "$pack/chip-harness.sh" tools --image "$image" > "$prefix/logs/tool-inventory.json"
  image_id=$(docker image inspect "$image" --format '{{.Id}}')
  [[ $(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}') == linux/amd64 ]] || fail 'EDA image architecture is incorrect.'
else
  printf '[4/5] EDA image explicitly skipped\n'
fi

stage='CLI verification and launcher'
printf '[5/5] Verifying CLI scope and creating launcher\n'
mkdir -p -- "$prefix/check-project" "$prefix/check-config" "$bin_dir"
node - "$cli" "$prefix/check-project" "$prefix/check-config" "$KIMI_EXECUTABLE" <<'JS'
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const [cli, projectDir, shareDir, kimi] = process.argv.slice(2);
const agent = require.resolve('@industrial-agent-harness/agent-kimi', {paths:[cli]});
const {createProcessSandbox} = require(path.join(path.dirname(agent), 'process-sandbox.cjs'));
const sandbox = createProcessSandbox({executable:kimi, projectDir, shareDir, protectedPaths:[cli], kimiProjectAccess:true});
try {
 const result=spawnSync(sandbox.executable,['--version'],{env:sandbox.env,encoding:'utf8'});
 if(result.status!==0 || !result.stdout.includes('2.1.1')) throw Error(result.stderr || 'Protected Kimi startup failed');
 fs.writeFileSync(path.join(shareDir,'process-boundary.json'),JSON.stringify(sandbox.boundary,null,2)+'\n');
} finally {sandbox.close();}
JS
INDUSTRIAL_HARNESS_CONFIG_DIR="$prefix/check-config" node "$cli/industrial-harness.cjs" run \
  --project-dir "$prefix/check-project" --task 'Inspect netlist signals' --scope-only \
  > "$prefix/logs/cli-scope.jsonl"
{
  printf '#!/usr/bin/env bash\n# Industrial Harness Chip managed launcher\nset -e\n'
  launcher_path="$node_dir/bin:$uv_dir"
  if [[ -n ${HARNESS_DOCKER_ACCESS_BIN:-} ]]; then
    [[ -x "$HARNESS_DOCKER_ACCESS_BIN/docker" ]] || fail 'Docker access helper is missing.'
    launcher_path+=":$HARNESS_DOCKER_ACCESS_BIN"
  fi
  printf 'export PATH=%q:"$PATH"\n' "$launcher_path"
  printf 'export KIMI_EXECUTABLE=%q\n' "$KIMI_EXECUTABLE"
  printf 'exec %q %q "$@"\n' "$node_dir/bin/node" "$cli/industrial-harness.cjs"
} > "$scratch/launcher"
chmod 755 "$scratch/launcher"
mv -- "$scratch/launcher" "$launcher"
"$pack/eda-harness/.venv/bin/python" - "$prefix" "$PAYLOAD_SHA256" "$image" "$image_id" "$launcher" <<'PY'
import json,sys
from pathlib import Path
from datetime import datetime,timezone
prefix,digest,image,image_id,launcher=sys.argv[1:]
root=Path(prefix)
record={"status":"INSTALLED","finished_at":datetime.now(timezone.utc).isoformat(),"payload_sha256":digest,"package":json.loads((root/"cli/HARNESS-PACKAGE.json").read_text()),"node":"24.12.0","uv":"0.11.6","kimi":"2.1.1","mcp_smoke":"PASS","cli_scope":"PASS","protected_kimi_startup":"PASS","process_boundary":json.loads((root/"check-config/process-boundary.json").read_text()),"image":image if image_id else None,"image_id":image_id or None,"image_inventory":"PASS" if image_id else "NOT_TESTED","launcher":launcher,"engineering_acceptance":"NOT_RUN"}
(root/"install-receipt.json").write_text(json.dumps(record,indent=2)+"\n")
PY
printf '\nInstallation complete. Launcher: %s\n' "$launcher"
printf 'Set model credentials in your environment, then run the launcher with --project-dir and --task.\n'
printf 'For engineering actions, set eda.yaml runtime.image to %s and require_native to true.\n' "$image"
printf 'Install receipt and logs: %s\n' "$prefix"
exit 0
