# PCB Domain Runtime Pack

## Container release and local validation

The `pcb-v*` release channel publishes `industrial-pcb-runtime` as a compressed-layer
OCI archive plus SHA-256. Its identity is generated in the repository's
`provenance/runtime-distribution.json`; a compatible Harness installer downloads,
checks and loads it into a running Linux Docker engine (including Docker Desktop
on macOS). Installing a tarball creates an image; a container starts when a tool
or validation task runs. The host gateway Python remains a separate prerequisite.

The recipe keeps KiCad 10.0.6, its symbols, footprints and 3D libraries pinned to
official historical Launchpad builds in `runtime/kicad-packages.json`. Rolling PPA
indexes no longer retain this version. Every downloaded package is checked against
the size and SHA-256 in the official build's `.changes` file. Base images, requested
Ubuntu packages and Freerouting 2.4.1 are also pinned; TLS verification stays enabled.

Build with this pack directory as the context, then validate a real board:

```sh
docker build --platform linux/amd64 -f Dockerfile.local-dev -t industrial-pcb-runtime:local .
./validate.sh examples/mounting-board/board.kicad_pcb --image industrial-pcb-runtime:local
./validate.sh examples/mounting-board/board.kicad_pcb --image industrial-pcb-runtime:local --output /tmp/pcb-result
INDUSTRIAL_PCB_VALIDATION_IMAGE=industrial-pcb-runtime:local node scripts/test-image.cjs
```

Each validation uses one disposable Linux AMD64 container: no network, read-only
input/root filesystem, caller UID/GID, dropped capabilities, 2 CPUs, 2 GiB memory,
128 PIDs, 256 MiB tmpfs and a 120-second timeout. The default output directory is
unique and printed; explicit output must be empty. `result.json` records the exit
status, saved board and optional `workload.log`. KiCad saves the board, a separate
native process reads it back, and DRC must report no errors or unconnected items.
Warnings remain visible in `drc.json`; the supplied mounting-board fixture has two
library warnings. This smoke checks image usability, not complete board engineering
acceptance. The original upstream qualification identity remains unchanged.

The public profile is macOS Apple Silicon only, exercised with KiCad 10.0.6 and Godot 4.7.2 official stable. Windows, Linux, Intel macOS and remote execution remain unqualified. Native executables are external dependencies, not bundled Pack resources. The dependency preparation scripts verify exact official archive SHA-256 values without requiring administrator rights, Apple Developer credentials or installer publication.

Harness injects its generic trusted `runtimeApi` (`executeTask`, `runtimeFiles`) into the installed plugin factory. The Pack does not resolve Core through a development checkout. Harness owns canonical Run, Action, Artifact, State, Verification and Checkpoint persistence, scoped authorization, protected execution and cancellation. The owner Pack owns source observation, typed edits, native command preparation, report interpretation and versioned Verifiers. Kimi Code stays pinned at 2.1.1 and retains its native loop, sessions, compaction and subtasks.

Engineering acceptance requires explicit task expectations plus native reports; model text, preview output, mocks and exit status cannot establish it. Source edits return `not_run` and invalidate previous acceptance. Every source file and the installed runtime/Verifier bytes contribute to the StateProvider identity; input or verifier changes make historical successful evidence stale. Artifacts and their hashes remain attributable to their original action/input snapshots. Native timeout, cancellation, malformed inputs or concurrent input changes preserve canonical failures with incomplete evidence.

## PCB: rectangular mounting-board layout

The first public task is a real KiCad rectangular board with existing mounting footprints. `pcb.kicad.edit` changes one rectangular Edge.Cuts outline and/or moves unique existing footprint references. It uses native pcbnew on a protected snapshot, then replaces source only after the expected SHA-256 and current input set still match. Arbitrary scripts, expressions, new footprint libraries and routing are outside the typed action.

`pcb.kicad.verify` runs `kicad-cli pcb drc --format json --severity-all`, then a separate native pcbnew process reloads the immutable input board. The independent `pcb.kicad.requirements.v1` checks version 10.0.6, zero DRC errors, zero unconnected items, explicit board dimensions and requested placements/rotations (0.001 mm tolerance). Optional `maxWarnings` binds an explicit warning limit. Full warnings, ignored checks, native bounds and centerline edge geometry are preserved; the latter avoids silently counting Edge.Cuts stroke width as board dimensions. No schematic parity/ERC, electrical behavior, autorouting or manufacturing signoff is claimed.

The self-authored `examples/mounting-board` has two mounting holes and a 40×30 mm rectangular outline. Its unattached `Harness` footprint library produces two recorded library warnings; the task explicitly permits at most two. Co-locating the holes creates real `holes_co_located`/silk overlap warnings and fails that bound. Repair restores independent acceptance. Native reports use project rules/defaults, never ambient user KiCad preferences; denied personal-config reads stay in the log. The private upstream actor implementation is neither used nor redistributed by the native profile; the pcb-design-e2e Skill documents are vendored in-pack (see PROVENANCE, 2026-10-10).

## Design tool surface (pcb.design.*, declared)

The pack declares the upstream model surface for parity: 89 `pcb.design.*` tools (88 canonical + `run_python`) grouped into seven stage capabilities (`core`, `components`, `schematic`, `pcb`, `verification`, `delivery`, `operate`) and the complete upstream `pcb-design-e2e` Skill (SKILL.md + nine references + one asset) shipped under `skills/pcb-design-e2e/`. The bounded native profile above keeps its own guidance in the `pcb-kicad-native` Skill.

The pinned upstream identity lives in `runtime/upstream-snapshot.json`: source commit, 112 file SHA-256s, tool-schema digest, container image id and KiCad package versions. `pcb.design.*` calls dispatch through `runtime/gateway.cjs`: one MCP stdio gateway process (`bridge/pcb-gateway.py`) per bound project starts the pinned Linux container, pins the live tool schema digest at startup and enforces the transport policy (64 KiB argument cap, 45 s call timeout, paged responses reassembled up to 256 KiB). Observation payloads ride in the action diagnostics; gateway/native errors fail the action with their detail. The actor sources (pcb-agent/, 101 files) are vendored inside this pack, chip-style: the pack is self-contained and no external checkout is required. Prerequisites are checked before any gateway start: Docker with the pinned image — build it with this pack as the context (`docker build --platform linux/amd64 -f Dockerfile.local-dev -t industrial-pcb-runtime <packDir>`, then set `INDUSTRIAL_HARNESS_PCB_DEV_IMAGE_ID`) — and the gateway Python environment (`uv sync --frozen --no-dev` inside packs/pcb, or `INDUSTRIAL_HARNESS_PCB_GATEWAY_PYTHON`). `INDUSTRIAL_HARNESS_PCB_UPSTREAM_DIR` optionally overrides the vendored resources with a full upstream checkout for evaluation parity. Optional: `INDUSTRIAL_HARNESS_PCB_REQUIREMENTS` binds an immutable public requirements file (it must live outside the writable project) and `INDUSTRIAL_HARNESS_PCB_IMAGE_INPUT=1` delivers `view_design` renders. Idle gateway sessions recycle after 15 minutes.

## Preparing native dependencies

From the owner package (or an installed Pack's `domain-packs/<domain>` directory):

```sh
node packs/pcb/runtime/setup-native.cjs /absolute/new/kicad-runtime
node packs/godot/runtime/setup-native.cjs /absolute/new/godot-runtime
```

Each prints environment variables and saves provenance. Set `INDUSTRIAL_HARNESS_KICAD_CLI`, `INDUSTRIAL_HARNESS_KICAD_PYTHON` and `INDUSTRIAL_HARNESS_GODOT_CMD` accordingly. Default paths are `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`, the same bundle's Python 3.9 executable, and `/Applications/Godot.app/Contents/MacOS/Godot`. GUI installation is optional; Pack installation alone does not install these dependencies or prove engineering acceptance. The native backend grants read-only access to the explicit dependency bundles, maintained scripts and public SSL configuration. It rejects dependency roots that would expose the original project or an enclosing directory.

Projects are bounded to 128 MiB, 16 MiB per input/output, 10,000 scan entries and depth 24. Symlinks, hardlinks, escaping paths, stale/cross-project scopes and undeclared tools fail closed. The reserved top-level `runtime-identity` input namespace cannot be shadowed. Native actions use a 1..120000 ms timeout per phase (default 60000); multi-phase verification may take several phase budgets. Input snapshots are immutable, writable native caches/output stay under the action run directory, and network access is denied.

The frozen `legacy-harness-pack.json` and bridge/src files remain standalone transport diagnostic references. They are not disclosed or loaded by the integrated host. Native engineering acceptance is tested through the canonical Runtime.
