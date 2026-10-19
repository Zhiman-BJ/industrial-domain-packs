# Pack authoring instructions

The repository-root [AGENTS.md](../AGENTS.md) owns the cross-repository
contract and supersedes this file. This file adds the conventions that apply to
every directory under `packs/`. Pack-specific usage, dependencies and platform
limits live in each pack's README.

## Every pack owns

- `pack.json` and `harness-pack.json` — the manifest pair. Manifest version and
  `sourceFiles` hashes are release identity: regenerate them after changing a
  listed file; never hand-edit a hash.
- `PROVENANCE.md` — imported-source origin, licenses and distribution
  boundaries; update it when ownership or distribution boundaries change.
- `README.md` (plus the Chinese README where one exists) and its own test suite
  (chip: `uv sync --frozen --directory packs/chip/eda-harness` then `pytest -q`;
  pcb: the `uv` environment in `packs/pcb`; others: see the pack README).

## Working rules

- Any source change under `packs/` or `lib/` runs `npm run lock` at the
  repository root in the same commit; CI verifies with `npm run lock:check`.
- `skills/` content is first-class runtime resource, not documentation:
  consumers gate execution layers on skill edits, so treat them as source.
- Packs own domain semantics — typed edits, native command preparation, report
  interpretation, versioned Verifiers. Process/container lifecycle, sandbox
  limits and canonical persistence belong to the consumers' backends. A remote
  workload never gains a Docker socket, Kubernetes credentials or arbitrary
  host mounts.
- Engineering acceptance requires native reports plus explicit task
  expectations; source edits return `not_run` and invalidate prior evidence.
  Never reinterpret an old run with a new Verifier.
- Container recipes pin every external input: base images as
  `name:tag@sha256:<manifest-list digest>` (so `--platform` still resolves),
  apt packages as exact `package=version` including any epoch, and downloaded
  artifacts by sha256. A floating tag or an unpinned `apt-get install` line is
  a reproducibility defect; bump pins deliberately, one lock per change.
- Vendor complete sources with their licenses; a pack must be self-contained —
  no external checkout is required to run it.
