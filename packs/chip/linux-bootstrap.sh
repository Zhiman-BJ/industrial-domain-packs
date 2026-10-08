#!/usr/bin/env bash
# Public wget entry point. The packaging script pins the self-extracting installer.
set -Eeuo pipefail

INSTALLER_URL='@INSTALLER_URL@'
INSTALLER_SHA256='@INSTALLER_SHA256@'
skip_image=false
system_only=false
scratch=''
fail() { printf 'Installation failed: %s\n' "$*" >&2; exit 1; }
cleanup() { if [[ -n "$scratch" ]]; then rm -rf -- "$scratch"; fi; }
trap cleanup EXIT
args=()
while (( $# )); do
  case "$1" in
    --prefix|--bin-dir)
      (( $# >= 2 )) && [[ "$2" == /* ]] || fail "$1 requires an absolute directory"
      args+=("$1" "$2"); shift 2 ;;
    --skip-image) skip_image=true; args+=("$1"); shift ;;
    --system-only) system_only=true; shift ;;
    --help|-h)
      cat <<'HELP'
Industrial Harness Chip Linux complete installer
Usage: bash install-chip-linux.sh [--prefix DIRECTORY] [--bin-dir DIRECTORY] [--skip-image]
Installs CLI, Chip Pack, private Node/Python/Kimi and the native EDA image.
On Ubuntu/Debian hosts, installs missing curl, bubblewrap and ripgrep via apt; Docker setup needs systemd.
Run as the intended user; sudo is requested only for missing system dependencies.
Existing accessible Docker is reused. Model credentials and project/PDK are supplied separately.
  --system-only       Prepare/check system dependencies without installing the CLI
HELP
      exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'Use native x86-64 Linux.'
for program in wget sha256sum mktemp awk tar flock; do
  command -v "$program" >/dev/null || fail "Missing base utility: $program"
done
scratch=$(mktemp -d)
if ! "$system_only"; then
  printf 'Downloading and checking the pinned CLI + Chip Pack installer\n'
  wget --https-only --tries=3 --timeout=30 "$INSTALLER_URL" -O "$scratch/install.run"
  actual=$(sha256sum "$scratch/install.run")
  [[ "${actual%% *}" == "$INSTALLER_SHA256" ]] || fail 'Installer checksum mismatch; no downloaded installer was executed.'
fi
run_as_root() {
  if (( EUID == 0 )); then "$@"; else sudo -- "$@"; fi
}
prepare_root() {
  if (( EUID != 0 )); then
    command -v sudo >/dev/null || fail 'Install sudo or run this installer as root.'
    sudo -v
  fi
}
need_docker=false
if ! "$skip_image" && ! command -v docker >/dev/null; then need_docker=true; fi
if "$need_docker" || ! command -v curl >/dev/null || ! command -v bwrap >/dev/null || ! command -v rg >/dev/null; then
  [[ -r /etc/os-release ]] || fail 'Cannot identify this Linux distribution.'
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;;
    *) fail 'Automatic system dependencies support Ubuntu 22.04/24.04/26.04 and Debian 12/13. Install curl, bubblewrap, ripgrep and Docker first on other distributions.' ;;
  esac
  if "$need_docker"; then
    [[ -d /run/systemd/system ]] || fail 'Automatic Docker setup needs a systemd host. In containers, supply an accessible Docker daemon first.'
    for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
      if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true) == 'install ok installed' ]]; then
        fail "Existing $package conflicts with Docker CE; resolve it before installation."
      fi
    done
  fi
  prepare_root
  printf 'Preparing missing system dependencies with the official apt repositories\n'
  run_as_root apt-get -o DPkg::Lock::Timeout=60 update
  run_as_root env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=60 install -y --no-install-recommends ca-certificates curl bubblewrap ripgrep
  if "$need_docker"; then
    suite=${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}
    [[ "$suite" =~ ^[a-z]+$ ]] || fail 'Cannot determine the Docker repository suite.'
    if ! grep -Rqs "download.docker.com/linux/$ID" /etc/apt/sources.list.d /etc/apt/sources.list 2>/dev/null; then
      key=/etc/apt/keyrings/industrial-harness-docker.asc
      source=/etc/apt/sources.list.d/industrial-harness-docker.sources
      [[ ! -e "$key" && ! -L "$key" && ! -e "$source" && ! -L "$source" ]] || fail 'Refusing to overwrite existing Docker repository files.'
      wget --https-only --tries=3 --timeout=30 -q "https://download.docker.com/linux/$ID/gpg" -O "$scratch/docker.asc"
      run_as_root install -m 0755 -d /etc/apt/keyrings
      run_as_root install -m 0644 "$scratch/docker.asc" "$key"
      printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: amd64\nSigned-By: %s\n' "$ID" "$suite" "$key" > "$scratch/docker.sources"
      run_as_root install -m 0644 "$scratch/docker.sources" "$source"
      run_as_root apt-get -o DPkg::Lock::Timeout=60 update
    fi
    run_as_root env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=60 install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin
    run_as_root systemctl enable --now docker
  fi
fi

command -v bwrap >/dev/null || fail 'Install bubblewrap (bwrap) before running the protected Linux Agent.'
bwrap --unshare-user --unshare-pid --ro-bind / / --proc /proc --dev /dev -- /bin/true \
  || fail 'Bubblewrap cannot create the required user/PID namespaces. Check the host namespace policy; no unrestricted Agent fallback is used.'

write_docker_helper() {
  [[ ! -L "$access_dir" && ! -L "$access_dir/docker" ]] || fail 'Docker access helper cannot be a symbolic link.'
  if [[ -e "$access_dir/docker" ]]; then
    grep -Fxq '# Industrial Harness Docker group helper' "$access_dir/docker" || fail 'Refusing to replace an unmanaged Docker helper.'
  fi
  mkdir -p -- "$access_dir"
  {
    printf '#!/usr/bin/env bash\n# Industrial Harness Docker group helper\nset -euo pipefail\n'
    printf 'docker_binary=%q\n' "$docker_binary"
    cat <<'WRAPPER'
# Reuse effective process credentials. Never retry a Docker operation after it fails.
if (( EUID == 0 )); then exec "$docker_binary" "$@"; fi
docker_gid=$(getent group docker | cut -d: -f3)
case " $(id -G) " in
  *" $docker_gid "*) [[ -n "$docker_gid" ]] && exec "$docker_binary" "$@" ;;
esac
if [[ -r /proc/self/status ]] && awk '/^NoNewPrivs:/ {exit $2 != 1}' /proc/self/status; then
  printf 'Docker group activation is unavailable in the protected Agent. Use industrial_action_call through the host Domain Runtime; native Shell and child MCP cannot access host Docker.\n' >&2
  exit 77
fi
quote() { printf "'%s' " "${1//\'/\'\\\'\'}"; }
command="exec $(quote "$docker_binary")"
for argument in "$@"; do command+="$(quote "$argument") "; done
exec sg docker -c "$command"
WRAPPER
  } > "$scratch/docker-helper"
  chmod 755 "$scratch/docker-helper"
  mv -- "$scratch/docker-helper" "$access_dir/docker"
  export HARNESS_DOCKER_ACCESS_BIN="$access_dir"
  export PATH="$access_dir:$PATH"
}

if ! "$skip_image"; then
  access_dir="${XDG_DATA_HOME:-$HOME/.local/share}/industrial-harness/docker-access"
  # An upgrade may inherit an older managed helper in PATH. Find the actual
  # client instead of creating a helper that recursively invokes itself.
  docker_binary=''
  while IFS= read -r candidate; do
    if ! grep -Fxq '# Industrial Harness Docker group helper' "$candidate" 2>/dev/null; then
      docker_binary=$candidate; break
    fi
  done < <(type -a -p docker)
  [[ -n "$docker_binary" ]] || fail 'Cannot locate the real Docker client outside the managed helper.'
  if [[ -f "$access_dir/docker" ]] && grep -Fxq '# Industrial Harness Docker group helper' "$access_dir/docker"; then
    write_docker_helper
  fi
  if ! docker info >/dev/null 2>&1; then
    [[ ${DOCKER_HOST:-unix:///var/run/docker.sock} == unix:///var/run/docker.sock ]] || fail 'Configured Docker daemon is inaccessible; check DOCKER_HOST.'
    [[ ${DOCKER_CONTEXT:-default} == default ]] || fail 'Configured Docker context is inaccessible.'
    [[ $(docker context show) == default ]] || fail 'Configured Docker context is inaccessible.'
    prepare_root
    run_as_root docker info >/dev/null || fail 'Docker daemon is unavailable; check its service.'
    (( EUID != 0 )) || fail 'Docker daemon is inaccessible.'
    [[ -S /var/run/docker.sock && $(stat -c '%G' /var/run/docker.sock) == docker ]] || fail 'Docker socket does not use the docker group.'
    username=$(id -un)
    run_as_root usermod -aG docker "$username"
    # sg is used only until the new group is present in process credentials.
    write_docker_helper
  fi
  daemon_arch=$(docker info --format '{{.Architecture}}') || fail 'Docker is inaccessible after setup.'
  [[ "$daemon_arch" == amd64 || "$daemon_arch" == x86_64 ]] || fail 'Docker daemon must be native amd64.'
fi
if "$system_only"; then
  printf 'System dependencies ready.\n'
  if ! "$skip_image"; then docker version --format '{{.Server.Version}}'; fi
  exit 0
fi

bash "$scratch/install.run" "${args[@]}"
