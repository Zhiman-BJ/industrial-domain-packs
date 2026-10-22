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

## 2026-10-22 — resource updates and shared image publication revision

An isolated Guangzhou custom Tool used an existing Chip image from the allowed
namespace, a diagnostic Python HTTP process and token-authenticated access. CPU/
memory changed from 1 core/1 GiB to 2 cores/2 GiB. The old instance retained cgroup
quota 100000/100000 and memory 1073741824; the new instance had 200000/100000 and
2147483648. The old process boot UUID stayed constant and elapsed time increased.
Both instances were observed STOPPED and the test Tool absent after cleanup.

Disk update first exposed propagation lag: Tool readback changed from 1Gi to 5Gi,
but an immediately started instance still reported root capacity 1078984704 bytes.
A second isolated test waited 15 seconds after update: old root/tmp/home/mnt stayed
1078984704; new paths reported 5373952000. Instance API resource readback likewise
showed 1024Mi versus 5120Mi. Old process continuity was preserved. Temporary Tools
were deleted; subsequent API queries confirmed all four disk-test instances
STOPPED and both Tools absent. This proves an observed eventual update, not a bounded
15-second propagation guarantee or a pause/resume guarantee.

Read-only CCR checks found actual per-repository quota 100 tags, with Chip 3,
PCB 5 and FreeCAD 2 tags. No global or per-repository lifecycle strategy existed.
The new capacity helper was exercised against the live Chip repository and
returned 3/100, remaining 97. No image was published or deleted in these tests.
The revised workflow uses repository configuration/secrets rather than the
previous GitHub Environment prerequisite, cloud input-tag reuse, mutable resource
sizing, one batch writeback and per-domain summaries. End-to-end Action publication
is still not qualified by these targeted cloud checks.

Final revision checks: 172 tests, 170 passed and 2 skipped; architecture policy
checks passed; real Chip stdio MCP smoke returned 25 tools with required tools
present; actionlint passed after correcting the final job scratch-directory
context. Independent review covered the complete diff and follow-up fixes:
removed a hardcoded memory test that blocked valid configuration edits and allowed
same-source test-branch intent reuse without relaxing environment/source checks.
The existing pull role was configured as repository variable TENCENT_ROLE_ARN.
Registry username/password Secrets are still absent; the live publication Action
has not been dispatched. Existing native qualification is unchanged.

## 2026-10-22 — publication configuration grouped by environment

Replaced six per-domain JSON files with `publication/test.cjs` and
`publication/prod.cjs`. The files export plain objects with all domains and native
JS comments; no YAML dependency remains. Existing Ajv validates and translates
the objects. The six translated Tencent configurations were compared with the
pre-migration JSON values, including fixed fallback timeouts, and were identical.
The input files of all three actual Docker recipes were checked: none changed.
The package inventory and integrity verifier now cover the two CJS files; ordinary
consumers do not execute them. The consumer test rejects altered configuration
bytes, and configuration tests cover unknown fields, invalid types, missing
domains/profile mismatches and isolation from Node's cached configuration object.
Registry login Secrets were provisioned with owner approval after the previous
record; the full publishing Action has still not been dispatched.

Migration validation: 175 tests, 173 passed and 2 skipped; all 11 architecture
policy tests and content-lock verification passed. Package dry-run includes both
CJS files and no legacy publication JSON. Real Chip stdio MCP smoke returned 25
tools with required tools present. Independent review found no blocking or minor
issues and independently checked configuration equivalence and 14 focused tests.

## 2026-10-22 — automatic prod publication trigger

`Publish remote runtime` now runs on pushes to main, including PR merges.
Feature branches retain manual test publication; manual main dispatch supports
prod retries. The writeback keeps checkout's default GITHUB_TOKEN, so generated
lock commits do not recursively trigger workflows. Actionlint and all 11
architecture policy tests passed. Independent review checked branch/environment
selection, resume, writeback authentication and documentation with no findings.
No live publication was triggered during this change.

## 2026-10-22 — separate check and conditional publication jobs

The Check matrix is read-only. A selection job produces the Publish matrix from
its results; fully reusable packs skip Publish while retaining verified results
for final lock writeback and summary. Publish rechecks cloud state and retains
durable intent handling. Current selection controls artifact collection, so a
stale partial publication artifact from an earlier attempt cannot replace fresh
reuse evidence, and a failed current publication cannot fall back to a check.

Validation: 181 tests, 179 passed and 2 skipped, including four orchestration
checks proving no image build or Tool mutation in Check and propagation of cloud
query errors. Architecture policy, content-lock check and actionlint passed.
Independent review found a mixed-rerun artifact selection issue; it was fixed,
covered by regression tests and reviewed again with no remaining blockers.
No new live workflow was dispatched for this revision; the first automatic prod
run from the prior merged revision was still in progress during these checks.

## 2026-10-22 — local image preparation and lightweight Tool publication

Moved image builds, pushes and complete blob verification to `npm run publish:images`.
The local command requires all four Tencent credentials before invoking tools,
uses shared image inputs without an environment selector, and promotes the input
tag only after full verification. It does not write release locks or create Tools.
Actions verify manifest/config metadata and publish Tools, then retain the single
batch release/content-lock writeback. Missing prepared images fail Check.

Validation: 188 Node tests, 186 passed and 2 skipped; 9 Python verifier tests
passed. Architecture policy, content-lock verification and actionlint passed.
Independent review found a repeated-build context collision; fresh attempt
directories and a regression test resolved it, and follow-up review passed.
Real local Buildx push-by-digest/tag promotion and the revised Action have not
yet been qualified end to end.

The old prod and feature publication runs were cancelled after prolonged layer
upload stalls. Bounded registry probes showed fast authentication but slow or
timed-out bulk uploads from GitHub runners; equivalent local uploads completed.
The publication workflow remains manually disabled until this revision merges,
so old main cannot restart heavy uploads. Existing GitHub Secrets/Variables all
have active consumers and were retained. Native runtime qualification is unchanged.

## 2026-10-22 — PR image readiness check

Added `Runtime images ready` for all pull requests targeting main, checking the
candidate merge commit with the same image-input fingerprints as local preparation.
The gate performs read-only registry metadata verification and fails on absent
images, query errors or identity mismatch. Six focused tests cover these outcomes;
the complete Node suite passed 192 tests with 2 skipped (194 total). Architecture
policy, content-lock check and actionlint passed. No native runtime code changed.
Repository API permissions report write access without administration access;
required-check enforcement on main remains pending administrator configuration.
Independent review of the incremental gate found no blocking issues and reran all
six readiness tests successfully. Live GitHub/registry execution is checked
separately from these local fixtures.

## 2026-10-22 — local publication tag propagation

The live Chip build completed, pushed immutable digest
`sha256:ef6531dc6b7e3fcb7bdd0f04a05d197b73ac4765e3a6f653c8838140a6189d7a`,
passed full blob verification and promoted its input tag. Immediate TCR listing
still reported absence, while a subsequent read returned the matching digest.
Added owner-approved readback polling for absent tags, bounded to 60 seconds;
different digests and query errors still fail immediately. Six regression cases
passed, including cancellation of in-flight queries and stopping pagination after
the deadline. The full Node suite passed 198 tests with 2 skipped (200 total).
Content-lock and diff checks passed. FreeCAD/PCB publication remains in progress.

## 2026-10-22 — OCI export and chunked local upload

FreeCAD built successfully with its pinned AppImage, but Buildx direct large-layer
uploads encountered broken pipe/closed connection errors. That attempt was
cancelled without publishing an input tag. Owner approved using regctl to upload
the local OCI layout with 8 MiB chunks; full verification and tag promotion remain
mandatory. An isolated two-chunk CCR probe returned 202 for both chunks and 201
on completion; its readback was not qualified because the probe rejected the
registry redirect. It created no image manifest, tag or Tool.

Official regctl v0.11.7 darwin/arm64 binary SHA-256 matched the release asset.
Real FreeCAD OCI export and regctl local digest resolution matched
`sha256:da7d8d5d1593ea46fdd55807d4bba52db2c0348496b46e89416376f97e65a5ce`.
The full Node suite passed 199 tests with 2 skipped (201 total); content-lock
and diff checks passed. Independent incremental review passed, including an
upload-failure test that prevents tag promotion. Large-layer regctl upload and
subsequent full verification are still being exercised; no new native runtime
qualification is claimed.

## 2026-10-22 — full image verification time bound

The FreeCAD regctl digest upload completed, but the subsequent full readback
terminated at the previous 20-minute bound without publishing the input tag.
Owner approved a 90-minute full-verification bound and safe elapsed-time messages
every 30 seconds. Metadata verification remains bounded to 3 minutes; all image
bytes must still pass digest/size verification before tag promotion. Elapsed-time
messages are liveness indications, not byte-progress estimates. Four regression
cases cover bounds, timer cleanup and secret-safe failure reporting. The full
Node suite passed 203 tests with 2 skipped (205 total); content-lock and diff
checks passed. Live FreeCAD/PCB full verification remains pending.

## 2026-10-22 — registry HEAD integrity gate

Owner approved replacing mandatory local full readback with manifest/config
hash and platform verification plus HEAD checks for every referenced blob.
The previous FreeCAD full readback was stopped; PCB digest upload continued.
This gate trusts registry content-addressed storage and its digest/size headers;
it does not claim independent readback of all remote bytes. Full verification
remains available as a diagnostic command.

Live FreeCAD verification succeeded for digest
`sha256:da7d8d5d1593ea46fdd55807d4bba52db2c0348496b46e89416376f97e65a5ce`,
linux/amd64, all 16 layers plus config, with verification mode `registry-heads`.
The full Node suite passed 204 tests with 2 skipped (206 total), all 11 Python
verifier tests passed, and content-lock/diff checks passed. Input tag promotion
and PCB publication remain pending. CI continues to check metadata and the
trusted publisher-owned input tags without downloading layers.

## 2026-10-22 — completed local shared image publication

`npm run publish:images` completed successfully from source `188d515` on macOS
arm64 with Docker Desktop/Buildx producing linux/amd64 images and regctl v0.11.7.
All three manifests/configs and blob HEAD identities passed; all input tags were
read back successfully. Chip reused its existing image; FreeCAD and PCB reused
cached OCI build layers and previously uploaded immutable digests, then promoted
their input tags. The earlier FreeCAD and PCB large-layer chunk uploads completed
successfully. No Tool was created and no release/content lock was changed.

| Domain | Immutable digest | Layers | Verification |
| --- | --- | --- | --- |
| Chip | `sha256:ef6531dc6b7e3fcb7bdd0f04a05d197b73ac4765e3a6f653c8838140a6189d7a` | 29 | registry-heads |
| FreeCAD | `sha256:da7d8d5d1593ea46fdd55807d4bba52db2c0348496b46e89416376f97e65a5ce` | 16 | registry-heads |
| PCB | `sha256:9fd7098faef318a95e77b8e08886d029be0f764794e1568c8820556726e1ade0` | 11 | registry-heads |

Evidence lives in the local Git common directory under
`runtime-image-publications/188d515765f44855856d0609f9fde3e096b41529/<domain>/attempt-*/registry-heads.json`.
Independent incremental review found no blocking issues and reran all 11 Python
and 18 focused Node tests successfully. Full-byte readback was previously proven
for Chip only; this record does not claim full-byte readback or new native AGS
execution qualification for FreeCAD/PCB. GitHub Tool publication remains disabled
until this workflow change is merged; required-check enforcement needs admin setup.

## 2026-10-22 — AGS personal CCR image reference correction

Test publication run `39777033553` passed registry checks but all three created
Tools entered FAILED. AGS StatusReason explicitly rejected digest-only personal
CCR image references and required tag or tag+digest. Corrected Tool requests to
use the existing input tag plus immutable digest, preserving canonical digest-only
identities in release locks and registry API reads. Tests cover tagged request
reuse/resume, canonical lock output and registry URL construction.

Owner authorized deleting only the three failed test Tools (`sdt-k02zv7lz`,
`sdt-7d1p957v`, `sdt-ack99r9f`); deletion was confirmed and seven ACTIVE Tools
remained. No ACTIVE Tool was modified or removed. Independent incremental review
found no blocking findings; 15 focused tests passed. The complete suite passed
205 tests with two skipped (207 total), with content-lock and diff checks passing.
Live retry qualification follows this code correction.

## 2026-10-22 — first test Tool publication and writeback regression

Run `39778046006` successfully created and verified all three test Tools using
input-tag-plus-digest references. Final lock writeback stopped at `npm test`:
a pre-publication facade test incorrectly assumed the real checkout could never
contain a covered Chip test lock. Removed that checkout-state assumption; isolated
missing-coverage, tampering, platform and successful-read tests remain intact.

All seven release tests passed both in the unpublished checkout and an isolated
copy with a valid covered test lock. The complete suite passed 205 tests with two
skipped (207 total); content-lock and diff checks passed. Incremental review was
skipped for this four-line test-only removal; previous implementation reviews
remain applicable. The next publication should reuse the three ACTIVE Tools and
complete lock writeback without creating replacements.

## 2026-10-22 — successful test publication and automatic lock writeback

Run `39778930538` completed successfully on source `3efa363`. All three Check jobs
reused the ACTIVE Tools created by run `39778046006`; Publish jobs were skipped.
The finish job passed its checks and pushed commit `dc2d758`, containing all three
test release locks, package file entries and the final content lock.

- Chip: `sdt-3mpfi02x`.
- FreeCAD: `sdt-6e1agoen`.
- PCB: `sdt-lxd25fhf`.

This qualifies test Tool creation, readback, reuse and automatic feature-branch
lock writeback. It does not qualify native instance execution or prod publication.
The bot writeback uses GITHUB_TOKEN and therefore does not trigger new PR checks;
a subsequent documentation push triggers checks on the final lock-bearing tree.
The account now has ten Tools. Prod creation requires capacity planning, and
protected-main lock writeback still needs qualification against the enforced PR
and required-check rules. Neither limitation was bypassed in this test.

## 2026-10-22 — automatic PR test publication preparation

Same-repository PRs targeting main now select the feature head SHA/branch for test
publication and lock writeback. Feature pushes use existing PACK_BUMP_PAT so the
new head receives ordinary checks; a generated-commit guard skips recursive
publication. Fork and Dependabot-triggered PR publication are excluded. Missing
images still stop the read-only Check phase. Main prod writeback is unchanged.

The full Node suite passed 215 tests with two skipped (217 total); actionlint,
content-lock and diff checks passed. Tests cover PR head versus merge identity,
generated commit recognition and negative cases including code/dependency changes
and incorrect source parents. The guard also recognized real writeback commit
dc2d758. Live automatic PR trigger/writeback/skip qualification is pending push.
