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

`getRuntimeRelease(packId, profileId, environment, selection)` reads a release record
from `packs/<source-directory>/releases/<profile>/<test|prod>.lock.json`. The source
directory, domain and target platform come from the pinned catalog, not a caller
path. `selection` must contain `region`, `buildInputDigest`, `runtimeDigest` and
`workerProtocolVersion`, derived independently by a trusted consumer. All supplied
selection fields must match. Extra fields are rejected. The complete Pack integrity
check runs before reading; untracked release files, altered bytes, unsafe paths and
symlinks are rejected. The result is a detached, recursively frozen record.

This is a metadata contract only. No checked-in Tool binding or automated Tencent
publication is provided by this API. It does not query the cloud, prove engineering
qualification, authorize execution, compute actual build inputs or activate profiles.
Consumers must validate the actual build-input identity, Tool configuration and
qualification evidence before enabling execution. A `passed` label is not proof.
Existing local/remote profile APIs and worker execution behavior are unchanged.

Required fields (unknown fields are rejected, including arbitrary launch settings):

| Field | Contract |
| --- | --- |
| schemaVersion / releaseId | `1`; bounded stable lowercase identifier for this publication |
| packId / domain / runtimeProfile | Exact pinned catalog selection |
| environment / region | `test` or `prod`; exact trusted deployment region |
| buildSourceCommit | Full 40-character source Git SHA, commit A used for the build |
| buildSourceContentSha256 | Original A content-lock SHA-256, 64 lowercase hex characters |
| buildInputDigest | `sha256:` plus 64 hex characters; identity of actual build inputs, independently supplied by the consumer |
| runtimeDigest | Existing nonempty opaque runtime identity string; this API introduces no competing runtime hash algorithm |
| image | Exactly `reference` (registry/repository@sha256 digest) and `platform`; no mutable tag, URL scheme or credential authority; platform must match the catalog profile |
| provider / providerToolId | `tencent-ags`; recorded `sdt-...` resource ID, scoped by region |
| toolConfigurationDigest | SHA-256 identity of read-back Tool configuration; cloud read-back is a separate requirement |
| workerProtocolVersion | Positive integer, equal to the consumer-supported version |
| qualification | `status: passed`, nonempty unique `gates` and `evidence` string arrays; the consumer checks the required gates and evidence for its target environment |

Runtime image source identity and consumer publication identity are separate. A
build uses source commit A and its content lock. Writing a release record produces
commit B and a new content lock that covers the record itself. The record does not
embed B or B's content digest, avoiding self-reference. B is pinned externally by
the Service; `buildSourceContentSha256` continues to identify A, not B. Changing
only publication metadata does not by itself prove the image is reusable: the
build-input checker must validate the actual prepared build context and dependencies.
Current Docker recipes copy broad package contents; excluding publication records
from a future build fingerprint without preparing a matching context is unsafe.

The standalone `lib/runtime-release.cjs` validator/reader is for trusted publishing
and testing code. The package `getRuntimeRelease` facade additionally enforces full
package integrity, catalog routing and target platform. Neither accepts paths or
execution settings from an untrusted remote client. Public records must contain no
credentials, customer inputs or private deployment endpoints.
