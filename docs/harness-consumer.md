# Harness consumer release 0.3.0

Domain ownership is centralized here. Host Runtime declarations, domain capabilities, Skill metadata and source directories are exported with the same content-lock identity as sandbox profiles. Harness consumes an exact Git commit and matching package lock integrity. It may assemble signed distribution caches from these bytes; those caches are not maintenance sources.

The release reconciles committed Harness 2482114 changes using original import commits as merge bases. The exact changed paths and source hashes are recorded in provenance/harness-consumer-reconciliation.json; original imports remain in domain-migration.json. Chip retains the common sandbox/local Verifier while gaining the verified host environment preflight and protected workspace fixes. FreeCAD retains the later bounded edit, repair and cancellation behavior before consumer replacement.

`consumerMetadata()` provides domain labels, availability, capability defaults and Skill references. `hostPacks()` provides canonical Pack IDs with host providers and source locks. `sourceDirectory(packId)` resolves only maintained package sources. `skillResource(id)` resolves a maintained Skill directory. Consumers must preserve per-project enablement policy and canonical Runtime authorization; metadata does not authorize execution.

New distribution versions are Chip 0.6.2, FreeCAD 1.1.4-pack.4, Godot 0.1.1 and PCB 2026.09.28-pack.1. Native software versions are unchanged. PCB actor hashes and image identity identify private external dependencies, not redistributed actor code. Sandbox profile exports remain compatible with existing pinned remote consumers. No additional remote/native platform qualification is asserted by source tests.
