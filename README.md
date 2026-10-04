# Industrial Domain Packs

[简体中文](README.zh-CN.md)

Shared domain implementations and versioned Pack sources for Industrial Agent Harness. Local Docker execution and remote Kubernetes execution will consume the same domain release, including tool definitions, execution code, verification, dependency locks and tool-image identities.

**Status: source bootstrap.** The first Pack imports the existing public, MIT-licensed Chip implementation. The shared remote execution backend, RTL-only image, immutable runtime releases and consumer migration are planned. Creating this repository does not switch an existing Harness installation or remote deployment.

## Repository ownership

| Repository | Responsibility |
| --- | --- |
| **industrial-domain-packs** | Domain MCP, domain execution, canonical tool and verifier adapters, Skills, native-tool recipes and Pack releases |
| [industrial-agent-harness](https://github.com/Zhiman-BJ/industrial-agent-harness) | Canonical contracts, industrial Runtime, Broker, Agent integration, CLI, Desktop and Viewer framework |
| industrial-harness-remote | Authentication, snapshots, approval envelopes, bounded scheduling, sandbox lifecycle and transport |

Domain changes are maintained here. Consumers will pin a Pack release rather than maintain handwritten copies. Canonical industrial contracts remain owned by Industrial Agent Harness.

## First Pack

`packs/chip/` contains the EDA Harness 0.6.1 source, 25-tool stdio MCP, Python lockfile, EDA Skill, existing Core RTL adapter and full EDA tool-image recipe. The source comes from public Harness commit `01d8ea2c01bdf3e3be858b1bcee0c29b174ad8c5`; individual imported file hashes are recorded in [bootstrap provenance](provenance/chip-bootstrap.json).

Install the Python source with Python 3.13 and uv, then check the real stdio MCP:

```sh
uv sync --frozen --directory packs/chip/eda-harness
uv run --frozen --directory packs/chip/eda-harness python ../scripts/mcp-smoke.py
```

This check discovers tools and reads server information. Engineering execution requires separately prepared native tools and a valid project. The inherited full EDA recipe targets Linux amd64; a shared `rtl-cpu` image and Kubernetes execution backend are the next implementation steps. The raw MCP surface is for standalone EDA use; protected Harness sessions must enter the canonical Runtime and scoped gateway.

## Source archives

After committing changes, build an archive from the committed Pack files:

```sh
python3 scripts/package-pack.py chip --output dist
```

The archive contains a source manifest with the exact Git commit and file hashes; an adjacent SHA-256 file identifies the archive. It excludes untracked files and local Python environments. This bootstrap packaging does not publish or qualify an executable image.

See [architecture](docs/architecture.md), [release identity](docs/release-format.md), [migration plan](docs/migration.md), and [Chip source notes](packs/chip/README.md).

## License and imported components

Original contributions use MIT. Imported Chip source retains its original notices and [provenance](packs/chip/PROVENANCE.md). Native tools, PDKs, libraries and binary dependencies retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md). This repository contains source, build recipes and small upstream educational fixtures. It does not contain customer projects, deployment credentials, PDK distributions, native binaries or archived execution logs.
