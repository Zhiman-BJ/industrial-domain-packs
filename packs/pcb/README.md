# PCB Domain Runtime Pack

The public profile is macOS Apple Silicon only, exercised with KiCad 10.0.6 and Godot 4.7.2 official stable. Windows, Linux, Intel macOS and remote execution remain unqualified. Native executables are external dependencies, not bundled Pack resources. The dependency preparation scripts verify exact official archive SHA-256 values without requiring administrator rights, Apple Developer credentials or installer publication.

Harness injects its generic trusted `runtimeApi` (`executeTask`, `runtimeFiles`) into the installed plugin factory. The Pack does not resolve Core through a development checkout. Harness owns canonical Run, Action, Artifact, State, Verification and Checkpoint persistence, scoped authorization, protected execution and cancellation. The owner Pack owns source observation, typed edits, native command preparation, report interpretation and versioned Verifiers. Kimi Code stays pinned at 2.1.1 and retains its native loop, sessions, compaction and subtasks.

Engineering acceptance requires explicit task expectations plus native reports; model text, preview output, mocks and exit status cannot establish it. Source edits return `not_run` and invalidate previous acceptance. Every source file and the installed runtime/Verifier bytes contribute to the StateProvider identity; input or verifier changes make historical successful evidence stale. Artifacts and their hashes remain attributable to their original action/input snapshots. Native timeout, cancellation, malformed inputs or concurrent input changes preserve canonical failures with incomplete evidence.

## PCB: rectangular mounting-board layout

The first public task is a real KiCad rectangular board with existing mounting footprints. `pcb.kicad.edit` changes one rectangular Edge.Cuts outline and/or moves unique existing footprint references. It uses native pcbnew on a protected snapshot, then replaces source only after the expected SHA-256 and current input set still match. Arbitrary scripts, expressions, new footprint libraries and routing are outside the typed action.

`pcb.kicad.verify` runs `kicad-cli pcb drc --format json --severity-all`, then a separate native pcbnew process reloads the immutable input board. The independent `pcb.kicad.requirements.v1` checks version 10.0.6, zero DRC errors, zero unconnected items, explicit board dimensions and requested placements/rotations (0.001 mm tolerance). Optional `maxWarnings` binds an explicit warning limit. Full warnings, ignored checks, native bounds and centerline edge geometry are preserved; the latter avoids silently counting Edge.Cuts stroke width as board dimensions. No schematic parity/ERC, electrical behavior, autorouting or manufacturing signoff is claimed.

The self-authored `examples/mounting-board` has two mounting holes and a 40×30 mm rectangular outline. Its unattached `Harness` footprint library produces two recorded library warnings; the task explicitly permits at most two. Co-locating the holes creates real `holes_co_located`/silk overlap warnings and fails that bound. Repair restores independent acceptance. Native reports use project rules/defaults, never ambient user KiCad preferences; denied personal-config reads stay in the log. The private PCB-bench actor implementation is neither used nor redistributed by the native profile; the pcb-design-e2e Skill documents are vendored in-pack (see PROVENANCE, 2026-10-10).

## PCB-bench tool surface (pcb.bench.*, declared)

The pack declares the upstream PCB-bench model surface for parity: 89 `pcb.bench.*` tools (88 canonical + `run_python`) grouped into seven stage capabilities (`core`, `components`, `schematic`, `pcb`, `verification`, `delivery`, `operate`) and the complete upstream `pcb-design-e2e` Skill (SKILL.md + nine references + one asset) shipped under `skills/pcb-design-e2e/`. The bounded native profile above keeps its own guidance in the `pcb-kicad-native` Skill.

The pinned upstream identity lives in `runtime/bench-upstream.json`: source commit, 112 file SHA-256s, tool-schema digest, container image id and KiCad package versions. In this first step the 89 tools are registered with placeholder execution: calling them throws a setup error that names the prerequisites (authorized PCB-bench checkout in `INDUSTRIAL_HARNESS_PCB_BENCH_DIR`, Docker with the pinned image, gateway Python via `uv sync --frozen --no-dev`). Container dispatch through the maintained bridge is wired in the follow-up change; tool schemas stay fingerprint-verified against the live service and are never copied into this pack.

## Preparing native dependencies

From the owner package (or an installed Pack's `domain-packs/<domain>` directory):

```sh
node packs/pcb/runtime/setup-native.cjs /absolute/new/kicad-runtime
node packs/godot/runtime/setup-native.cjs /absolute/new/godot-runtime
```

Each prints environment variables and saves provenance. Set `INDUSTRIAL_HARNESS_KICAD_CLI`, `INDUSTRIAL_HARNESS_KICAD_PYTHON` and `INDUSTRIAL_HARNESS_GODOT_CMD` accordingly. Default paths are `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`, the same bundle's Python 3.9 executable, and `/Applications/Godot.app/Contents/MacOS/Godot`. GUI installation is optional; Pack installation alone does not install these dependencies or prove engineering acceptance. The native backend grants read-only access to the explicit dependency bundles, maintained scripts and public SSL configuration. It rejects dependency roots that would expose the original project or an enclosing directory.

Projects are bounded to 128 MiB, 16 MiB per input/output, 10,000 scan entries and depth 24. Symlinks, hardlinks, escaping paths, stale/cross-project scopes and undeclared tools fail closed. The reserved top-level `runtime-identity` input namespace cannot be shadowed. Native actions use a 1..120000 ms timeout per phase (default 60000); multi-phase verification may take several phase budgets. Input snapshots are immutable, writable native caches/output stay under the action run directory, and network access is denied.

The frozen `legacy-harness-pack.json` and bridge/src files remain standalone transport diagnostic references. They are not disclosed or loaded by the integrated host. Native engineering acceptance is tested through the canonical Runtime.
