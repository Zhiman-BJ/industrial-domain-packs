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
