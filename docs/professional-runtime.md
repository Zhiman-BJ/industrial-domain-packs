# PCB and Godot professional Runtime profiles

The public profile is macOS Apple Silicon only, exercised with KiCad 10.0.6 and Godot 4.7.2 official stable. Windows, Linux, Intel macOS and remote execution remain unqualified. Native executables are external downloads, not bundled Pack resources. Consumer release 0.4.1 declares official downloads, sizes and executable environment mappings for the shared Harness Pack Manager; see [native installation](native-installation.md). The developer preparation scripts remain available without requiring administrator rights, Apple Developer credentials or installer publication.

Harness injects its generic trusted `runtimeApi` (`executeTask`, `runtimeFiles`) into the installed plugin factory. The Pack does not resolve Core through a development checkout. Harness owns canonical Run, Action, Artifact, State, Verification and Checkpoint persistence, scoped authorization, protected execution and cancellation. The owner Pack owns source observation, typed edits, native command preparation, report interpretation and versioned Verifiers. Kimi Code stays pinned at 2.1.1 and retains its native loop, sessions, compaction and subtasks.

Engineering acceptance requires explicit task expectations plus native reports; model text, preview output, mocks and exit status cannot establish it. Source edits return `not_run` and invalidate previous acceptance. Every source file and the installed runtime/Verifier bytes contribute to the StateProvider identity; input or verifier changes make historical successful evidence stale. Artifacts and their hashes remain attributable to their original action/input snapshots. Native timeout, cancellation, malformed inputs or concurrent input changes preserve canonical failures with incomplete evidence.

## PCB: rectangular mounting-board layout

The first public task is a real KiCad rectangular board with existing mounting footprints. `pcb.kicad.edit` changes one rectangular Edge.Cuts outline and/or moves unique existing footprint references. It uses native pcbnew on a protected snapshot, then replaces source only after the expected SHA-256 and current input set still match. Arbitrary scripts, expressions, new footprint libraries and routing are outside the typed action.

`pcb.kicad.verify` runs `kicad-cli pcb drc --format json --severity-all`, then a separate native pcbnew process reloads the immutable input board. The independent `pcb.kicad.requirements.v1` checks version 10.0.6, zero DRC errors, zero unconnected items, explicit board dimensions and requested placements/rotations (0.001 mm tolerance). Optional `maxWarnings` binds an explicit warning limit. Full warnings, ignored checks, native bounds and centerline edge geometry are preserved; the latter avoids silently counting Edge.Cuts stroke width as board dimensions. No schematic parity/ERC, electrical behavior, autorouting or manufacturing signoff is claimed.

The self-authored `examples/mounting-board` has two mounting holes and a 40×30 mm rectangular outline. Its unattached `Harness` footprint library produces two recorded library warnings; the task explicitly permits at most two. Co-locating the holes creates real `holes_co_located`/silk overlap warnings and fails that bound. Repair restores independent acceptance. Native reports use project rules/defaults. Before importing pcbnew, the adapter binds KiCad configuration/documents to the action output directory using the official `KICAD_CONFIG_HOME`/`KICAD_DOCUMENTS_HOME` variables and routes wx diagnostics to stderr. This prevents native error dialogs from hanging process shutdown on a clean macOS login. Personal preferences remain denied; CLI diagnostics are retained. See the [official configuration variables](https://docs.kicad.org/9.0/en/kicad/kicad.html#advanced-environment-variables). The private PCB-bench actor and private Skills are neither used nor redistributed.

## Godot: structural scene task

`godot.scene.edit` performs hash-checked text-scene edits limited to node position, rotation_degrees, scale, visibility and existing BoxMesh size. Values are finite typed literals; arbitrary property expressions or script assignment are rejected. Before/after scene artifacts are retained. The `.godot` cache is excluded from source observation and protected against task writes.

`godot.scene.verify` first performs native offline headless import, then runs an independent scene property readback and an exact 1..180 frame readback using the maintained native GDScript. It requires nonempty explicit node/property expectations. `godot.scene.requirements.v1` compares all supplied values against both resolved readback and initial/final frame states, including native engine version 4.7.2 official stable. This checks supplied structural properties and script import/frame execution, not unrestricted gameplay, graphics, audio or network behavior.

The self-authored `examples/structural` uses a Node3D, BoxMesh and referenced GDScript. The task changes dimensions/transforms repeatedly, detects a wrong expectation and a real GDScript parse error, then repairs and verifies. Native logs remain complete. The offline macOS sandbox emits one exact Godot 4.7.2 TLS CA bootstrap diagnostic at `get_system_ca_certificates (platform/macos/os_macos.mm:1035)`; that single known unrelated diagnostic is retained but is nonfatal. Other ERROR/SCRIPT ERROR/Parse Error messages remain failures, with a regression test for the exception. The runtime uses the Dummy text driver; existing macOS font directory is permitted read-only for engine initialization. This does not qualify TLS, font rendering or graphics.

## Preparing native dependencies

For a compatible Harness with managed dependency support, select the PCB or Godot Pack in the installation screen. The shared installer uses the maintained `runtimeAssets` declaration to download, verify, prepare, probe and supply the executable paths; no user environment configuration is required. Pack preparation is not engineering acceptance.

For standalone developer preparation, from the owner package (or an installed Pack's `domain-packs/<domain>` directory):

```sh
node packs/pcb/runtime/setup-native.cjs /absolute/new/kicad-runtime
node packs/godot/runtime/setup-native.cjs /absolute/new/godot-runtime
```

Each developer script prints environment variables and saves provenance. Set `INDUSTRIAL_HARNESS_KICAD_CLI`, `INDUSTRIAL_HARNESS_KICAD_PYTHON` and `INDUSTRIAL_HARNESS_GODOT_CMD` accordingly only for that standalone path. Default paths are `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`, the same bundle's Python 3.9 executable, and `/Applications/Godot.app/Contents/MacOS/Godot`. GUI installation is optional. Older Harness consumers require this separate preparation. The native backend grants read-only access to the explicit dependency bundles, maintained scripts and public SSL configuration. It rejects dependency roots that would expose the original project or an enclosing directory.

Projects are bounded to 128 MiB, 16 MiB per input/output, 10,000 scan entries and depth 24. Symlinks, hardlinks, escaping paths, stale/cross-project scopes and undeclared tools fail closed. The reserved top-level `runtime-identity` input namespace cannot be shadowed. Native actions use a 1..120000 ms timeout per phase (default 60000); multi-phase verification may take several phase budgets. Input snapshots are immutable, writable native caches/output stay under the action run directory, and network access is denied.
