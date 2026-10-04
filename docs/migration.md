# Shared Domain Pack migration

2026-10-04. The user selected a new public Domain Pack repository under Zhiman-BJ. This repository is the destination for shared domain development and Pack publication. The existing private EDA repository remains historical/upstream provenance; consumers are migrated explicitly.

| Step | Delivery | Acceptance |
| --- | --- | --- |
| Source bootstrap | Public Chip code, locks, Skill, Core adapter, provenance and source packaging | Source import hashes match the public baseline; real MCP discovery and imported contract tests pass |
| Shared execution | Extract the native execution interface, common Verifier and `rtl-cpu` image recipe | Same inputs and profile produce consistent local/remote verification; no nested Docker inside a workload |
| Immutable runtime release | Generate trusted metadata and runtime manifest from this repository | Code/dependency/profile identities verified; incomplete qualification cannot be advertised as supported |
| Remote consumer | Replace the trial's separate runner/Verifier/Chip recipe; persist release identity in grants/jobs | Version mismatch rejected before execution; existing concurrency, cancellation and recovery behavior retained |
| Harness consumer | Consume released metadata and local/remote runtime facade | Protected CLI path first, then Desktop; remote mode has no local Domain MCP or native tools |
| Deployment qualification | Import exact images and drain/switch the existing single coordinator | Two-sandbox CPU budget retained; historical results and rollback identity preserved |

Pack publishing moves from Harness's `package-chip.cjs` / `release-chip.yml` to this repository. Harness retains generic contracts and the Core Runtime, and pins Pack releases. Domain-specific capability and Verifier code is maintained here; reviewed consumer artifacts can be cached, but hand-edited source copies must be removed.

The imported full EDA recipe and Core adapter are baseline source, not evidence of a new native or Kubernetes qualification. Current source tests include synthetic contract fixtures. Real native success, assertion failure, compile failure, timeout, cancellation, version rejection and restart recovery must be exercised before releasing the shared RTL profile.

Chip, Godot, public PCB bridges, FreeCAD public PR29 and CAD guidance have been imported. Public import paths and hashes are recorded; private PCB actor code remains an external dependency. The shared Chip profile is consumed by the remote service with an exact Git dependency. Linux native support for other domains remains a separate qualification gate.

Linux sandbox qualification and protected Linux Agent qualification are separate gates. The lightweight CLI connection test on a server does not qualify a full Kimi industrial session. No large-scale concurrency or additional worker pool is introduced by this migration.
