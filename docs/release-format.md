# Pack release identity

Each `pack.json` describes maintained source, native dependencies and implemented/available profiles. `scripts/package-pack.py` builds a committed-source archive with `source-manifest.json`, its Git commit and file SHA-256 values. It records `kind: source-only`; it does not assert that native tools or remote execution have passed qualification.

The implemented consumer identity consists of the exact Git commit, npm package version and content-lock digest, plus a trusted profile and separately pinned runtime image. Broader release metadata can extend these identities with:

| Group | Identity |
| --- | --- |
| Pack | Pack ID, immutable release version, manifest schema and externally recorded manifest SHA-256 |
| Source | This repository's commit, source content digest and upstream provenance |
| Semantics | Canonical contract compatibility, ToolDescriptor and input-profile digests, Verifier revision and digest |
| Dependencies | Python version, dependency-lock digest, actual tool versions and required licensed assets |
| Execution | Profile, platform, immutable OCI image identity, fixed entry point and qualified resource requirements |
| Client content | Capability, Skill and Viewer declaration digests |

The manifest does not embed its own digest. A change in code, verification, dependency or image produces a new release identity. API transport and Pack versions are managed independently. Clients and servers validate compatibility and profile availability before native submission; initial consumers use exact Pack release matching.

The remote consumer records Pack content identity, profile, tool/Verifier revision, actual image and input snapshot before dispatch. Same-platform local and remote runs selecting the same profile use the same image identity. Running jobs retain their original identity during upgrades; old results keep their original Verifier provenance.

Source archives are read from Git objects rather than the working directory, so local untracked files and Python environments are excluded. The archive SHA-256 is adjacent to the archive, and per-file hashes inside the source manifest cover all included Pack files. Root and imported license notices are included separately in the archive.


## Remote runtime release records (schema 1)

`getRuntimeRelease(packId, profileId, environment)` reads a release record
from `packs/<source-directory>/releases/<profile>/<test|prod>.lock.json`. The source
directory, domain and target platform come from the pinned catalog, not a caller
path. The three arguments select the published record. Region, build identity and
runtime identity are returned metadata; they are not caller inputs. The Service
checks the returned region against its deployment constraints. No new worker
protocol-version contract is introduced. The complete Pack integrity
check runs before reading; untracked release files, altered bytes, unsafe paths and
symlinks are rejected. The result is a detached, recursively frozen record.

This is a metadata contract only. No checked-in Tool binding is provided by this API. The separate
`Publish remote runtime` Action performs Tencent publication. It does not query the cloud, prove engineering
qualification, authorize execution, compute actual build inputs or activate profiles.
The publishing Action must validate actual build inputs, Tool configuration and
qualification evidence before publishing a usable record. Consumers trust their
reviewed Pack pin and check deployment constraints. A `passed` label is not proof.
Existing local/remote profile APIs and worker execution behavior are unchanged.

Required fields (unknown fields are rejected, including arbitrary launch settings):

| Field | Contract |
| --- | --- |
| schemaVersion / releaseId | `1`; bounded stable lowercase identifier for this publication |
| packId / domain / runtimeProfile | Exact pinned catalog selection |
| environment / region | `test` or `prod`; exact trusted deployment region |
| buildSourceCommit | Full 40-character source Git SHA, commit A used for the build |
| buildSourceContentSha256 | Original A content-lock SHA-256, 64 lowercase hex characters |
| buildInputDigest | `sha256:` plus 64 hex characters; identity of actual build inputs, computed and validated by the publishing Action |
| runtimeDigest | Existing nonempty opaque runtime identity string; this API introduces no competing runtime hash algorithm |
| image | Exactly `reference` (registry/repository@sha256 digest) and `platform`; no mutable tag, URL scheme or credential authority; platform must match the catalog profile |
| provider / providerToolId | `tencent-ags`; recorded `sdt-...` resource ID, scoped by region |
| toolConfigurationDigest | SHA-256 identity of read-back Tool configuration; cloud read-back is a separate requirement |
| qualification | `status: passed`, nonempty unique `gates` and `evidence` string arrays; the consumer checks the required gates and evidence for its target environment |

Runtime image source identity and consumer publication identity are separate. A
build uses source commit A and its content lock. Writing a release record produces
commit B and a new content lock that covers the record itself. The record does not
embed B or B's content digest, avoiding self-reference. B is pinned externally by
the Service; `buildSourceContentSha256` continues to identify A, not B. Changing
only publication metadata does not by itself prove the image is reusable: the
build-input checker must validate the actual prepared build context and dependencies.
The build fingerprint must describe the actual image inputs under the packaging
rules below; it must not silently ignore files that the recipe copies.

The standalone `lib/runtime-release.cjs` validator/reader is for trusted publishing
and testing code. The package `getRuntimeRelease` facade additionally enforces full
package integrity, catalog routing and target platform. Neither accepts paths or
execution settings from an untrusted remote client. Public records must contain no
credentials, customer inputs or private deployment endpoints.


## Publication workflow and generated files

The `Publish remote runtime` Action implements the following workflow. It is
explicitly dispatched on a repository branch; it has no push/PR trigger. The
schema-1 metadata API does not implement an unpublished-placeholder schema.

Runtime release files at `packs/<source-directory>/releases/<profile>/<environment>.lock.json`
are owned by the publishing GitHub Action. Developers and coding agents must not
hand-edit generated image digests, Tool IDs, build identities or qualification
results. This ownership rule lives here and is linked from both root READMEs (required reading for agents); JSON
records do not need comments or an extra generated-marker field.

1. Prepare runtime source and the intended profile/environment publication slot.
   An initial placeholder is unpublished and must never be accepted as a usable
   release. Until placeholder support is implemented, do not insert fake values
   into a schema-1 record. On later updates, keep the last successful record
   intact until its replacement is ready.
2. Trigger the publishing Action on source commit A. Validate A's source content
   lock and derive the actual build-input fingerprint before building or safely
   reusing an image. Create a new Tool for a new runtime image binding and verify
   its configuration and required publication gates.
3. Only after success, fill or replace the corresponding release record with the
   real immutable image reference, Tool ID and evidence. The source identity
   fields continue to describe A. Failed publication must not replace a working
   record with partial results.
4. Stage every new generated release file under `packs/` so the lock generator
   includes it. After all covered source and release-record writes are complete,
   run `npm run lock`, then `npm run lock:check` and the relevant validation gates.
   Do not change covered files after this final hash calculation; if they change,
   regenerate and revalidate the lock.
5. Commit the generated records, `content-lock.json` and any generated package
   file-inventory update together as publication commit B on the triggering
   branch. If that branch has moved, fail or restart against its new source;
   do not attach A's results to unrelated changes. Generated-only writeback must
   not start a recursive publication. Consumers pin B only after success.

The final maintained-content hash is `content-lock.json.contentSha256`. The
existing generator hashes each tracked/staged file under `packs/` and `lib/`, then
hashes the sorted path-to-file-hash JSON map. Thus B's content hash includes the
completed release records as well as maintained source. It does **not** hash the
entire repository, `content-lock.json` itself, or the Git commit: README/workflow
changes alone need not change it. The externally pinned Git SHA identifies the
complete committed tree. An exported archive has its own separate archive hash.

The final content hash is calculated near the end of the Action, after generated
content is finalized and before committing B. It is not written into the release
record that it covers, avoiding a self-reference. `buildSourceContentSha256` is
still A's pre-build content hash, not this final B content hash. Neither hash
substitutes for the actual Docker build-input fingerprint.


## Remote runtime image packaging

All domains must follow the same remote runtime packaging rules, using the
FreeCAD and PCB recipes as the reference:

- Copy only the files required by the selected execution profile: its manifest,
  runtime code, shared worker, dependency locks/installers and required assets.
  Do not copy the entire repository, `packs/` or `lib/` as a shortcut.
- Keep runtime publication records (`releases/`), the repository-wide
  `content-lock.json`, and unrelated documentation out of runtime images and
  their build inputs. Dependency locks are actual build inputs and must remain
  included; they are different from publication records and the Pack content lock.
- Validate the full Pack content lock during publication and when the Service
  loads its pinned Pack. Do not require the sandbox to carry or revalidate the
  entire Pack source tree. Use the existing shared worker's runtime-identity and
  input-snapshot checks inside the sandbox; do not add a separate whole-Pack
  source-lock mechanism there. Runtime identity matching is not a recomputed
  image hash and does not replace immutable image binding.
- Derive the build fingerprint from actual runtime source, dependencies, recipe,
  base-image identities, target platform and effective build arguments. Shared
  worker changes affect every profile that copies it. Publication-only and
  unrelated documentation changes must not trigger image rebuilds.
- Write the image/Tool publication results first, then regenerate the final Pack
  content lock. Publication output must not feed back into image build inputs.

Apply these rules to new profiles and changes to existing profiles in every
domain. Packaging consistency does not itself establish native qualification.


## Build input calculation and image reuse

The publisher prepares a fresh, minimal Docker context with the checked-in
helper, then builds from that directory:

```sh
node scripts/runtime-build.cjs packs/chip/images/Dockerfile.rtl-cpu linux/amd64 /tmp/chip-context > /tmp/chip-inputs.json
docker buildx build --platform linux/amd64 -f /tmp/chip-context/packs/chip/images/Dockerfile.rtl-cpu /tmp/chip-context
```

The context directory must not already exist. Optional fourth argument is a JSON
object of reviewed build-argument overrides. Pass those same effective arguments
and platform to Docker. The output records `buildInputDigest`, file hashes/modes,
recipe, effective arguments and immutable external image references. This tracks
checked-in inputs and explicit dependency declarations; it does not poll mutable
APT repositories for updates or promise bit-for-bit reproducible builds. Updating
a dependency requires changing its reviewed declaration/pin. The helper
copies the same bytes it hashes. Build from its output context, not a separately
read or modified checkout; do not modify the prepared context before building.
The checkout's `.dockerignore` does not govern this fresh context, which contains
only enumerated files and the Dockerfile.

The reader intentionally supports the existing recipes' explicit file `COPY`,
image stages, cache mounts and unique literal `ARG` defaults. Automatic platform
arguments, defaultless arguments, expanded defaults and repeated/scoped argument
definitions are rejected rather than guessing builder-dependent values. Broad/directory copies,
local glob/variable sources, `ADD`, bind-mounted inputs and unrecognized syntax
fail rather than silently excluding inputs. All external `FROM`, `COPY --from`
and Dockerfile frontend references must be digest-pinned. Extend the reader and
its tests together when a recipe actually requires additional syntax.

`scripts/runtime-image-reuse.cjs` provides the publisher's `reuseImage` helper.
Its candidate must be a previously qualified release record from a reviewed,
integrity-checked Pack. It validates that record, compares build inputs, platform
and runtime identity, then calls the existing registry artifact verifier to
verify the immutable manifest, actual config platform and referenced blobs.
Qualification references remain attached to that same publication/image; they
are not copied to a different digest and native result samples are not rerun here.
A missing prior release or changed input returns a build decision. Invalid
metadata, missing/corrupt images and registry errors fail the reuse check; they
never count as permission to reuse. Existing Docker credentials may be used
explicitly for a private registry; credentials are not recorded in the descriptor.

The publishing Action uses these helpers; by themselves they do not publish
images, create Tools, write release locks or change Service configuration.


## Tencent publishing Action configuration

`publish-runtime.yml` is separate from the legacy GHCR/local-development image
release workflow. A repository maintainer selects `chip`, `freecad` or `pcb`, and
`test` or `prod`. Test candidates can come from trusted repository feature
branches; prod candidates must come from `main`. It never deploys the Service,
changes its Pack pin, deletes a Tool or changes an existing Tool's image.
Configure GitHub Environments named `test` and `prod`, with branch restrictions
and reviewers appropriate to the credentials. Only trusted maintainers may run
credentialed candidate code. Fork PRs have no publication trigger. Protect prod
with a main-only Environment rule as well as the workflow check.

Environment secrets:

| Name | Purpose |
| --- | --- |
| `TENCENT_SECRET_ID`, `TENCENT_SECRET_KEY` | Dedicated CAM identity for AGS CreateSandboxTool and DescribeSandboxToolList; no deletion permission is used |
| `TENCENT_REGISTRY_USERNAME`, `TENCENT_REGISTRY_PASSWORD` | Push/read credentials for the selected CCR/TCR repository; these are registry login credentials, not assumed to equal the CAM key pair |

Environment variables:

| Name | Purpose |
| --- | --- |
| `TENCENT_REGION` | AGS region, checked into the resulting release record |
| `TENCENT_IMAGE_PREFIX` | Registry host plus namespace, without scheme/tag; the publisher appends `<domain>-<environment>` |
| `TENCENT_TOOL_CONFIGURATION` | Trusted CreateSandboxTool configuration JSON, described below |

Provision the repositories and image-pull role before running. The role must let
AGS pull the private immutable image. The Tool configuration includes
`ToolType: "custom"`, `Persistent: false`, a `DefaultTimeout` expressed in seconds
(e.g. `"300s"`), `RoleArn`, `NetworkConfiguration`, and `CustomConfiguration` with
`ImageRegistryType` (`personal` or `enterprise`). Choose network and resource
settings to match the Service deployment. Do not put credentials in this JSON.
The image field is supplied by the publisher; mounts and computer configurations
are rejected. The publisher does not invent cloud endpoints, role ARNs or
resource settings. Configuration is included in the publication identity and
is strictly checked against cloud readback; unexpected defaults fail closed
until their normalization is reviewed against real cloud evidence.

The Action builds the minimal DPR-02 context with the exact recorded arguments,
pushes to the configured repository and verifies the registry manifest, platform
and blobs by immutable digest. The prepared plan and registry evidence are
uploaded **before** CreateSandboxTool. Upload failure prevents Tool creation.
A stable hash of environment, region, Pack/profile, immutable image and Tool
configuration supplies the API ClientToken and deterministic Tool name. An
existing matching Tool is reused only after ACTIVE/configuration checks. Creation
timeouts are followed by bounded paginated queries, never another create in the
same invocation. Re-running uses the same token/name for the same binding and
queries before creation; it does not generate a fresh token to bypass uncertainty
or quota errors. A collision or mismatched readback fails. The API's unique-name
and idempotency constraints are part of this safety boundary.

Successful result evidence is uploaded before the generated lock is written.
Evidence artifacts have 90-day retention; archive them separately if a longer
retention period is required. Failed or interrupted runs can leave an image or
Tool without a release lock; inspect the run's intent and reconcile the same
identity. There is no automatic cleanup. Repository-wide publication concurrency
avoids competing content-lock writers. Source advancement is still checked at
writeback: a compare-and-swap push accepts only the publication commit whose
single parent is source A, and only while the remote branch still equals A.
Branch protection may reject the bot write; it must not be disabled to force a
release. Generated writes use GITHUB_TOKEN, and explicit-only dispatch prevents
recursive publication.

The generated qualification gates are exactly `registry-integrity`,
`ags-tool-active`, and `ags-tool-configuration`. These are publication checks,
**not native success/failure/cancellation/recovery qualification**. Consumers
must retain their domain qualification requirements; publishing a Tool does
not enable a profile. Real GitHub/Tencent execution remains unqualified until
credentials/configuration and the corresponding cloud gates have been exercised.
The SDK is a development dependency used only by the publisher; cloud keys are
passed only to the Tool step and never enter an image or release record.

API references: [CreateSandboxTool](https://cloud.tencent.com/document/api/1814/124812)
and [Tool/readback structures](https://cloud.tencent.com/document/api/1814/124823).
