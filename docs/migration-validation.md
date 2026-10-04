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
