const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "..");
const hash = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const lock = require("../content-lock.json");

function verifyIntegrity() {
  for (const [name, expected] of Object.entries(lock.files)) {
    if (
      !/^(?:packs|lib)\/[a-zA-Z0-9._/-]+$/.test(name) ||
      name.split("/").includes("..")
    )
      throw Error("Unsafe Domain Pack content path.");
    let current = root;
    for (const part of name.split("/")) {
      current = path.join(current, part);
      if (fs.lstatSync(current).isSymbolicLink())
        throw Error("Domain Pack content cannot be a symlink.");
    }
    if (
      !fs.statSync(current).isFile() ||
      hash(fs.readFileSync(current)) !== expected
    )
      throw Error(
        "Domain Pack content differs from its pinned release: " + name,
      );
  }
  if (hash(JSON.stringify(lock.files)) !== lock.contentSha256)
    throw Error("Invalid Domain Pack content identity.");
  return {
    packageVersion: require("../package.json").version,
    contentSha256: lock.contentSha256,
  };
}
const identity = Object.freeze(verifyIntegrity());
const catalog = Object.freeze(
  ["chip", "godot", "pcb", "freecad", "cad", "cuda"].map((name) => ({
    ...require(`../packs/${name}/pack.json`),
    contentSha256: identity.contentSha256,
  })),
);
function getPack(id) {
  const pack = catalog.find((item) => item.id === id);
  if (!pack) throw Error("Unknown Domain Pack: " + id);
  return pack;
}
function getSandboxProfile(packId, profileId) {
  const pack = getPack(packId);
  const profile = pack.profiles.find((item) => item.id === profileId);
  if (!profile?.entry || !profile.artifacts)
    throw Error("Pack has no sandbox execution profile.");
  const { descriptor, verifier } = require(
    path.join(root, profile.canonicalEntry),
  );
  return Object.freeze({
    ...profile,
    packId,
    identity,
    descriptor,
    verifier,
    inventory: {
      domain: pack.domain,
      tools: [descriptor],
      packId,
      profileId,
      packIdentity: identity,
      inputProfile: profile.inputProfile,
    },
  });
}
// Only published locks covered by this package's integrity manifest are readable.
// Build identities are publisher-owned metadata; consumers select routing and protocol.
function getRuntimeRelease(packId, profileId, environment, selection) {
  verifyIntegrity();
  const pack = getPack(packId);
  const profile = pack.profiles.find((item) => item.id === profileId);
  if (!profile?.canonicalEntry || !profile.entry || !profile.artifacts)
    throw Error("Pack has no sandbox execution profile.");
  const match = /^packs\/([a-z0-9][a-z0-9_-]*)\//.exec(profile.canonicalEntry);
  if (!match) throw Error("Invalid Pack profile source path.");
  const selectionFields = [
    "region",
    "workerProtocolVersion",
  ];
  if (
    !selection ||
    typeof selection !== "object" ||
    Array.isArray(selection) ||
    Object.keys(selection).length !== selectionFields.length ||
    selectionFields.some((key) => !Object.hasOwn(selection, key))
  )
    throw Error("Invalid runtime release: trusted selection fields");
  const { readRuntimeRelease } = require("./runtime-release.cjs");
  const record = readRuntimeRelease({
    root,
    contentLock: lock,
    relativePath: `packs/${match[1]}/releases/${profileId}/${environment}.lock.json`,
    expected: {
      ...selection,
      packId,
      domain: pack.domain,
      runtimeProfile: profileId,
      environment,
    },
  });
  if (record.image.platform !== profile.platform)
    throw Error("Runtime release platform differs from the pinned profile.");
  return record;
}

module.exports = {
  root,
  identity,
  catalog,
  getPack,
  getSandboxProfile,
  getRuntimeRelease,
  verifyIntegrity,
  ...require("./consumer.cjs"),
};
