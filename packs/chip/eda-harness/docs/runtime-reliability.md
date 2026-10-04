# Runtime fixes: 0.6.1

This patch release addresses the G01 trajectory's resource, container-lifecycle,
CTS callback and formal-model failures. It retains the pinned upstream tool versions;
it is not upstream EDA Harness 0.7.0 and does not claim completion of the original MCU.

## CTS and the pinned IHP export

The original fixed ORFS image reproduces `cts.tcl, 83 child killed: illegal instruction`
on this ARM Mac. Its Kepler executable also receives SIGILL on `--help`, while OpenROAD
reports its version and completes timing repair. Upstream has documented this
[CPU compatibility problem](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/discussions/4115).

The rebuilt image installs `tools/orfs-lec.py` as the ORFS LEC callback. This callback
loads functional Liberty cells and both real netlists into Yosys, normalizes state,
and requires nonempty proven equivalence with no unproven cells. It accepts only the
ORFS two-netlist YAML interface. Other Kepler interfaces are not implemented. The
original Kepler executable remains as `kepler-formal.upstream` for provenance.

The pinned IHP platform also sets `REMOVE_CELLS_FOR_LEC` to `bondpad_70* sg13g2*`,
which removes every functional standard cell from its exported LEC netlists. The
image patch removes that broad `sg13g2*` pattern. Physical-only cells are still
identified by ORFS. Undriven outputs or missing functional models fail the proof;
they cannot become a vacuous PASS. Timing repair and LEC remain enabled.

Existing project copies of the IHP configuration must apply this export correction
too. Rebuild the image; updating Python alone cannot repair its embedded executable
or platform configuration. A project that overrides `KEPLER_FORMAL_EXE` selects its
own checker and does not use this replacement.

## Resources and container ownership

```yaml
runtime:
  kind: docker
  image: eda-harness-tools:cli-fix-20261003
  require_native: false
  resources:
    cpu: 2
    memory_gb: 4
    build_jobs: 1
    pids: 256
    timeout_seconds: 1800
```

Compilation defaults to one worker. Structured Verilator simulation sets explicit
worker counts and C++ output splitting. In the rebuilt image, make/Verilator wrappers
cap even a project script's `-j64` or unbounded jobs to `build_jobs`. CPU quotas alone
do not constrain compiler worker counts. Requests for parallel compilation must fit
the declared CPU allocation and reserve at least 2 GiB per compiler; this reservation
does not guarantee that a large translation unit fits. A compiler crash remains a
failure, with its log preserved.

The image itself also defaults to one build worker and two ORFS threads, so separately
launched containers inherit safe worker defaults. Managed runs override them from the
declared budget. Only managed runs provide daemon admission, run evidence and guaranteed
cleanup; image defaults do not make an external Docker client's timeout a managed run.

For a single user, projects sharing a Docker daemon share a transactional resource
lease. One managed action runs by default. `EDA_MAX_CONCURRENT_ACTIONS` can raise this
limit, but total CPU and RAM requests must still fit actual daemon capacity, including
a 0.5 GiB RAM reserve. Increasing a container's memory flag cannot increase Docker VM
memory. `EDA_RESOURCE_STATE_DIR` selects the shared lease directory; using different
directories separates admission accounting. Local execution has worker environment
limits but does not have Docker memory quotas or daemon-wide admission.

An action owns its named container regardless of the Docker client's exit state.
Every completion, timeout, cancellation and client crash attempts bounded removal.
The runtime captures Docker's OOM/exit state before removing the container. It uses
explicit removal instead of auto-removal to preserve that evidence. Read-only doctor,
capability and inventory probes also clean their named containers in a finally block.

If deletion cannot be confirmed, the run retains `cleanup_pending`, the container
name and its resource lease; the project refuses a new run. Restore the daemon
connection and call MCP `recover_runs` (CLI `recover`) to retry cleanup. A lost MCP
connection alone does not terminate an asynchronously submitted worker. Inspect its
run ID before retrying a mutation.

`report.execution` records argv, image/platform/daemon identity, limits, OOM/exit state
and cleanup results. Diagnostics distinguish SIGILL, compiler failure, formal model
failure and confirmed OOM. `Error 247` alone is never classified as OOM. Execution
failure reports verification UNKNOWN and never creates a reusable successful state.
Set `require_native: true` to reject Docker image/daemon architecture mismatch.

## Explicit equivalence actions

Declare a project action and capture both inputs and actual functional cell models:

```yaml
inputs:
  rtl: [rtl/top.sv]
  netlist: [gate/top.v]
  liberty: [pdk/cells.lib]
workflow:
  actions:
    - id: verify.equivalence
      tool: yosys
      dependencies: []
      input_types: [rtl, netlist, liberty]
      artifact_types: [report.equivalence]
actions:
  verify.equivalence:
    parameters:
      operation: equivalence
      top: top
      gold_sources: [{path: rtl/top.sv}]
      gate_sources: [{path: gate/top.v}]
      cell_liberties: [{path: pdk/cells.lib}]
      engine: eqy
      memory_mode: map
      depth: 10
      partition_timeout_seconds: 60
      solver: z3
required_verification: [verify.equivalence]
```

`cell_models` accepts functional Verilog models; `cell_liberties` accepts functional
Liberty models, without blackbox import. Async flops are normalized and memories are
mapped before SAT. `engine: yosys` provides a direct proof alternative. EQY runs one
worker, disables inserted match buffers, and collects matched nets together to avoid
independent memory cutpoints hiding changed writes. It requires the final complete
proof and rejects partial, failed or empty proofs.

The structured adapter supports mapped memories only. A native-memory trial in the
pinned EQY/SBY toolchain crashed with `$mem_v2` xprop; disabling xprop still left a
correct native-memory/mapped-gate pair unproven. That combination is therefore not
advertised as supported. For very large RAMs, supply a separately verified custom
SMT flow with matching real memory models rather than mapping beyond the budget.
SMT expressions are unrolled before solving, which avoids the pinned Z3's stall
with nested functions. Formal convergence cannot be guaranteed for arbitrary
designs. Inspect inconclusive evidence instead of accepting a partial proof.

## Scope and validation

Use MCP `run_action`/`run_until` for computation, including project-specific scripts.
Raw agent shell commands and separately launched Docker containers bypass managed
runtime ownership, admission and evidence. Updating a CLI archive does not rewrite
an existing project's scripts or replace a prebuilt tool image.

The regression tests exercise real Docker timeout, client SIGKILL, confirmed OOM,
resource reuse, real Yosys memory/reset proofs and deliberately changed designs.
Image acceptance includes valid/invalid RTL, artifact readability and two isolated
validation tasks. The original G01 MCU project/PDK is not present in the supplied
trajectory snapshot, so its full P&R and formal acceptance remain unverified.

The local patch's image identity, source digest and completed validation results are
recorded in [runtime-fixes-validation.json](../tools/runtime-fixes-validation.json).
The source and release archives include matching logs in `tools/runtime-fixes-evidence/`.
The original full local artifact set is retained in the separately delivered review package.

To reproduce the patch regression tests, run `uv run --frozen python -m pytest -q`
from this EDA directory. Native tool tests skip unless Yosys is installed and
`EDA_NATIVE_TEST_IMAGE` / `EDA_EQY_TEST_IMAGE` select an installed validated image.
Run those opt-in cases against the rebuilt image to qualify container lifecycle
and mapped-memory proofs; Python-only CI does not replace that qualification.
