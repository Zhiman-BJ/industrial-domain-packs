# Industrial Domain Packs

Development boundary: [accepted architecture contract IH-ARCH-001](docs/architecture-contract.md).

[简体中文](README.zh-CN.md)

Maintained public source for Industrial Harness domains. Local and remote consumers pin this repository by an exact Git commit; domain execution, Skills, dependency locks, tool definitions, Verifiers and image recipes are developed here.

| Pack | Included implementation | Execution boundary |
| --- | --- | --- |
| Chip | EDA 0.6.1, 25-tool MCP, canonical RTL adapter, Skills, full EDA recipe and shared `rtl-cpu` recipe | CPU trial exposes `chip.rtl.verify`; raw MCP has a wider standalone surface |
| Godot | Typed scene edits, source StateProvider, independent native import/readback/frame Verifier | macOS Apple Silicon, Godot 4.7.2; bounded structural task |
| PCB | Public typed KiCad edits, source StateProvider, DRC + native geometry Verifier | macOS Apple Silicon, KiCad 10.0.6; rectangular mounting-board task |
| FreeCAD | Maintained runtime reconciled with verified Harness fixes, bounded recipe validator, native code and Skill | Inherited macOS arm64 qualification; Linux/Windows are not qualified |
| CUDA | Paired remote Compiler/Evaluator MCP Servers, bounded source client, canonical Verifier and optimization Skill | Fixed AXPBY RTX 4090 profile; private native workers remain external; see [qualification](packs/cuda/QUALIFICATION.md) |
| CAD guidance | AutoCAD macOS, ezdxf and intent-loop Skills | Skill-only; no remote execution profile |

The migration imports only committed public source: Harness `371b011b41417d5cb0c29bc9a0fd4c8bfa4bf75e` and FreeCAD PR29 `62510b22b47803e0841825ec1c2b34fae48b3b15`. Exact imports are recorded in [provenance](provenance/domain-migration.json). Existing uncommitted work and private PCB actor resources are excluded. Pack-local READMEs and harness-pack.json preserve upstream/bootstrap setup notes, including references to Harness-only scripts and earlier plans. Current availability and consumer instructions are defined by this README, pack.json and docs/migration-validation.md; legacy consumer metadata remains reference material until the Harness consumer transition.

## Consumers

```sh
npm install --ignore-scripts 'https://codeload.github.com/Zhiman-BJ/industrial-domain-packs/tar.gz/<full-reviewed-commit>'
```

The package `@zhiman-bj/industrial-domain-packs` exports `catalog`, `identity`, `getSandboxProfile()` and integrity checks. `content-lock.json` identifies maintained code and metadata. Pin the full commit in both package.json and package-lock.json. No npm registry publication is required for this Git dependency.

The remote service consumes the trusted `chip-pack/rtl-cpu` profile, including its descriptor, Verifier, native entry, bounded artifact exporter and Dockerfile. The shared worker checks runtime identity and input-snapshot integrity before native execution; full Pack content integrity is checked outside the sandbox. The native entry calls the same EDA `semantic.prepare` and `semantic.observe` used by local execution; both canonical adapters use `runtime/verifier.cjs`. Image versions and Pack identities are part of capability disclosure and persisted jobs. The local Harness consumer transition remains separate; existing installations do not change automatically.

## Development and validation

```sh
npm ci --ignore-scripts
npm test
npm run lock:check
uv sync --frozen --directory packs/chip/eda-harness
uv run --frozen --directory packs/chip/eda-harness pytest -q
uv run --frozen --directory packs/chip/eda-harness python ../scripts/mcp-smoke.py

docker buildx build --platform linux/amd64 --load \
  -f packs/chip/images/Dockerfile.rtl-cpu -t industrial-domain-rtl:0.2.0 .
./validate.sh packs/chip/examples/rtl --image industrial-domain-rtl:0.2.0
```

`validate.sh` starts one isolated ephemeral container per task, checks real simulation completion, native verification and VCD structure, and writes `result.json`. See [qualification](docs/migration-validation.md) for exercised paths and limits. Images are validated locally; source CI does not build or publish tool images.

After deliberate source changes, stage reviewed files under packs/ and lib/ and run `npm run lock`. This changes the release identity. Pin consumers to a reviewed Pack commit and sandboxes to the selected profile's immutable image; see [release identity](docs/release-format.md) for build-source versus publication identities.

Build a committed **source-only** archive for any Pack with `python3 scripts/package-pack.py chip --output dist` (also godot, pcb, freecad, cad, cuda). Archives contain import notices and file hashes. A pinned repository may serve as the Docker build context; this does not mean its entire contents belong in the image. Remote image contents follow the [packaging rules](docs/release-format.md#remote-runtime-image-packaging).

## Ownership and license

This repository owns domain code. [Industrial Agent Harness](https://github.com/Zhiman-BJ/industrial-agent-harness) owns canonical contracts, generic Runtime, Broker, Kimi integration, CLI, Desktop and Viewer framework. The private remote service owns authentication, snapshots, grants, bounded scheduling and sandbox lifecycle. Workloads receive no Docker socket or Kubernetes credentials. Process success never substitutes for canonical verification.

Original contributions and imported Harness contributions use MIT; preserve [notices](THIRD_PARTY_NOTICES.md) and `licenses/industrial-agent-harness.MIT`. Native tools retain their own terms. Customer projects, credentials, deployment endpoints, PDKs, private PCB actor source, native binaries and archived runs are outside this public source repository.

See [architecture](docs/architecture.md), [release identity](docs/release-format.md), and [migration](docs/migration.md).

## Harness consumer release

Version 0.3.0 adds `consumerMetadata()`, `hostPacks()`, `sourceDirectory()` and `skillResource()`. The maintained package now owns the host declarations, capability/Skill catalog and verified source resources used by Harness packaging. Canonical Pack IDs are shared with the existing catalog. Harness imports this exact package; it must not maintain a second domain registry or source tree. See [consumer release](docs/harness-consumer.md).

Version 0.4.0 adds the CUDA Pack. Configure both authenticated remote MCP endpoints in trusted host settings. Local worker preparation requires a GPU/profile preflight and explicit evaluator UUID; remote clients need no local GPU. See [deployment and client setup](packs/cuda/README.md).

Version 0.4.0 also introduces the [PCB/Godot professional Runtime profiles](docs/professional-runtime.md), replacing integrated legacy MCP disclosure with canonical Runtime Actions. Installed Packs use the Harness-injected backend outside development repositories. No new remote or other-platform qualification is asserted.

Version 0.4.1 declares managed official KiCad and Godot downloads alongside FreeCAD for a compatible Harness Pack Manager on macOS Apple Silicon. Installation sizes, version probes and both KiCad executables are declared by the owner; users do not need shell commands or environment variables on this managed path. Chip still requires its external toolchain and CUDA requires administrator-provided remote services. See [native installation metadata and limits](docs/native-installation.md). Existing public installers do not gain these recipes until their consumer pin and generic installer are updated and qualified.

FreeCAD now declares version-bound result groups for models, previews, exports and diagnostics. Inspect results reference their exact input snapshot; edits explicitly replace the input version only after successful native generation. Harness binds local output names to registered Artifacts. Selection is optional; model geometry acceptance still requires the existing Verifier.

[All-domain result declarations and expansion plan](docs/result-presentation-plan.md), with [schema-checked examples](docs/result-presentation-examples.json), cover the five domains and six Packs. This is preparation: other Pack producers, snapshot-version binding and CUDA consumer adoption remain to be implemented and qualified.

Version 0.5.1 combines these managed installation recipes with the 0.5.0 result declarations. FreeCAD distribution 1.1.4-pack.7 retains the complete installation recipe from pack.6 and the source presentation behavior from pack.5; published earlier Pack versions keep their original bytes. PCB and Godot versions and qualified platform limits are unchanged. Consumers must update their immutable pin and qualify the combined application.

Version 0.5.2 retains the managed installation recipes and FreeCAD result declarations while incorporating the latest Godot typed scene-edit checks and tool guidance. Godot distribution 0.2.2 combines those reviewed Runtime sources with the existing managed native recipe; other Pack versions and contents are unchanged. Earlier distribution versions keep their original immutable identities.

Version 0.5.3 prepares PCB distribution 0.3.1 for the GitHub Release tarball channel:
the Linux AMD64 recipe preserves KiCad 10.0.6 using verified historical official
packages, bootstraps HTTPS trust before apt, and runs a real board round-trip and
DRC during the build. `packs/pcb/validate.sh` provides isolated per-task validation.
The host gateway Python and existing native/engineering qualification boundaries
remain as documented in the [PCB Pack](packs/pcb/README.md).

Version 0.5.4 restores local verification after the remote report-contract migration:
PCB 0.3.2 and FreeCAD 1.1.4-pack.9 retain validated request expectations in the
execution result. PCB's local plugin verifies native board/DRC evidence; the remote
entry still requires its worker run report and fixed profile/version. Run
`node --test tests/pcb-local-runtime.test.cjs tests/freecad-local-runtime.test.cjs`
for production execute-to-verifier contract regressions with native-process fixtures.
These tests do not establish native qualification. Existing consumers require a
reviewed pin update; remote images must be rebuilt from the same pinned package.

Version 0.5.5 keeps the Linux FreeCAD AppImage in the remote image recipe, outside
the desktop managed-download manifest. FreeCAD distribution 1.1.4-pack.10 retains
the local verifier fix and the qualified macOS download; Linux remains a separate
remote execution profile. Harness Pack archive validation must pass on this release.

## Optional PCB model data

Package 0.5.8 / PCB Pack 0.3.4 declares the official KiCad 3D library **10.0.7** as optional,
read-only data, independently of the KiCad 10.0.6 native application dependency.
Its packaged catalog contains all **7,252 original STEP paths**, byte sizes and
independently computed SHA-256 values, bound to one official Git commit. A
compatible viewer can fetch only referenced models and apply its own resource
limits. The package includes metadata and the upstream license notice; it does
not bundle the model library. WRL paths are never rewritten to STEP paths.
See [source verification, reproduction and limits](packs/pcb/README.md#optional-official-3d-model-data).

PCB 0.3.4 uses the official GitLab repository-files API with an immutable commit
and catalog schema 2. It requires the corresponding updated Harness downloader;
beta.7 rejects this catalog rather than treating an API base as a raw directory.
All model paths, bytes, SHA-256 values and existing engineering identities are unchanged.

## Optional layout viewer Python

Package **0.5.7 / Chip distribution 0.6.3** declares a private, viewer-only
CPython **3.12.15** environment with KLayout **0.30.12**. A compatible Harness
manager can acquire the fixed official Astral PBS **20261013** `install_only`
archive and the exact CPython 3.12 PyPI wheel for macOS ARM64, Linux x64,
Linux ARM64 or Windows x64. The source package contains metadata and a synthetic
GDS-to-PNG installation probe; it contains no Python or KLayout binaries.
The managed path needs neither host Python nor uv, and uses its complete private
Python tree rather than a relocated virtual environment. See
[asset provenance, installation budgets and validation](packs/chip/README.md#optional-layout-viewer-environment).

This declaration prepares only the read-only layout viewer. It does not prepare
RTL execution or change Chip's existing Python 3.13, Verilator, C++ toolchain,
other EDA/PDK prerequisites or engineering qualification. The macOS ARM64
archive/wheel and 64×64 PNG probe were exercised; the other three platform assets
were downloaded and hash-checked, without asserting native execution support.

Remote publishers and consumers can validate schema-1 runtime release records with `getRuntimeRelease`; see [release identity](docs/release-format.md). This metadata API does not publish Tools or qualify execution profiles.

Prepare and fully verify shared images locally (Buildx OCI export and regctl chunked upload) with `npm run publish:images` first; see [local prerequisites and environment variables](docs/release-format.md#local-image-preparation). Actions only verify image metadata, publish Tools and write the final lock.

Runtime release lock values are maintained by the publishing GitHub Action; do not hand-edit generated image digests, Tool IDs or publication evidence. Before changing publication, read the [publication workflow and final hashing order](docs/release-format.md#publication-workflow-and-generated-files). `Publish remote runtime` automatically publishes prod on pushes to main; manually dispatch feature branches for test; see the [Tencent configuration and qualification limits](docs/release-format.md#tencent-publishing-action-configuration).

Before adding or changing any remote runtime profile, read the [shared packaging rules](docs/release-format.md#remote-runtime-image-packaging). Dispatch takes no inputs: main selects prod, other branches select test; image/Tool reuse is checked against Tencent Cloud. Environment configuration lives in `publication/test.cjs` and `publication/prod.cjs`, with domains grouped in each file, and results plus capacity warnings appear in the Action Summary.

Publishing runs separate Check and conditional Publish jobs; unchanged packs skip Publish and still appear in the final summary.

PRs targeting main run `Runtime images ready`; an administrator must make it a required, up-to-date check. Missing images block readiness until local preparation succeeds. See [release format](docs/release-format.md#pr-image-readiness-gate).
