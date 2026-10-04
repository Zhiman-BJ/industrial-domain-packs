# Chip Pack source provenance

The bootstrap imports original source from the public repository `Zhiman-BJ/industrial-agent-harness` at commit `01d8ea2c01bdf3e3be858b1bcee0c29b174ad8c5`, primarily `domain-packs/chip/`. Exact source and destination paths and SHA-256 values are preserved in `../../provenance/chip-bootstrap.json`.

The EDA subtree originated from `Zhiman-BJ/eda-harness` commit `9842ba787230e7bee2de37056594d567ee925d31`, with the public Harness's documented 0.6.1 patch set. [UPSTREAM_PROVENANCE.md](UPSTREAM_PROVENANCE.md) preserves the original record. This bootstrap is not an upstream 0.7.0 release.

Original EDA contributions retain their authorized MIT license in `eda-harness/LICENSE`; the Core adapter and smoke script retain the Harness MIT notice in `../../licenses/industrial-agent-harness.MIT`. Third-party native tools, PDKs and dependencies retain separate terms.

The import selects source modules, locks, Skill, source tests, the Core adapter, image recipes and small public educational fixtures used by those recipes. It excludes upstream archived logs, execution receipts, prebuilt environments, native binaries and private deployment material. Repository documentation and bootstrap packaging are new original contributions.

Future shared domain changes are maintained in this repository. The bootstrap file hashes are historical provenance; a new release records the current content separately. Existing Harness or remote-service copies are not automatically replaced by this import.

The maintained migration refreshes selected public source to Harness 371b011b41417d5cb0c29bc9a0fd4c8bfa4bf75e; see ../../provenance/domain-migration.json. The original bootstrap record is preserved. New shared rtl-cpu entry, recipe and Verifier are identified by content-lock.json.
