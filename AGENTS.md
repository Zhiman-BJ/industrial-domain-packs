# Repository instructions

Read README.md, docs/architecture.md and docs/migration.md before changing module boundaries.

- This is the maintained public source of Domain Packs. Harness and remote-service consumers pin release identities rather than hand-editing source copies.
- Domain-specific ToolDescriptors, execution logic, Skills and Verifiers belong in packs/. Canonical Artifact, Action, Run, State, Verification and Checkpoint contracts remain owned by industrial-agent-harness; do not introduce competing engineering facts.
- Keep the existing Agent kernel and scoped Runtime boundary. Raw MCP disclosure does not authorize protected industrial mutations.
- Local and remote execution share domain semantics. Backend adapters own process/container lifecycle and actual resource limits. A remote workload must not gain a Docker socket, Kubernetes credentials or arbitrary host mounts.
- Record exact source, dependencies, platform, profile and image identities. Do not replace an immutable release or interpret an old run with a new Verifier.
- The shared rtl-cpu entry runs inside an allocated sandbox; preserve exact profile/content identities. Additional domains are source migrations, not qualified remote profiles. Mark exercised platforms accurately.
- Imported source retains licenses and provenance. Update provenance when ownership or distribution boundaries change. Keep the original bootstrap hash record as historical evidence.
- Never commit customer inputs, generated engineering runs, credentials, private deployment endpoints, PDK archives, local Python environments or unreviewed binaries.
- Contract fixtures are not evidence of engineering acceptance. Qualify native success, real failure, cancellation and recovery before declaring an execution profile supported.
- Keep English and Chinese READMEs aligned. Run relevant tests and the real stdio MCP smoke test for domain changes.
