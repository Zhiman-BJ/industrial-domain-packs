---
name: cuda-kernel-optimize
description: Optimize a bound CUDA extension with the remote Compiler and Evaluator MCP Servers; preserve trusted inputs and inspect candidate evidence.
---

Use this Skill when the active CUDA Project has both configured MCP Servers and
its reference model matches the fixed remote task. The first profile is AXPBY
on Linux amd64 / RTX 4090 SM 8.9. Server configuration and credentials belong to
the host Runtime. Discover and call tools through the current Broker scope.

## Workflow

1. Check both `cuda.compiler.check` and `cuda.evaluator.check`. Stop execution if
   the source, image, task, GPU or Verifier identity differs. Follow the Runtime
   diagnostic to restore the reviewed service configuration.
2. Before editing, call `cuda.baseline.profile` in its own turn. Inspect the
   immutable reference's kernel breakdown. This establishes a baseline, not
   candidate correctness or optimization success.
3. Edit only `model_new.py` and source files under `kernels/`. Keep `model.py`,
   binding files, `utils/`, `CAPABILITIES.json` and `.cuda/` protected. Read the
   fixed task's public capability contract rather than guessing permitted APIs.
4. Call `cuda.candidate.compile` with `inputs: {}`. The Runtime submits only the
   candidate files and keeps the project-bound compiler ticket. Check the
   native phase and diagnostics. A successful compile has no correctness claim.
5. Call `cuda.candidate.verify` with `inputs: {}` in its own turn. The Evaluator
   accepts the ticket for the exact current source. Repair a failed input or
   policy result before pursuing speed. Changing any candidate source requires
   a fresh compile ticket.
6. Once correctness passes, call `cuda.candidate.profile` with `inputs: {}` in
   its own turn before replacing that source. It verifies again, then measures
   against the fixed torch.compile baseline. Treat contended/unclassified GPU
   measurements as insufficient evidence. `targetMet` means at least 5% faster;
   successful execution or valid measurement alone does not meet this target.
7. Preserve current and best source checkpoints through the bound workspace's
   authorized file tools/Git workflow. Use saved candidate source hashes and
   the canonical Action evidence when comparing or restoring a candidate.
   Upstream Harbor's `/app` Git refs belong to its separate client workspace;
   do not apply its hard-coded reset commands to a Harness Project.
8. Finish with a freshly verified and profiled candidate. State correctness,
   measurement validity and target achievement separately. Explain unresolved
   failures using actual diagnostics and input hashes.

## Implementation contract

Read `CAPABILITIES.json` for the fixed task's complete positive contract.
Preserve the reference constructor and required state. Return extension-derived
results. CUDA files contain raw CUDA; binding files handle permitted tensors,
allocation and the current stream. Place permitted cuBLAS/cuDNN calls in ordinary
C++ files, and retain the task's required library dependence checks. Keep compiled
libraries, object files and Python caches out of `kernels/`.

## Cancellation and recovery

Cancel through Harness. A cancelled native operation quarantines and stops the
owned remote allocation; its evidence must show confirmed cleanup. An operator
recovers the same pinned allocation before new calls. Completed request IDs
replay their stored receipts across an MCP reconnect or service restart. A
cancelled, failed or interrupted Action is never silently retried. Use a new
Action after recovery, and compile changed source again.

The private CUDA-Agent-Harbor implementation supplies native policy, compilation
and evaluation. This Skill and the two MCP interfaces are maintained in the
Domain Pack. Kimi retains its own loop and context. Harbor/OpenCode trajectory
claims and paper-protocol scores are not synthesized from these MCP calls.
