# Runtime image release runbook

This repository is the public release surface for the container images its
packs define. Images ship through two free channels:

1. **GitHub Release assets (primary, anonymous today)** — compressed
   `oci-archive` tarballs (`<image>-<tag>.tar` + `.sha256`): download,
   verify, `docker load`. Works for registry-free, air-gapped or
   bandwidth-constrained installs and is the acceptance gate of every
   release run.
2. **GHCR (pull channel, pending org policy)** —
   `ghcr.io/zhiman-bj/<image>:<tag>` for `docker pull` and Engine-API
   consumers once the packages are public. Org package policy currently
   locks visibility to Private (see first-release notes below); pushes
   already happen, anonymous pull activates the moment the packages are
   flipped public.

The organization Harbor stays internal-only; nothing here depends on it.
`provenance/runtime-distribution.json` is the single lock that records what is
currently released (image digest, config digest, archive sha256/size, source
commit) and how to address both channels.

## Released images

| Image | Pack | Recipe | Tag prefix |
| --- | --- | --- | --- |
| `industrial-pcb-runtime` | pcb | `packs/pcb/Dockerfile.local-dev` | `pcb-v*` |
| `industrial-chip-tools` | chip | `packs/chip/eda-harness/Dockerfile.tools` | `chip-v*` |
| `industrial-chip-rtl` | chip | `packs/chip/images/Dockerfile.rtl-cpu` | `chip-v*` |

Recipes pin their base layers by digest. Bump a base digest deliberately, in
its own change, with the reason recorded in the commit message.

## Releasing

1. Ensure the recipes and tests on `main` are what you want to ship
   (images are built from the pushed tag's commit).
2. Tag and push, e.g. `git tag pcb-v1.0.0 && git push origin pcb-v1.0.0`.
   Tag shape is `<pack>-v<semver>`; `pcb-v*` releases the pcb image,
   `chip-v*` releases both chip images under the same tag.
3. The `release-images` workflow then, without human intervention:
   builds for `linux/amd64`, pushes to GHCR, exports the compressed
   `oci-archive` tarball, attaches the tarball + `.sha256` to the GitHub
   Release, **verifies the release assets anonymously** (full download and
   sha256 against the recorded identity), checks the GHCR pull channel
   (non-fatal warning while org policy keeps the packages private), and
   opens a lock-update pull request against
   `provenance/runtime-distribution.json`.
4. Merge the lock PR after reviewing it (never merged automatically). The
   release is complete when the lock PR is merged.

Re-running for an existing tag: `workflow_dispatch` with the tag (and pack)
inputs; assets are re-uploaded with `--clobber` and the lock branch is
force-updated.

## One-time setup / first release notes

- **GHCR package visibility.** There is **no API** to change container
  package visibility (REST nor GraphQL) - it is web-UI only. Newly created
  org packages are private, and Zhiman-BJ's org package policy currently
  locks members to Private (the UI shows "Setting is disabled by
  organization administrators"). To activate the pull channel: an **org
  owner** allows public packages under org Settings → Packages, then a
  member flips each package public in its settings page. The workflow's
  GHCR check (and the `image` verification command) turns green on the
  next re-run; nothing else changes.
- **Org package creation rights.** If the push itself fails with 403, the org
  restricts package creation; allow this repository to push to GHCR in the org
  packages settings.
- **`PACK_BUMP_PAT`.** The lock-update PR step needs this repository secret
  (same PAT used by `harness-pin-bump.yml`). Without it the updated lock is
  still attached to the release asset list, but no PR opens — apply the same
  JSON to `provenance/runtime-distribution.json` by hand.

## Acceptance (what a consumer verifies)

```sh
# Primary: Release asset bytes, plain HTTPS + sha256 (works today)
python3 scripts/verify-release-artifact.py asset \
  'https://github.com/Zhiman-BJ/industrial-domain-packs/releases/download/<tag>/<archive>.tar' \
  --sha256 <64-hex>

# Registry bytes: full anonymous manifest + blob verification
# (401/403 until the GHCR packages are public - see first-release notes)
python3 scripts/verify-release-artifact.py image \
  'https://ghcr.io/v2/zhiman-bj/industrial-pcb-runtime/manifests/<digest>'
```

Both commands re-download every byte and fail on any mismatch; use the
identity values from `provenance/runtime-distribution.json`. Air-gapped
install of a verified tarball: `sha256sum -c <archive>.tar.sha256 && docker load -i <archive>.tar`.

## Scope and boundaries

- Only the three images above are released today. The pcb *gateway* runs on
  the host (uv-managed Python), not inside the image; a gateway-baked image is
  future work if a profile needs it.
- macOS runtime assets (KiCad/FreeCAD/Godot DMG & zip) still pin their
  upstream GitHub Release URLs with sha256 in each pack's `harness-pack.json`.
  Mirroring them into this repository's releases is optional redundancy and
  not implemented yet.
- Evaluation task data (PCB-bench tasks) is never distributed through this
  surface; it lives in its own repository with separate licensing.
