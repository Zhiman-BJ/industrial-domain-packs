#!/bin/sh
# Export a pushed registry image as a compressed oci-archive tarball plus a
# sha256 sidecar, and emit step outputs (digest, config_digest, archive,
# archive_sha256, archive_bytes). Requires skopeo and jq on PATH.
# Usage: export-oci-archive.sh <image-name> <tag> <push-digest> <namespace>
set -eu
image="$1"
tag="$2"
digest="$3"
namespace="$4"
ref="docker://ghcr.io/${namespace}/${image}@${digest}"
raw=$(skopeo inspect --raw "$ref")
if printf '%s' "$raw" | jq -e 'has("manifests")' >/dev/null; then
  platform_digest=$(printf '%s' "$raw" | jq -r '.manifests[] | select(.platform.os=="linux" and .platform.architecture=="amd64") | .digest')
  config_digest=$(skopeo inspect --raw "docker://ghcr.io/${namespace}/${image}@${platform_digest}" | jq -r '.config.digest')
else
  config_digest=$(printf '%s' "$raw" | jq -r '.config.digest')
fi
archive="${image}-${tag}.tar"
# --override-* selects the linux/amd64 child when the digest is an index, and
# keeps the archive single-manifest so `docker load` accepts it.
skopeo copy --override-os linux --override-arch amd64 "$ref" "oci-archive:${archive}"
bytes=$(stat -c '%s' "$archive")
sha=$(sha256sum "$archive" | cut -d' ' -f1)
sha256sum "$archive" > "${archive}.sha256"
{
  echo "digest=${digest}"
  echo "config_digest=${config_digest}"
  echo "archive=${archive}"
  echo "archive_sha256=${sha}"
  echo "archive_bytes=${bytes}"
} >> "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
