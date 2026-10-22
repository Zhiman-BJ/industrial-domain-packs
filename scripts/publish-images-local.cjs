// Network-heavy image preparation only. Tool publication and lock writes belong to Actions.
const fs = require("node:fs"),
  path = require("node:path");
const { execFileSync } = require("node:child_process");
const names = [
  "TENCENT_SECRET_ID",
  "TENCENT_SECRET_KEY",
  "TENCENT_REGISTRY_USERNAME",
  "TENCENT_REGISTRY_PASSWORD",
];
function validateEnvironment(env) {
  const missing = names.filter((name) => !env[name]?.trim());
  if (missing.length)
    throw Error("Missing environment variables: " + missing.join(", "));
}
function run(cmd, args, options = {}) {
  return (
    execFileSync(cmd, args, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "inherit"],
      ...options,
    })?.trim() || ""
  );
}
const write = (file, value) =>
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + "\n");
function verifyIdentity(image, evidence) {
  if (
    evidence?.identity?.digest !== image.reference.split("@")[1] ||
    evidence?.identity?.platform !== image.platform ||
    evidence?.identity?.verification !== "full-blobs"
  )
    throw Error("Full registry verification did not match the image");
}
async function publishImage(
  { repository, input, directory, recipe },
  dependencies = {},
) {
  const find =
    dependencies.find || require("./runtime-cloud-image.cjs").findImage;
  const verify =
    dependencies.verify || require("./runtime-image-reuse.cjs").verifyImage;
  const execute = dependencies.execute || run;
  let image = await find({ repository, input });
  const reused = Boolean(image);
  if (!image) {
    const metadata = path.join(directory, "build-metadata.json");
    const args = [
      "buildx",
      "build",
      "--builder",
      "domain-pack-local",
      "--platform",
      input.platform,
      "--provenance=false",
      "--sbom=false",
      "--progress=plain",
      "--output",
      `type=image,name=${repository},push=true,push-by-digest=true`,
      "--metadata-file",
      metadata,
      "-f",
      path.join(directory, "context", recipe),
    ];
    for (const [key, value] of Object.entries(input.buildArgs))
      args.push("--build-arg", `${key}=${value}`);
    args.push(path.join(directory, "context"));
    execute("docker", args, { stdio: "inherit", timeout: 90 * 60 * 1000 });
    const hash = JSON.parse(fs.readFileSync(metadata))["containerimage.digest"];
    if (!/^sha256:[a-f0-9]{64}$/.test(hash))
      throw Error("Missing immutable image digest");
    image = { reference: repository + "@" + hash, platform: input.platform };
  }
  // Retain partial results for diagnosis, but do not expose a new input tag yet.
  write(path.join(directory, "image.json"), image);
  const evidence = await verify(image, true);
  verifyIdentity(image, evidence);
  write(path.join(directory, "registry-full.json"), evidence);
  if (!reused) {
    // A concurrent producer must never silently replace another image for this input.
    const concurrent = await find({ repository, input });
    if (concurrent && concurrent.reference !== image.reference)
      throw Error(
        "Input tag appeared with a different image; reconcile before promotion",
      );
    if (!concurrent)
      execute(
        "docker",
        [
          "buildx",
          "imagetools",
          "create",
          "--prefer-index=false",
          "--tag",
          repository + ":input-" + input.buildInputDigest.slice(7),
          image.reference,
        ],
        { stdio: "inherit", timeout: 3 * 60 * 1000 },
      );
    const published = await find({ repository, input });
    if (published?.reference !== image.reference)
      throw Error(
        "Promoted image tag readback mismatch; retry after registry propagation",
      );
  }
  return { image, operation: reused ? "reused" : "built" };
}
function attemptDirectory(storage, source, domain) {
  const parent = path.join(storage, source, domain);
  fs.mkdirSync(parent, { recursive: true });
  return fs.mkdtempSync(path.join(parent, "attempt-"));
}
async function main() {
  validateEnvironment(process.env); // Deliberately before Git, Docker, dependencies or cloud calls.
  if (process.argv.length !== 2)
    throw Error(
      "Usage: npm run publish:images (all domains; no environment argument)",
    );
  const root = path.resolve(__dirname, "..");
  process.chdir(root);
  if (Number(process.versions.node.split(".")[0]) < 24)
    throw Error("Node.js 24 or newer is required");
  for (const [cmd, args] of [
    ["git", ["--version"]],
    ["python3", ["--version"]],
    ["docker", ["buildx", "version"]],
    ["docker", ["info", "--format", "{{.ServerVersion}}"]],
  ]) {
    try {
      run(cmd, args, { timeout: 15000 });
    } catch {
      throw Error(
        "Required local tool unavailable: " + cmd + " " + args.join(" "),
      );
    }
  }
  if (run("git", ["status", "--porcelain"]))
    throw Error("Image source must be a clean committed checkout");
  const source = run("git", ["rev-parse", "HEAD"]);
  const pack = require("../lib/index.cjs");
  pack.verifyIntegrity();
  run("npm", ["run", "lock:check"]);
  const { recipes } = require("./publish-runtime.cjs");
  const { prepareContext } = require("./runtime-build.cjs");
  const { repositoryFor, tcrClient } = require("./runtime-cloud-image.cjs");
  const { checkCapacity } = require("./runtime-image-capacity.cjs");
  const storage = path.resolve(
    root,
    run("git", ["rev-parse", "--git-common-dir"]),
    "runtime-image-publications",
  );
  fs.mkdirSync(storage, { recursive: true, mode: 0o700 });
  const lock = path.join(storage, "running");
  try {
    fs.mkdirSync(lock);
  } catch {
    throw Error(
      "Another local image publication may be running. Inspect " +
        lock +
        " before removing a stale lock",
    );
  }
  write(path.join(lock, "owner.json"), { pid: process.pid, source });
  try {
    // Password travels on stdin, never in argv or diagnostic output.
    run(
      "docker",
      [
        "login",
        "ccr.ccs.tencentyun.com",
        "--username",
        process.env.TENCENT_REGISTRY_USERNAME,
        "--password-stdin",
      ],
      {
        input: process.env.TENCENT_REGISTRY_PASSWORD,
        stdio: ["pipe", "pipe", "pipe"],
        timeout: 30000,
      },
    );
    let builder;
    try {
      builder = run("docker", ["buildx", "inspect", "domain-pack-local"], {
        stdio: ["ignore", "pipe", "pipe"],
        timeout: 30000,
      });
    } catch {
      run(
        "docker",
        [
          "buildx",
          "create",
          "--name",
          "domain-pack-local",
          "--driver",
          "docker-container",
        ],
        { timeout: 30000 },
      );
      builder = run("docker", ["buildx", "inspect", "domain-pack-local"], {
        timeout: 30000,
      });
    }
    if (!/^Driver:\s+docker-container\s*$/m.test(builder))
      throw Error("domain-pack-local builder must use docker-container");
    for (const [domain, [packId, , profileId, dockerfile]] of Object.entries(
      recipes,
    )) {
      if (
        run("git", ["rev-parse", "HEAD"]) !== source ||
        run("git", ["status", "--porcelain"])
      )
        throw Error("Source changed during image publication");
      const directory = attemptDirectory(storage, source, domain);
      const recipe = `packs/${domain}/images/${dockerfile}`;
      const profile = pack
        .getPack(packId)
        .profiles.find((p) => p.id === profileId);
      const input = prepareContext(
        { root, recipe, platform: profile.platform },
        path.join(directory, "context"),
      );
      console.log(`Checking ${domain}: ${input.buildInputDigest}`);
      const result = await publishImage({
        repository: repositoryFor(domain),
        input,
        directory,
        recipe,
      });
      console.log(`${domain}: ${result.operation} ${result.image.reference}`);
      try {
        const c = await checkCapacity({
          image: result.image.reference,
          client: tcrClient(),
        });
        console.log(
          `${c.warning ? "WARNING: " : ""}${c.repository}: ${c.used}/${c.limit} tags, ${c.remaining} remaining`,
        );
      } catch {
        console.warn("WARNING: image capacity unavailable");
      }
    }
    console.log(
      "Images verified and available to the Tool publication Action. No release locks were changed.",
    );
  } finally {
    fs.rmSync(lock, { recursive: true, force: true });
  }
}
module.exports = { validateEnvironment, publishImage, attemptDirectory };
if (require.main === module)
  main().catch((error) => {
    // Never print subprocess objects (which may contain password-stdin) or SDK requests.
    console.error(
      "Local image publication failed: " +
        (error.status !== undefined || error.signal
          ? "subprocess failed; inspect the preceding output"
          : error.code || error.message),
    );
    process.exitCode = 1;
  });
