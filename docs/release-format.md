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
| buildSourceCommit | Full 40-character candidate source Git SHA, commit A with matching image build inputs |
| buildSourceContentSha256 | Candidate A content-lock SHA-256, 64 lowercase hex characters |
| buildInputDigest | `sha256:` plus 64 hex characters; identity of actual build inputs, computed and validated by the publishing Action |
| runtimeDigest | Existing nonempty opaque runtime identity string; this API introduces no competing runtime hash algorithm |
| image | Exactly `reference` (registry/repository@sha256 digest) and `platform`; no mutable tag, URL scheme or credential authority; platform must match the catalog profile |
| provider / providerToolId | `tencent-ags`; recorded `sdt-...` resource ID, scoped by region |
| toolConfigurationDigest | SHA-256 identity of read-back Tool configuration; cloud read-back is a separate requirement |
| qualification | `status: passed`, nonempty unique `gates` and `evidence` string arrays; the consumer checks the required gates and evidence for its target environment |

Runtime image source identity and consumer publication identity are separate. A
candidate source is commit A with its content lock. The image may have been
prepared locally on another commit with identical build inputs; the input digest
binds that reuse, while these source fields identify the candidate checked by
Actions. Writing a release record produces
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
2. Prepare and fully verify images locally. Trigger the publishing Action on
   candidate source commit A. Validate A's content lock and derive its actual
   build-input fingerprint, then require a matching prepared image in CCR. Create a new Tool for a new runtime image binding and verify
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

`scripts/runtime-cloud-image.cjs` looks up the publisher-owned `input-<full
buildInputDigest>` tag directly in personal CCR. All lookups and pushes stay in
`ccr.ccs.tencentyun.com/industrial-mcp-remote/`, in `chip-rtl`, `freecad-worker`
or `pcb-kicad`. Test and prod share images. A release lock is an output for
consumers, not a prerequisite or cache index for publishing. Existing legacy tags
without a build-input identity cannot prove reuse eligibility. The publisher
resolves a matching tag to an immutable digest. Local preparation verifies its
manifest, actual config platform and every referenced blob. Actions verify only
manifest/config digests and platform, without downloading layers. A missing tag
permits a local build; in Actions it fails with instructions to prepare images
locally. A cloud query error, corrupt image or incomplete response fails publication.
Only trusted publishers should write these input tags; do not retarget them.

## Local image preparation

Run from a clean committed checkout after `npm ci --ignore-scripts`:

```sh
npm run publish:images
```

The script reports all missing environment variables before invoking local tools
or contacting cloud services. Required variables are `TENCENT_SECRET_ID`,
`TENCENT_SECRET_KEY`, `TENCENT_REGISTRY_USERNAME` and `TENCENT_REGISTRY_PASSWORD`.
The CAM pair queries existing images and capacity; the registry pair authenticates
uploads and full verification. No `TENCENT_ROLE_ARN`, test/prod selection or GitHub
token is required locally. Keep values outside version control.

Prerequisites: Node.js 24+, installed npm dependencies, Git, Python 3, a running
Docker engine and Buildx. The script logs into CCR with password-stdin and uses a
reusable `domain-pack-local` docker-container builder, without switching the
user's default builder. Docker's configured credential store manages the login.
On ARM hosts the engine must support building the declared linux/amd64 platform.

All three domains are checked automatically. Matching images are fully verified
and reused. Missing images are built and pushed by digest, with no input tag yet;
after every layer passes digest/size verification, the script promotes the same
manifest to `input-<full buildInputDigest>` and checks tag readback. Verification
failure never publishes a new input tag. The script never creates Tools or
updates release/content locks. Build/upload has a 90-minute bound; full registry
verification has a 20-minute bound and tag promotion has a 3-minute bound.

Local evidence and partial image identities are retained under the repository's
Git common directory at `runtime-image-publications/<source-commit>/<domain>/attempt-*`.
A `running` directory prevents simultaneous preparation from worktrees sharing
that Git directory. After a hard kill, inspect its owner record and confirm the
process has stopped before removing a stale lock. Coordinate publication from
one machine at a time; this local lock is not cross-machine coordination.

Prepare images before starting the feature test Action or merging to main.
Images are shared across environments, and generated release-lock commits do not
change build inputs. If an Action starts before image preparation completes,
finish preparation and rerun that Action. The existing namespace and input tags
remain the handoff; no second image-version lock is introduced.

## Tencent publishing Action configuration

`Publish remote runtime` runs automatically on pushes to `main` (including PR
merges), using prod configuration. Trusted feature branches publish test through
manual dispatch, with no domain or environment inputs. Manual dispatch on `main`
also permits retrying prod publication. The `Check` matrix inspects chip/freecad/pcb build inputs and cloud resources
without creating or updating them. A selection job collects those checks and
creates a `Publish` matrix containing only packs that need Tool creation/resource
updates or reconciliation of a pending Tool. Missing images stop Check; Actions
never build or push images. When everything
is reusable, the entire Publish job is skipped. Publish rechecks cloud state
before mutations; a check result is not a reservation. Unchanged resources are reused, not rebuilt. Shared worker changes
naturally change every affected domain's build hash. Tool-only configuration
changes do not change image build inputs. Publication does not deploy Service.

Configuration lives in `publication/test.cjs` and `publication/prod.cjs`, one
file per environment with all domains under `domains`. Each uses native Node.js
`module.exports` and exports plain data only; comments are supported, but do not
put imports, environment reads or runtime logic in these files. No YAML parser or
new configuration dependency is required. Existing Ajv validates the exported
object, rejecting unknown fields, missing domains, wrong profiles and invalid
types before publishing. Required root fields are `schemaVersion`, `region` and
`domains`; each domain declares `profile`, `networkMode`, `command`, `args`,
`ports`, `resources` and `probe`. CPU and memory quantities remain strings (for
example `cpu: "2"`, `memory: "2Gi"`); port numbers and probe timings are numbers.
The publisher converts these fields into Tencent API request objects in memory.

Both files are covered by the content lock and included in the package inventory.
Integrity verification happens before requiring a configuration file. Ordinary
Pack consumers verify its bytes but do not execute it. Publication configuration
is not an image build input; changing resource values does not rebuild images.
The namespace, custom Tool type, registry type, nonpersistent setting and Tool
fallback timeout remain fixed in publishing code. Credentials never belong in
these files. Test currently uses Guangzhou; prod uses Beijing. Personal CCR is
queried in Guangzhou independently of the AGS region.

Repository secrets:

| Name | Purpose |
| --- | --- |
| `TENCENT_SECRET_ID`, `TENCENT_SECRET_KEY` | CAM identity for AGS create/list/update and read-only CCR image/quota queries |
| `TENCENT_REGISTRY_USERNAME`, `TENCENT_REGISTRY_PASSWORD` | Read private CCR manifest/config metadata; still required even though Actions no longer upload |

Repository variable `TENCENT_ROLE_ARN` supplies the existing AGS image-pull role.
GitHub Environments are not required. Only trusted maintainers may run candidate
code with repository credentials; fork PRs have no publication trigger. Registry
repositories and the pull role must already exist. Tool type is `custom`,
`Persistent` is false. The publisher generates Tool names and immutable image
references. Mounts and computer configurations are rejected.

| Change | Image | Tool |
| --- | --- | --- |
| CPU, memory or disk size | Reuse if build inputs match | Update existing Tool and verify readback |
| Command/Args, Ports, Probe, network or pull role | Reuse if build inputs match | New Tool identity |
| Dockerfile, copied code, declared dependencies, build arguments/platform | Local preparation builds/verifies missing image first | New Tool if image digest changes |
| No changes | Reuse | Reuse ACTIVE matching Tool |

Tool `DefaultTimeout` is a fixed creation fallback (Chip 1800s, CAD/PCB 900s),
not an editable publication setting. Service explicitly supplies instance Timeout;
that instance lifetime and worker execution timeout are separate policies.
The lock records actual Tool configuration, not a pretend update of its default.

CPU/memory/disk sizes are excluded from the stable Tool name identity; the full
configuration remains in the publication identity, ClientToken and lock digest.
All other settings, image, environment, region and Pack/profile remain in the
stable name identity. Before updating, readback must prove that only the allowed
resource fields differ. Updates persist intent first, issue at most one update
per invocation, and reconcile uncertain responses by querying. They never change
the Tool image. Resource updates affect newly created instances; existing running
instances retained their previous resources in the 2026-10-22 tests. **Propagation
is not atomic:** an instance started immediately after a disk update still used
the old size; a separate test waiting 15 seconds observed the new size. Fifteen
seconds is an observation, not a guaranteed maximum propagation delay. Tool
readback qualification does not promise instantaneous instance resource changes.
See [validation evidence](migration-validation.md).

Before Tool publication, retries for the same source/domain/environment restore the
prior uploaded plan and reverify its exact immutable image. Two test branches at
the same source commit may reuse that intent; only the writeback branch is rebound,
while source, environment, build inputs and full cloud configuration remain checked. Intent is uploaded
before Tool creation/update. If an earlier cloud step ran but intent has expired
or disappeared, fail for reconciliation rather than continuing an uncertain
publication. Retain workflow history and intent artifacts while unresolved.
Artifacts have 90-day retention. Cloud operations use bounded polling; no blind
create/update replay occurs after a timeout. Name collisions or other drift fail.

Check jobs upload verified reuse results and publication decisions. Publish jobs
upload results even on partial failure. The final job uses publication results
for packs selected by the current checks and check results for unchanged packs; a partial publication never
falls back to earlier successful check evidence. The final job collects and
validates every domain's evidence, writes all release locks together, computes
the final content lock, runs checks, and makes one source-branch writeback commit.
Any domain failure prevents the batch writeback. A compare-and-swap push requires
that the source branch still equals the build source. Branch protection is not
bypassed. Repository-wide workflow concurrency serializes publication, and
the writeback uses checkout's repository `GITHUB_TOKEN`, whose pushes do not
trigger another workflow run ([GitHub documentation](https://docs.github.com/en/actions/concepts/security/github_token)).
Keep this checkout on `GITHUB_TOKEN`; switching it to a PAT would remove that
loop prevention.

The Action Summary lists immutable image references, Tool IDs, reused/restored
images, created/updated/reused Tools and cloud status separately from
lock writeback. Local image preparation reports built/reused images and immutable digests.
Missing images, metadata verification errors and Tool failures fail the Action; partial resources are retained
for reconciliation, not automatically deleted. Unconfirmed Tool outcomes never
produce a successful release lock. Cloud error-code detail after uncertain Tool
calls remains a diagnostic limitation.

New Action qualification gates are `registry-metadata`, `ags-tool-active`, and
`ags-tool-configuration`; these are **not native domain qualification**. Local full-layer verification and Action metadata verification are separate checks;
`registry-metadata` does not claim that the Action downloaded layers. The split
GitHub/Tencent publishing workflow still needs real execution qualification.
Publisher SDKs are development dependencies. Cloud credentials are passed only
to cloud-query/Tool steps, never image build arguments or release records.

API references: [CreateSandboxTool](https://cloud.tencent.com/document/api/1814/124812),
[UpdateSandboxTool](https://cloud.tencent.com/document/api/1814/124809),
[Tool/readback structures](https://cloud.tencent.com/document/api/1814/124823).

### Image capacity advisory

After building or reusing an image, publication queries the Guangzhou personal
CCR repository tag count and the account's actual per-repository tag quota.
Only `ccr.ccs.tencentyun.com/industrial-mcp-remote/` repositories are queried.
The Action Summary reports used, maximum and remaining tag counts; usage at or
above 80% also emits a GitHub Actions warning annotation. Query failures emit an
explicit unknown-capacity warning rather than failing the publication. This is
an advisory check, not a reservation or an automatic deletion policy. Images are
shared by test/prod; any future cleanup must protect references from both.

### PR image readiness gate

`Runtime images ready` runs for every PR targeting main, on GitHub's candidate
merge commit. It computes all three domains' actual build inputs, locates their
input tags only in `industrial-mcp-remote`, and verifies immutable manifest/config
identity and platform without downloading layers. It creates no Tools and writes
no locks. Documentation or Tool-only changes reuse images when build inputs match.
A missing image, registry error or invalid metadata fails the check. Run
`npm run publish:images` locally on the candidate source, then rerun the failed
check. If main changed the combined build inputs, update the branch from main
before local preparation. Local input tags are promoted only after full blob
verification; they are a trusted-publisher convention, not a signed attestation.

A repository administrator must configure an active main Ruleset (or branch
protection) requiring `Runtime images ready` from GitHub Actions, with branches
required to be up to date before merging. Preserve existing required checks,
including `Architecture contract`. Do not grant routine bypass. Adding the workflow
alone does not enforce merging restrictions. The implementing account has write
access but no administration access, so this required-check setting is pending
administrator configuration.

This is an ordinary `pull_request` workflow: same-repository contributors are
trusted with repository Secrets, as in feature publication. Fork and Dependabot
PRs without cloud Secrets fail closed; a maintainer must review and move accepted
changes to a trusted repository branch for this check. Do not use
`pull_request_target` to execute untrusted candidate code with cloud credentials.
Publication after merge still rechecks cloud state because images can disappear
after PR validation.

Local tag promotion waits up to 60 seconds for the cloud listing to expose the
new input tag, polling every five seconds. A different digest or query failure
still fails immediately; an absent tag after the deadline fails for a later retry.
