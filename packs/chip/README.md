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

## Optional layout viewer environment

Distribution **0.6.3** adds `pythonEnvironments` to the trusted host declaration,
independently of the unchanged EDA 0.6.1 provider and RTL execution prerequisites.
The entry `klayout-viewer` has purpose `viewer`, viewer `layout`, modules
`klayout.db`/`klayout.lay`, and maps only
`INDUSTRIAL_HARNESS_KLAYOUT_PYTHON`. It does not replace
`INDUSTRIAL_HARNESS_EDA_PYTHON` or qualify an engineering runtime.

The complete private interpreter is CPython **3.12.15**, Astral
python-build-standalone **20261013**, using official `install_only.tar.gz`
archives that include pip **26.2.1**. Unix interpreters are at
`python/bin/python3.12`; Windows uses `python/python.exe`. KLayout **0.30.12**
comes from its exact official PyPI `cp312-cp312` wheel. These declarations cover
macOS ARM64, Linux glibc x64/ARM64 and Windows x64; no musl, Intel Mac or Windows
ARM64 variant is selected. The Linux wheels require glibc 2.27 or newer, in
addition to the interpreter's own platform requirements.

[`resources/klayout-viewer/assets.json`](resources/klayout-viewer/assets.json)
records the fixed official release API/PyPI sources, URLs, SHA-256 values,
compressed sizes, executable paths, license references and measured archive
payloads. All eight selected downloads were independently hashed on 2026-10-20.
`installedSize` is an installation-space budget: regular-file and symlink-text
bytes in the interpreter archive plus wheel uncompressed bytes, with **16 MiB**
for generated installation metadata/bytecode. It excludes filesystem allocation,
download cache and temporary extraction. It is not a measured final footprint
on every OS.

The inline manifest `probe` must exactly match
[`resources/klayout-viewer/probe.py`](resources/klayout-viewer/probe.py). It writes
a rectangle to temporary GDS, loads it through the real `klayout.lay.LayoutView`,
renders PNG, verifies its signature/IHDR and **64×64** dimensions, and removes the
temporary files. A compatible manager runs it in staging and again at the final
private path. macOS ARM64 passed an offline wheel install and the real probe;
other platform download identities were checked, without claiming native probe
or engineering qualification. No system Python, uv, native EDA tools or user
project is required by this viewer-only installation path.

Maintainer reproduction (metadata only; does not download native binaries):

```sh
node packs/chip/scripts/update-viewer-environment.cjs
node --use-env-proxy packs/chip/scripts/update-viewer-environment.cjs --check --verify-upstream
node --test tests/chip-viewer-environment.test.cjs
npm run lock
```

For an explicitly supplied prepared interpreter, tests can additionally run the
real probe with `INDUSTRIAL_HARNESS_KLAYOUT_PYTHON=/private/path/to/python`.
Preserve the complete upstream CPython/pip and KLayout license/notices trees;
these downloaded dependencies keep their own licenses rather than this
repository's MIT license.
