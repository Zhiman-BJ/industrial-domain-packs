# GODOT Domain Runtime Pack

The public profile is macOS Apple Silicon only, exercised with KiCad 10.0.6 and Godot 4.7.2 official stable. Windows, Linux, Intel macOS and remote execution remain unqualified. Native executables are external dependencies, not bundled Pack resources. The dependency preparation scripts verify exact official archive SHA-256 values without requiring administrator rights, Apple Developer credentials or installer publication.

Harness injects its generic trusted `runtimeApi` (`executeTask`, `runtimeFiles`) into the installed plugin factory. The Pack does not resolve Core through a development checkout. Harness owns canonical Run, Action, Artifact, State, Verification and Checkpoint persistence, scoped authorization, protected execution and cancellation. The owner Pack owns source observation, typed edits, native command preparation, report interpretation and versioned Verifiers. Kimi Code stays pinned at 2.1.1 and retains its native loop, sessions, compaction and subtasks.

Engineering acceptance requires explicit task expectations plus native reports; model text, preview output, mocks and exit status cannot establish it. Source edits return `not_run` and invalidate previous acceptance. Every source file and the installed runtime/Verifier bytes contribute to the StateProvider identity; input or verifier changes make historical successful evidence stale. Artifacts and their hashes remain attributable to their original action/input snapshots. Native timeout, cancellation, malformed inputs or concurrent input changes preserve canonical failures with incomplete evidence.

## Godot: structural scene task

`godot.scene.edit` performs hash-checked text-scene edits limited to node position, rotation_degrees, scale, visibility and existing BoxMesh size. Values are finite typed literals; arbitrary property expressions or script assignment are rejected. Before/after scene artifacts are retained. The `.godot` cache is excluded from source observation and protected against task writes.

`godot.scene.verify` first performs native offline headless import, then runs an independent scene property readback and an exact 1..180 frame readback using the maintained native GDScript. It requires nonempty explicit node/property expectations. `godot.scene.requirements.v1` compares all supplied values against both resolved readback and initial/final frame states, including native engine version 4.7.2 official stable. This checks supplied structural properties and script import/frame execution, not unrestricted gameplay, graphics, audio or network behavior.

The self-authored `examples/structural` uses a Node3D, BoxMesh and referenced GDScript. The task changes dimensions/transforms repeatedly, detects a wrong expectation and a real GDScript parse error, then repairs and verifies. Native logs remain complete. The offline macOS sandbox emits one exact Godot 4.7.2 TLS CA bootstrap diagnostic at `get_system_ca_certificates (platform/macos/os_macos.mm:1035)`; that single known unrelated diagnostic is retained but is nonfatal. Other ERROR/SCRIPT ERROR/Parse Error messages remain failures, with a regression test for the exception. The runtime uses the Dummy text driver; existing macOS font directory is permitted read-only for engine initialization. This does not qualify TLS, font rendering or graphics.

## Preparing native dependencies

From the owner package (or an installed Pack's `domain-packs/<domain>` directory):

```sh
node packs/pcb/runtime/setup-native.cjs /absolute/new/kicad-runtime
node packs/godot/runtime/setup-native.cjs /absolute/new/godot-runtime
```

Each prints environment variables and saves provenance. Set `INDUSTRIAL_HARNESS_KICAD_CLI`, `INDUSTRIAL_HARNESS_KICAD_PYTHON` and `INDUSTRIAL_HARNESS_GODOT_CMD` accordingly. Default paths are `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`, the same bundle's Python 3.9 executable, and `/Applications/Godot.app/Contents/MacOS/Godot`. GUI installation is optional; Pack installation alone does not install these dependencies or prove engineering acceptance. The native backend grants read-only access to the explicit dependency bundles, maintained scripts and public SSL configuration. It rejects dependency roots that would expose the original project or an enclosing directory.

Projects are bounded to 128 MiB, 16 MiB per input/output, 10,000 scan entries and depth 24. Symlinks, hardlinks, escaping paths, stale/cross-project scopes and undeclared tools fail closed. The reserved top-level `runtime-identity` input namespace cannot be shadowed. Native actions use a 1..120000 ms timeout per phase (default 60000); multi-phase verification may take several phase budgets. Input snapshots are immutable, writable native caches/output stay under the action run directory, and network access is denied.

The frozen `legacy-harness-pack.json` and bridge/src files remain standalone transport diagnostic references. They are not disclosed or loaded by the integrated host. Native engineering acceptance is tested through the canonical Runtime.
