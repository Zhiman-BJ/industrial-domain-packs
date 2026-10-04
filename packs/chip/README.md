# Chip Pack source

This directory maintains the EDA Harness 0.6.1 bootstrap imported from a fixed public Industrial Agent Harness commit. Source, Python locks, Skill, Core adapter and the full tool recipe are present. The original implementation and Verifier have not been rewritten during this import.

From the repository root:

```sh
uv sync --frozen --directory packs/chip/eda-harness
uv run --frozen --directory packs/chip/eda-harness python ../scripts/mcp-smoke.py
uv run --frozen --directory packs/chip/eda-harness pytest -q
```

To run the standalone MCP for an existing project:

```sh
uv run --frozen --directory packs/chip/eda-harness eda --project /absolute/project mcp
```

The project needs a valid `eda.yaml`. The full raw MCP has 25 tools; protected Core sessions use their scoped Runtime adapter. Real EDA actions require an independently prepared tool image or declared native environment. Python installation alone does not install Verilator, Yosys, OpenROAD or PDK assets.

The existing full recipe can be built from its complete source context:

```sh
docker build --platform linux/amd64 \
  -f packs/chip/eda-harness/Dockerfile.tools \
  -t eda-harness-tools:source-bootstrap packs/chip/eda-harness
```

This command downloads the pinned upstream images. No image has been published or newly qualified by this repository. A shared RTL-only profile and a sandbox process backend are planned in [migration](https://github.com/Zhiman-BJ/industrial-domain-packs/blob/main/docs/migration.md).

`harness-pack.json` preserves the original consumer metadata; its provider source hash describes the historical Harness layout. It is reference metadata until the source paths and content digest are regenerated for this repository's release format. `pack.json` is the source catalog, not an executable qualification record.

Source identity and licenses are in [provenance](PROVENANCE.md). Historical runtime notes under `eda-harness/docs/` describe the imported implementation; their linked execution receipts are retained in the upstream repository, not redistributed here.
