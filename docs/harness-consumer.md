# Harness consumer release 0.3.1

Domain ownership is centralized here. Host Runtime declarations, domain capabilities, Skill metadata and source directories are exported with the same content-lock identity as sandbox profiles. Harness consumes an exact Git commit and matching package lock integrity. It may assemble signed distribution caches from these bytes; those caches are not maintenance sources.

The release reconciles committed Harness 2482114 changes using original import commits as merge bases. The exact changed paths and source hashes are recorded in provenance/harness-consumer-reconciliation.json; original imports remain in domain-migration.json. Chip retains the common sandbox/local Verifier while gaining the verified host environment preflight and protected workspace fixes. FreeCAD retains the later bounded edit, repair and cancellation behavior before consumer replacement.

`consumerMetadata()` provides domain labels, availability, capability defaults and Skill references. `hostPacks()` provides canonical Pack IDs with host providers and source locks. `sourceDirectory(packId)` resolves only maintained package sources. `skillResource(id)` resolves a maintained Skill directory. Consumers must preserve per-project enablement policy and canonical Runtime authorization; metadata does not authorize execution.

New distribution versions are Chip 0.6.2, FreeCAD 1.1.4-pack.4, Godot 0.1.1 and PCB 2026.09.28-pack.1. Native software versions are unchanged. PCB actor hashes and image identity identify private external dependencies, not redistributed actor code. Sandbox profile exports remain compatible with existing pinned remote consumers. No additional remote/native platform qualification is asserted by source tests.

The Chip distribution remains 0.6.2, while its host MCP declaration identifies the embedded EDA Python package as 0.6.1. The gateway validates that native identity against its locked environment; a distribution version must not substitute for the tool version. Consumer tests compare the declaration with the maintained Python project metadata.

## Consumer release 0.4.0

PCB 0.1.0-kicad.1 and Godot 0.2.0 replace integrated legacy MCP declarations with typed Runtime tools and explicit native requirements Verifiers. The installed factory now requires the trusted `runtimeApi` supplied by Harness. Consumer dependency pins and integrity must be updated together; older consumers cannot claim this installed professional path. See [profiles and limitations](professional-runtime.md).

## Consumer release 0.4.1

PCB 0.1.0-kicad.2 and Godot 0.2.1 add managed native dependency recipes, and FreeCAD 1.1.4-pack.6 adds its measured installed size. Native tool versions, Runtime code, protected actions, Verifiers and qualified platforms are unchanged. The FreeCAD distribution skips pack.5, which was reserved by a separate result-presentation change; no runtime or presentation behavior from that change is included here.

Harness must support the generic `macos-app-zip` type, `archiveApp` nested bundle source, `environmentExecutables` mappings and `installedSize` display metadata before consuming this release. The PCB recipe supplies both kicad-cli and the exact bundled Python used by pcbnew. The Godot recipe needs no export templates for the qualified structural task. The owner retains dependency and license provenance; downloads, health, repair and cancellation are Harness responsibilities. See [native installation](native-installation.md) for source measurements, external prerequisites and qualification limits. Do not update a public consumer pin without its installation checks.

## Consumer release 0.5.1

This release merges the 0.4.1 managed installation recipes with 0.5.0 FreeCAD result declarations. FreeCAD distribution 1.1.4-pack.7 has the exact pack.5 Runtime/presentation sources and the complete pack.6 native recipe, including measured installed size. Its new distribution identity allows both existing pack.5 and pack.6 installations to upgrade without republishing their content. PCB 0.1.0-kicad.2, Godot 0.2.1, Chip prerequisites, and CUDA remote-service and unavailable desktop-bundle declarations are retained.

Consumers must use one immutable 0.5.1 commit and matching lockfile integrity across CLI and Desktop. Source and presentation tests preserve both behavior sets; they do not replace combined packaged installation, native task and result-presentation acceptance. This release adds no qualified platforms or remote profiles.

## Consumer release 0.5.2

Godot distribution 0.2.2 combines the managed native recipe with the typed scene-edit validation and guide fixes from owner main `8f00a3fd12211bfce3b91a69d0bea2406b6695a6`. Its consumer domain, host/provider and Pack distribution versions agree. The Runtime tool version remains 0.2.1 because those execution sources are unchanged from that reviewed main commit; the distribution version identifies the combined source and installation metadata.

FreeCAD 1.1.4-pack.7, PCB 0.1.0-kicad.2, Chip and CUDA retain their previous Pack bytes and versions. No platform qualification or dependency archive changes. Consumers must pin the new full commit and archive integrity together; old 0.5.1 and Godot 0.2.1 content is not republished under its former identity.

## Manual consumer pin bump CD (2026-10-19)

`.github/workflows/harness-pin-bump.yml` is the manually triggered release CD from this repository to the industrial-agent-harness consumer. It never runs on push. Dispatch it from the Actions tab (optionally passing a `ref` input — a commit SHA, branch or tag to pin; by default it pins the branch the run was dispatched on) and it compares the consumer's `@zhiman-bj/industrial-domain-packs` tarball pin with that commit. When the pin is behind, the workflow repins the root, CLI and Desktop manifests together, aligns the domain-skills peer version with the target package version, refreshes `pnpm-lock.yaml` with `pnpm install --lockfile-only`, force-pushes the single branch `chore/pack-pin-auto` in the consumer repository, closes any superseded auto bump pull request and opens a fresh one. The pull request body lists the Domain Packs commits since the old pin and a tarball file inventory diff. The inventory only shows added/removed paths; reviewers must still inspect the commit diff for changes inside existing files.

The CD never merges or auto-merges the bump pull request; the coordinated consumer-merge rule from [release identity](release-format.md) is unchanged. The workflow reads pnpm's version from `harness/package.json`, requires a changed lockfile, and runs the consumer architecture boundary tests before pushing. A mismatched existing adapter pin aborts before writing any manifests. The consumer's frozen-lockfile CI remains the acceptance gate.

Setup requirement: a fine-grained personal access token with repository `Contents: read/write` and `Pull requests: read/write` on `Zhiman-BJ/industrial-agent-harness`, stored as the `PACK_BUMP_PAT` secret in this repository. Both repositories are public, so tarball downloads in the workflow need no credentials. If the secret is missing the workflow fails closed (no pull request is opened) and a manual pin bump remains possible at any time.

## Optional data libraries: consumer release 0.5.6

PCB distribution 0.3.3 adds the top-level `dataLibraries` declaration to its host
manifest. Native KiCad remains 10.0.6 with its existing platform qualification.
`hostPacks()` carries this declaration unchanged; it is independent of provider
execution resources, Tools and `runtimeAssets` installation readiness.

Each declaration binds `id`, `version`, `label`, immutable HTTPS `baseUrl`, a
Pack-relative `catalog`, `catalogSha256`, and exact `referencePrefixes`. The PCB
catalog is `resources/kicad-3d-10.0.7.json`; its SHA-256 binds the complete list of
original paths, byte counts and raw-content SHA-256 values. The original license
notice resides alongside it under `resources/licenses/`. Both are npm package and
content-lock resources. A distribution builder must copy these resources and
rebase catalog paths to its own fixed Pack resource directory.

A compatible host may resolve eligible missing references against that exact
catalog and fetch only admitted files into a bounded, versioned read-only cache.
The domain owner supplies library identity and reference semantics; the host
owns network/cancellation, scope checks, byte/time/memory budgets, hash checking,
cache health and viewer presentation. Matching a prefix does not authorize path
guessing or changing extensions. This catalog contains STEP only; existing WRL
references require their actual local model and cannot be silently replaced.
Unknown, missing, invalid and over-budget models must remain explicit. Optional
data failures cannot make an installed native application appear uninstalled.

Consumer release 0.5.8 / PCB distribution 0.3.4 uses catalog schema 2 and a matching
`download: { type: "gitlab-repository-files", project, ref }` in the declaration and
catalog. `baseUrl` is the plain official `/api/v4/` directory; `ref` is the complete
40-character immutable commit, not a branch or tag. The host constructs the
repository-files endpoint with fully encoded project/file paths and only this
fixed ref query. Redirects cannot change origin, project, file or ref. Existing
schema-1 directory catalogs retain their old behavior. Unsupported consumers,
including beta.7, reject schema 2 during catalog validation; consumer migration
must accompany publication of this new Pack. File sizes/hashes and the model
library version remain unchanged.
