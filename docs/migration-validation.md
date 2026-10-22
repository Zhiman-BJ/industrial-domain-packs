# Multi-domain migration validation

2026-10-04. Tested implementation: `422a8e3d08696905cfc248e1a4b199c28f8e5ec0`. Documentation-only updates do not change the runtime content identity.

| Gate | Result |
| --- | --- |
| Migrated public sources | Chip, Godot, public PCB bridges, committed FreeCAD PR29 and CAD guidance; 114 imported file records |
| JavaScript source checks | 5 passed: catalog boundaries, real Godot source inspection/path restrictions, FreeCAD bounded recipe rejection, shared Verifier, changed-source rejection |
| Imported Python tests, macOS arm64 Python 3.13 | 29 passed, 6 skipped; skipped native tests are not new qualification evidence |
| Real Chip stdio MCP | 25 tools discovered and get_server_info called |
| Committed source archives | All hashes verified: Chip 98, Godot 15, PCB 17, FreeCAD 13, CAD 13 files (including shared source metadata) |
| Clean remote HTTPS dependency install | Exact Git commit archive and npm integrity lock; 12 control-plane tests passed |
| Real Docker integration | 4 passed: prior image rejection, native success/failure/concurrency/cancel/reconnect, timeout, native-created output symlink |
| Host validate.sh protocol | Help 0; no arguments/unknown option 64; missing input 66; invalid RTL nonzero with failed result.json |
| Concurrent validate.sh | Two isolated tasks passed with independent output directories containing spaces; 30.6 seconds each under amd64 emulation |

The shared `rtl-cpu` image uses Verilator 5.026 from the same official pinned image as the inherited full EDA recipe. Its real build smoke and external tests compile an assertion-enabled counter testbench, require `$finish` / EDA_SIMULATION_FINISHED, native PASS and readable VCD structure. Functional coverage remains testbench-defined.

Tested runtime content identity: `1ec54341dc881d4f39946c3b09eac0f0f5721ef80a4c34e48cfb551bcdf1a677`.

Local tested image: `industrial-domain-rtl:0.2.0`, linux/amd64, image identity `sha256:ba6a3ccb431d95e01e29a0e363b8a8b4f077f7d2d08cc6f6c6e23277fb2e202b`, approximately 255 MB. No binary registry publication is part of this migration. Import and deployment identities are recorded by the private consumer.

Tests preserve the fixed two-slot CPU trial. Per sandbox: 2 CPU / 2 GiB, no network, read-only root/input, dropped capabilities and bounded work/output. Peak cgroup memory is not measured by this local source suite; Verilator reports allocation in tool logs. The conservative two-slot limit is retained.

Godot/PCB/FreeCAD remote native execution is not qualified. PCB actor source and native image remain external. FreeCAD preserves the committed public macOS arm64 implementation; migration does not qualify Linux or Windows. Existing local Harness installations still require their separate consumer migration.

## Private consumer deployment qualification

The existing Linux amd64 single-node CPU trial was upgraded to the pinned source, after draining the single coordinator and saving a consistent server-side backup. Its namespace, two sandboxes, quotas, persistent data and prior images were retained. No public ingress or registry publication was added.

CLI qualification passed 7 real native jobs: demo completion, two concurrent jobs plus FIFO, assertion/compile failure, cancellation and artifact download. Streamable HTTP MCP discovery/reconnect passed with four scoped service tools; the CLI environment reported no Python or Verilator. Jobs persisted the expected Pack identity, rtl-cpu profile, 0.6.1-core.2 tool and actual image digest.

A targeted repeat of an existing synthetic project also passed. Sampled worker cgroup memory high was 291.1 MiB; memory.max was 2147483648 and cpu.max was 200000 / 100000, confirming the 2 GiB / 2 CPU limits. This is a sampled high, not a kernel-recorded absolute peak. Prior server identity, canonical State and an old waveform download/hash remained valid after the upgrade. Private deployment endpoints, credentials and receipts remain in the private consumer.


## Runtime packaging and build-input validation — 2026-10-22

Scope: DPR-02, implementation commits `ca19be7` and `ccb7218`, reviewed in
[PR #55](https://github.com/Zhiman-BJ/industrial-domain-packs/pull/55).
Host: macOS arm64, Node 24, Python 3.13; native Chip execution: Linux AMD64
under Docker emulation with Verilator 5.026. No Service deployment or cloud Tool
publication was performed.

- JavaScript: 136 passed, 2 skipped; architecture contract: 11 passed; content
  lock validation passed. Python: 24 passed, 12 skipped. Skips are not native
  qualification evidence. Real stdio MCP discovery and call passed (25 tools).
- All three remote recipes produce minimal contexts excluding publication locks
  and the full Pack content lock. Tests cover source/shared-worker changes,
  platform/build arguments, unsafe/unsupported inputs and immutable image reuse
  failures, including actual config-platform mismatch and corrupt registry bytes.
- Chip's prepared context imports and prepares RTL in an isolated interpreter
  without the full source tree. Its complete Linux AMD64 image build passed,
  including the recipe's real native smoke. Build-input fingerprint:
  `sha256:6d4ce757539bba49f7b4b85b1ffed5198ce1a876213c028bbedab0b22ba8fb22`.
- The repository `validate.sh` entry passed the public counter example and
  returned failure for a copied example with an intentionally wrong assertion.
  Both ran in isolated containers with no network, read-only input/root,
  dropped capabilities and 2 CPU / 2 GiB limits. The successful report contained
  native PASS, the completion marker and a genuine VCD; the failure contained
  Verilator's assertion failure. Cancellation/recovery/cloud gates were not
  rerun and are not newly qualified by these checks.
- The newly built Chip image starts the shared HTTP worker. Wrong runtime
  identity returns `400 RUNTIME_DIGEST_MISMATCH`; a mismatched input snapshot
  returns `422 SNAPSHOT_MISMATCH` before native execution.

The initial full build exposed unavailable existing APT pins. The owner approved
`ca-certificates=20260816ubuntu0.24.04.1` for all three remote recipes and
`git=1:2.43.0-1ubuntu7.4` for PCB. All three complete Linux AMD64 builds passed.
FreeCAD's build verified the original AppImage SHA-256 and FreeCAD 1.1.4 version;
PCB's build verified the locked KiCad artifacts, CLI version 10.0.6 and Python
`pcbnew` version 10.0.6. FreeCAD's official download was cached locally after full
hash verification to overcome slow downloads; the product recipe and its hash
check were unchanged. These build checks are not complete CAD/PCB task,
cancellation/recovery or cloud qualification.

| Profile | Build-input digest | Local Docker image ID |
| --- | --- | --- |
| Chip rtl-cpu | `sha256:6d4ce757539bba49f7b4b85b1ffed5198ce1a876213c028bbedab0b22ba8fb22` | `sha256:e50a7b5e41b98bbed4d3b68e9d65a8465eca9621a78173530f9dda2010135eff` |
| FreeCAD freecad-linux-cpu | `sha256:e2c997f76d0f02736083986deb544176e57a0ff0fac1f44028226938a28562cd` | `sha256:7ef02eaba847b293ac3473f220e7561f28aba685510d961ab0adb3984aeb0ac8` |
| PCB pcb-verify-cpu | `sha256:df85e59ed88f76b959e7a45c0394dac096060699dc79e96cbe1da7fc0732c659` | `sha256:c056684d6a97dfe892b116afd95de29d046302df7c8f9218f5746c838d76830b` |

Each built context's fingerprint was compared with the final source checkout and
matched. These are local build identities, not published registry/Tool bindings.

## 2026-10-22 — DPR-03 Tencent publisher, local validation

The explicit `Publish remote runtime` Action builds/verifies immutable remote
images, persists an intent before AGS creation, reconciles timeouts, checks
ACTIVE/configuration readback and writes generated release records plus the final
content lock to the unchanged source branch. It does not deploy the Service or
delete Tools. Configuration and publication-only gates are documented in
`release-format.md`; the existing GHCR release channel remains separate.

On macOS arm64 / Node 24.21.0, the complete JavaScript suite passed 160 tests with
2 existing skips; the separate architecture suite passed 11 tests. The new tests
exercise stable/environment-scoped identities, persistence failure, ambiguous
creation, pagination, readback mismatch/null normalization and real local-Git
writeback/source-advancement races. Initial sandboxed network-listener tests hit
EPERM and passed when rerun with loopback-listener permission. Actionlint 1.7.7
checks the workflow. Runtime recipes and native execution code are unchanged;
DPR-02 build evidence remains applicable, not a new cloud qualification.

No GitHub/Tencent publication has been run: test/prod Environment configuration is still absent. Shared Tencent
SecretId/SecretKey were subsequently configured at repository scope with explicit
owner approval; Environment creation was rejected by GitHub because the current
account lacks repository administrator rights. A read-only query verified all six
existing Tools are ACTIVE and retains their distinct resource/timeout settings;
this does not qualify a new publication. Registry upload, AGS API defaults,
CAM permissions and protected-branch bot writeback still require real Action
validation. The generated metadata must not be interpreted as native domain
qualification. Dependency audit also reports the existing MCP SDK/Ajv advisories
and a publisher-only transitive uuid advisory (v3/v5/v6 buffer APIs); the Tencent
SDK path uses uuid.v4 without a supplied buffer. No dependency overrides or
unrelated runtime upgrades were applied in this task.


Independent review identified a rerun gap: rebuilding after an uncertain Tool
creation could change the image digest and hence its idempotency identity. The
fix restores and validates the previous intent across runs/attempts before any
build; missing/expired evidence after a cloud attempt fails closed. Regression
tests cover a hypothetical different rebuild digest and expired/missing intent,
in addition to the existing timeout test. Cloud error-code diagnostics remain a
minor deferred finding; real default materialization, Environment protections,
native qualification and existing dependency advisories remain explicit external
or separately scoped limits, not claims made by this local validation.
