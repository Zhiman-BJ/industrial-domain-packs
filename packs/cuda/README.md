# CUDA Compiler and Evaluator MCP Pack

This Pack supplies two MCP Servers and one CUDA optimization Skill. The CPU
Compiler issues immutable project-bound tickets. The GPU Evaluator consumes
those tickets, verifies correctness and profiles against the protected reference.
Both expose Streamable HTTP endpoints (`/compiler/mcp`, `/evaluator/mcp`) with
separate scoped credentials; each also has a stdio entry. Kimi remains the
Harness agent kernel. Native CUDA-Agent-Harbor code is a private external
resource pinned in `locks/upstream.json`.

The first fixed task is AXPBY on Linux amd64 / RTX 4090 SM 8.9 with driver
595.71.05. This release is an explicitly configured developer integration, not
a general GPU service or an automatically installed desktop bundle. `pack.json`
records qualification separately from source delivery.

## Client

Configure both `INDUSTRIAL_HARNESS_CUDA_COMPILER_MCP_{URL,TOKEN,IDENTITY}` and
`INDUSTRIAL_HARNESS_CUDA_EVALUATOR_MCP_{URL,TOKEN,IDENTITY}` in trusted host settings.
IDENTITY is SHA-256 of the exact JSON identity printed by the reviewed deployment,
including its server role and Pack source digest. Use HTTPS or an authenticated loopback SSH tunnel.
Do not expose credentials to the model, source workspace or generated evidence.
The bound Project's `model.py` must match the deployment's task hash.

Every canonical tool accepts `inputs: {}`. The Runtime packages only
`model_new.py` and supported source files in `kernels/`. Compilation stores a
local protected ticket. GPU evaluation rejects tickets for changed source.
The local aggregate Verifier consumes the exact remote receipt collected in
Harness's canonical artifact store. Compilation, correctness, valid measurement
and the 5% target are separate facts; a process/MCP success cannot stand in for
verification. Broker scope and ordinary Runtime approval remain required.

## Administrator deployment

Provision an exact upstream AXPBY image and GPU-free client/CPU compiler plus an
isolated GPU evaluator. Workers have no Docker socket, host bind mounts or network
access. The host-side backend owns Docker lifecycle and real resource limits.
`server/backend.cjs` checks source hashes, protected files, hardware identity and
worker isolation. Project-supplied commands, images, mounts or GPU IDs are never
accepted by an MCP tool.

Before local preparation, run `node packs/cuda/scripts/preflight.cjs` to inspect
GPU model, UUID, VRAM, driver and compute capability. Multiple compatible GPUs
require an explicit `--gpu GPU-UUID` selection. `allocate.cjs` repeats this check
before reading recipes, writing allocation files or starting workers, and binds
both Evaluator visibility and its single device reservation to that UUID. The fixed
profile accepts RTX 4090 / SM 8.9 / driver 595.71.05 on Linux x64; detection alone
is not native qualification. A different GPU or driver needs a reviewed profile.
Do not silently upgrade drivers or downgrade to CPU execution. Remote clients
use `--remote-client` and require no local NVIDIA GPU.

The installation UI should distinguish local service preparation from connection
to a remote service, show the detected devices and actionable mismatch status,
and bind the selected evaluator by stable UUID. The automatic local installer/UI
is not yet supplied by this developer release.

Create a private configuration with `projectId`, `imageId`, `gpuUuid`, `taskSha256`,
`allocation`, absolute `upstreamRoot`, absolute `stateDirectory`, absolute Docker
executable (an administrator's sudo wrapper is supported) and optional loopback
`port`. `scripts/allocate.cjs INPUT OUTPUT` verifies the locked upstream recipe
and allocates a fresh uniquely named workspace. Never reuse another deployment's
allocation. The produced configuration contains immutable container IDs and the
resolved Compose file/project directory; the reviewed recipe's relative security
profiles continue to resolve against the original upstream environment directory.

Run `node packs/cuda/server/mcp.cjs /absolute/config.json --identity` to verify and
print identities. Run without `--identity` with separate
`CUDA_COMPILER_MCP_TOKEN` / `CUDA_EVALUATOR_MCP_TOKEN` environment secrets to serve
both endpoints. Bind only loopback; expose through authenticated TLS/tunneling.
For stdio, append `--stdio compiler` or `--stdio evaluator`; stdin is reserved for
MCP, and diagnostics use stderr. Concurrent stdio processes share the allocation
lock and protected request store.

The service is bound to one Project. Request IDs are durable and idempotent:
completed IDs return original receipts; changed input under the same ID fails.
Interrupted/failed IDs are not retried. Cancellation stops and verifies cleanup
of this allocation, then quarantines it. An operator starts the service with
`--recover` to restart the same image, close interrupted requests and permit new
Actions. The client waits for durable cleanup status and saves cancellation evidence before closing the MCP session. Native ticket/evidence receipts survive service restart; evaluator
transient state is recreated and reference profiling runs again when needed.
Keep the private state directory bounded and outside writable candidate inputs.

## Validation

Run source suites and real MCP transport checks. Before claiming a native profile
supported, exercise reference profiling, compilation, correct/wrong candidates,
exclusive measurement, stale ticket/version rejection, cancellation with cleanup,
and restart recovery on the exact allocated image. Application acceptance also
requires a Harness consumer pinned to the new Pack commit. It is recorded
separately from the standalone remote MCP checks.

See `PROVENANCE.md` and `skills/cuda-kernel-optimize/SKILL.md`.

The exercised fixed profile is recorded in [QUALIFICATION.md](QUALIFICATION.md). The readiness tool reports worker health; it does not grant qualification to an unreviewed deployment.
