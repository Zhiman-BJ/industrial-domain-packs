# Harness consumer release 0.3.1

Domain ownership is centralized here. Host Runtime declarations, domain capabilities, Skill metadata and source directories are exported with the same content-lock identity as sandbox profiles. Harness consumes an exact Git commit and matching package lock integrity. It may assemble signed distribution caches from these bytes; those caches are not maintenance sources.

The release reconciles committed Harness 2482114 changes using original import commits as merge bases. The exact changed paths and source hashes are recorded in provenance/harness-consumer-reconciliation.json; original imports remain in domain-migration.json. Chip retains the common sandbox/local Verifier while gaining the verified host environment preflight and protected workspace fixes. FreeCAD retains the later bounded edit, repair and cancellation behavior before consumer replacement.

`consumerMetadata()` provides domain labels, availability, capability defaults and Skill references. `hostPacks()` provides canonical Pack IDs with host providers and source locks. `sourceDirectory(packId)` resolves only maintained package sources. `skillResource(id)` resolves a maintained Skill directory. Consumers must preserve per-project enablement policy and canonical Runtime authorization; metadata does not authorize execution.

New distribution versions are Chip 0.6.2, FreeCAD 1.1.4-pack.4, Godot 0.1.1 and PCB 2026.09.28-pack.1. Native software versions are unchanged. PCB actor hashes and image identity identify private external dependencies, not redistributed actor code. Sandbox profile exports remain compatible with existing pinned remote consumers. No additional remote/native platform qualification is asserted by source tests.

The Chip distribution remains 0.6.2, while its host MCP declaration identifies the embedded EDA Python package as 0.6.1. The gateway validates that native identity against its locked environment; a distribution version must not substitute for the tool version. Consumer tests compare the declaration with the maintained Python project metadata.

## Consumer release 0.4.0

PCB 0.1.0-kicad.1 and Godot 0.2.0 replace integrated legacy MCP declarations with typed Runtime tools and explicit native requirements Verifiers. The installed factory now requires the trusted `runtimeApi` supplied by Harness. Consumer dependency pins and integrity must be updated together; older consumers cannot claim this installed professional path. See [profiles and limitations](professional-runtime.md).

## Consumer release 0.4.1

PCB 0.1.0-kicad.2 and Godot 0.2.1 add managed native dependency recipes, and FreeCAD 1.1.4-pack.6 adds its measured installed size. Native tool versions, Runtime code, protected actions, Verifiers and qualified platforms are unchanged. The FreeCAD distribution skips pack.5, which was reserved by a separate result-presentation change; no runtime or presentation behavior from that change is included here.

Harness must support the generic `macos-app-zip` type, `archiveApp` nested bundle source, `environmentExecutables` mappings and `installedSize` display metadata before consuming this release. The PCB recipe supplies both kicad-cli and the exact bundled Python used by pcbnew. The Godot recipe needs no export templates for the qualified structural task. The owner retains dependency and license provenance; downloads, health, repair and cancellation are Harness responsibilities. See [native installation](native-installation.md) for source measurements, external prerequisites and qualification limits. Do not update a public consumer pin without its installation checks.
