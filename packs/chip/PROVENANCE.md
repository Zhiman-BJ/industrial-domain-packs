# Chip Pack source provenance

The bootstrap imports original source from the public repository `Zhiman-BJ/industrial-agent-harness` at commit `01d8ea2c01bdf3e3be858b1bcee0c29b174ad8c5`, primarily `domain-packs/chip/`. Exact source and destination paths and SHA-256 values are preserved in `../../provenance/chip-bootstrap.json`.

The EDA subtree originated from `Zhiman-BJ/eda-harness` commit `9842ba787230e7bee2de37056594d567ee925d31`, with the public Harness's documented 0.6.1 patch set. [UPSTREAM_PROVENANCE.md](UPSTREAM_PROVENANCE.md) preserves the original record. This bootstrap is not an upstream 0.7.0 release.

Original EDA contributions retain their authorized MIT license in `eda-harness/LICENSE`; the Core adapter and smoke script retain the Harness MIT notice in `../../licenses/industrial-agent-harness.MIT`. Third-party native tools, PDKs and dependencies retain separate terms.

The import selects source modules, locks, Skill, source tests, the Core adapter, image recipes and small public educational fixtures used by those recipes. It excludes upstream archived logs, execution receipts, prebuilt environments, native binaries and private deployment material. Repository documentation and bootstrap packaging are new original contributions.

Future shared domain changes are maintained in this repository. The bootstrap file hashes are historical provenance; a new release records the current content separately. Existing Harness or remote-service copies are not automatically replaced by this import.

The maintained migration refreshes selected public source to Harness 371b011b41417d5cb0c29bc9a0fd4c8bfa4bf75e; see ../../provenance/domain-migration.json. The original bootstrap record is preserved. New shared rtl-cpu entry, recipe and Verifier are identified by content-lock.json.

## Optional KLayout viewer assets (distribution 0.6.3)

The owner declares CPython 3.12.15 from the official Astral
python-build-standalone release **20261013** (published 2026-10-14) and KLayout
0.30.12 `cp312` wheels from official PyPI. The fixed metadata and full download
identity for macOS ARM64, Linux x64, Linux ARM64 and Windows x64 are in
`resources/klayout-viewer/assets.json`; the archive contents were inspected and
all eight downloads independently checked against their published SHA-256/size.
No native archive, wheel or installed environment is committed or bundled.

PBS's build repository is MPL-2.0; this is not the license of the complete
interpreter distribution. CPython retains PSF License Version 2 and its historical
notices; pip is MIT with separately licensed vendored dependencies. The exact
PBS tag's `LICENSE.cpython.txt` and `python-licenses.rst` identify upstream and
native-library terms. The actual `install_only` archives preserve CPython and
pip license trees and contain no `PYTHON.json`; their metadata is not represented
as a single blanket MPL license. KLayout's PyPI metadata declares
**GPL-3.0-or-later**, with the corresponding source license fixed at
`KLayout/klayout` tag **v0.30.12**. Preserve all downloaded notices and matching
upstream license/source references. This repository supplies only original MIT
declaration/generation/probe code and source provenance.

The synthetic GDS-to-PNG probe is an installation display check. It reads no user
project, performs no industrial Action and cannot create engineering acceptance
or qualify the unchanged RTL execution profile. Only the macOS ARM64 environment
and PNG probe were executed locally; other-platform metadata/download checks are
not native qualification receipts.
