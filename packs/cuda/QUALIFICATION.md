# CUDA qualification — 2026-10-08

This record covers the first AXPBY allocation on Linux amd64 / RTX 4090 SM 8.9,
driver 595.71.05. Source commit and installed runtime are separate identities:
reviewed private upstream commit `6e94914b5c857e69755c2362f1745d6599426009`;
immutable image `sha256:f40cdcaaa64645e95e7896eedbcb1b8e13c2fcb4830a8000ff37fed44dfdf1be`.
The exact prepared wrapper/protected-file inventory is pinned in
`locks/rtx4090-image.json`. Core worker modules match the upstream file hashes.
The image label records an earlier build commit; qualification uses the full
immutable image plus installed-file checks, not that label as a source assertion.

## Exercised native paths

- Both distinct MCP identities: authenticated Streamable HTTP initialization,
  tool discovery and readiness; real native stdio initialization/discovery/checks.
- Immutable reference profiling, CPU-only candidate compilation, five independent
  GPU correctness inputs, and exclusive GPU profiling against torch.compile.
- A valid compiled candidate produces passed correctness and measurement evidence;
  the original candidate does not meet the separate 5% speedup target.
- A syntax-error kernel fails compilation. A compiling subtraction kernel fails
  GPU correctness. Neither process/MCP completion upgrades the verdict.
- Changed local source cannot reuse its prior compiler ticket. Tokens, role,
  project, source, image, Verifier and Pack source identity remain bound.
- A real in-flight compilation cancellation stops the exact three owned workers;
  Docker inspection confirms all stopped. Explicit recovery restarts the same
  pinned allocation. Completed request receipts and compiler submissions survive
  service restart; interrupted requests cannot silently become accepted.

## Harness and source gates

The production `createProjectRuntime` / `IndustrialRuntime` and deterministic
Broker at Harness commit `371d2b43b103d929fc2860dc1c27dbfda9cc10e8` exercised the real
paired remote service with the maintained CUDA plugin. Missing approval and
out-of-scope calls failed. Native receipts were collected into canonical artifacts,
verified, and committed as Actions, States and Checkpoints. This developer path
injected the reviewed Pack registration; packaged consumers need the new immutable
release pin before discovering CUDA normally.

The source suite includes real HTTP/stdio MCP transports, role/project/ticket
boundaries, request replay/reuse, cancellation with delayed confirmed cleanup,
source/receipt substitution and contended-measurement rejection. The architecture
contract gate and complete JavaScript source suite pass. Private task files,
credentials, host addresses and full native logs remain in the private qualification
workspace; this public record carries only the exercised scope and pinned hashes.

## Local preparation GPU gate

The added read-only host preflight checks model, stable UUID, VRAM, driver and
compute capability before allocating native workers. The fixed 4090 host was
inspected successfully; unsupported model/driver/SM, missing or ambiguous device
information and explicit multi-GPU selection have source regression coverage.
Real host Compose configuration roundtrips confirmed one selected Evaluator GPU
reservation, GPU-free CPU workers and preserved isolation/security-profile paths.
This configuration check started no containers and ran no new GPU benchmark.
Remote-client mode skips local GPU detection. This is an installation admission
check, not additional native GPU or desktop-installer qualification.

## Limits

No H200, other GPU/driver, task corpus, generic GPU coordinator profile, desktop
bundle or real-model optimization trajectory is claimed. Harbor/OpenCode trajectory
validation is a separate protocol and is not synthesized from Harness MCP receipts.
`qualifiedBundlePlatforms: []` keeps CUDA out of automatically advertised desktop
bundles. The qualification allocation is temporary and is removed after checks;
a reviewed administrator deployment must provision and configure the paired service.
