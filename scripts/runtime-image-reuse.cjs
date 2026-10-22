// Publication records come from a reviewed, integrity-checked Pack, never a client.
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const { validateRuntimeRelease } = require("../lib/runtime-release.cjs");
const run = promisify(execFile);
async function verifyImage(
  image,
  dockerCredentials = false,
  metadataOnly = false,
) {
  const [repository, digest] = image.reference.split("@");
  const slash = repository.indexOf("/");
  const url = `https://${repository.slice(0, slash)}/v2/${repository.slice(slash + 1)}/manifests/${digest}`;
  const args = [
    path.join(__dirname, "verify-release-artifact.py"),
    "image",
    url,
    "--platform",
    image.platform,
  ];
  if (metadataOnly) args.push("--metadata-only");
  if (dockerCredentials) args.push("--docker-credentials");
  const { stdout } = await run("python3", args, {
    maxBuffer: 4 * 1024 * 1024,
    timeout: (metadataOnly ? 3 : 20) * 60 * 1000,
  });
  return JSON.parse(stdout);
}
async function reuseImage({
  input,
  record,
  expected,
  dockerCredentials = false,
  verifyImage: check = verifyImage,
}) {
  if (!record) return { reuse: false, reason: "no-previous-release" };
  const release = validateRuntimeRelease(record, expected);
  if (
    release.buildInputDigest !== input.buildInputDigest ||
    release.image.platform !== input.platform
  )
    return { reuse: false, reason: "build-inputs-changed" };
  const runtimes = Object.entries(input.buildArgs).filter(([name]) =>
    /(?:^|_)RUNTIME_DIGEST$/.test(name),
  );
  if (runtimes.length !== 1 || runtimes[0][1] !== release.runtimeDigest)
    throw Error("Publication runtime identity differs from build arguments");
  // A match refers to the same qualified immutable publication. Do not transfer
  // qualification to a new digest or accept a boolean "image exists" assertion.
  const evidence = await check(release.image, dockerCredentials);
  if (
    evidence?.identity?.digest !== release.image.reference.split("@")[1] ||
    evidence?.identity?.platform !== input.platform
  )
    throw Error("Registry image identity differs from the publication");
  return {
    reuse: true,
    image: release.image,
    qualification: release.qualification,
    evidence,
  };
}
module.exports = { reuseImage, verifyImage };
