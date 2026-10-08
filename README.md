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
| CAD guidance | AutoCAD macOS, ezdxf and intent-loop Skills | Skill-only; no remote execution profile |

The migration imports only committed public source: Harness `371b011b41417d5cb0c29bc9a0fd4c8bfa4bf75e` and FreeCAD PR29 `62510b22b47803e0841825ec1c2b34fae48b3b15`. Exact imports are recorded in [provenance](provenance/domain-migration.json). Existing uncommitted work and private PCB actor resources are excluded. Pack-local READMEs and harness-pack.json preserve upstream/bootstrap setup notes, including references to Harness-only scripts and earlier plans. Current availability and consumer instructions are defined by this README, pack.json and docs/migration-validation.md; legacy consumer metadata remains reference material until the Harness consumer transition.

## Consumers

```sh
npm install --ignore-scripts 'https://codeload.github.com/Zhiman-BJ/industrial-domain-packs/tar.gz/<full-reviewed-commit>'
```

The package `@zhiman-bj/industrial-domain-packs` exports `catalog`, `identity`, `getSandboxProfile()` and integrity checks. `content-lock.json` identifies maintained code and metadata. Pin the full commit in both package.json and package-lock.json. No npm registry publication is required for this Git dependency.

The remote service consumes the trusted `chip-pack/rtl-cpu` profile, including its descriptor, Verifier, native entry, bounded artifact exporter and Dockerfile. It checks the sandbox's content identity before native execution. The native entry calls the same EDA `semantic.prepare` and `semantic.observe` used by local execution; both canonical adapters use `runtime/verifier.cjs`. Image versions and Pack identities are part of capability disclosure and persisted jobs. The local Harness consumer transition remains separate; existing installations do not change automatically.

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

After deliberate source changes, stage reviewed files under packs/ and lib/ and run `npm run lock`. This changes the release identity. Build consumer and sandbox images from the same pinned package, then drain the old coordinator before switching.

Build a committed **source-only** archive for any Pack with `python3 scripts/package-pack.py chip --output dist` (also godot, pcb, freecad, cad). Archives contain import notices and file hashes; executable images use the complete pinned package as their build context.

## Ownership and license

This repository owns domain code. [Industrial Agent Harness](https://github.com/Zhiman-BJ/industrial-agent-harness) owns canonical contracts, generic Runtime, Broker, Kimi integration, CLI, Desktop and Viewer framework. The private remote service owns authentication, snapshots, grants, bounded scheduling and sandbox lifecycle. Workloads receive no Docker socket or Kubernetes credentials. Process success never substitutes for canonical verification.

Original contributions and imported Harness contributions use MIT; preserve [notices](THIRD_PARTY_NOTICES.md) and `licenses/industrial-agent-harness.MIT`. Native tools retain their own terms. Customer projects, credentials, deployment endpoints, PDKs, private PCB actor source, native binaries and archived runs are outside this public source repository.

See [architecture](docs/architecture.md), [release identity](docs/release-format.md), and [migration](docs/migration.md).

## Harness consumer release

Version 0.3.0 adds `consumerMetadata()`, `hostPacks()`, `sourceDirectory()` and `skillResource()`. The maintained package now owns the host declarations, capability/Skill catalog and verified source resources used by Harness packaging. Canonical Pack IDs are shared with the existing catalog. Harness imports this exact package; it must not maintain a second domain registry or source tree. See [consumer release](docs/harness-consumer.md).

Version 0.4.0 introduces the [PCB/Godot professional Runtime profiles](docs/professional-runtime.md), replacing integrated legacy MCP disclosure with canonical Runtime Actions. Installed Packs use the Harness-injected backend outside development repositories. Native binaries require separate installation; no new remote or other-platform qualification is asserted.
