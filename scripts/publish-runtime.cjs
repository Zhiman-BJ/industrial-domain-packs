const fs = require("node:fs"),
  path = require("node:path");
const { execFileSync } = require("node:child_process");
const { prepareContext } = require("./runtime-build.cjs");
const { reuseImage, verifyImage } = require("./runtime-image-reuse.cjs");
const {
  planTool,
  ensureTool,
  digest,
} = require("./runtime-tool-publication.cjs");
const { validateRuntimeRelease } = require("../lib/runtime-release.cjs");
const { pushPublication } = require("./runtime-publication-writeback.cjs");
const recipes = {
  chip: ["chip-pack", "chip", "rtl-cpu", "Dockerfile.rtl-cpu"],
  freecad: ["freecad-pack", "cad", "freecad-linux-cpu", "Dockerfile.linux-cpu"],
  pcb: ["pcb-pack", "pcb", "pcb-verify-cpu", "Dockerfile.pcb-verify"],
};
function selection(domain, environment, ref) {
  if (!Object.hasOwn(recipes, domain))
    throw Error("Unknown publication domain");
  if (!["test", "prod"].includes(environment))
    throw Error("Unknown publication environment");
  if (!ref?.startsWith("refs/heads/"))
    throw Error("Publication requires a source branch");
  if (environment === "prod" && ref !== "refs/heads/main")
    throw Error("Production publication requires main");
  const [packId, factDomain, profile, recipe] = recipes[domain];
  return {
    directory: domain,
    packId,
    domain: factDomain,
    profile,
    environment,
    branch: ref.slice(11),
    recipe: `packs/${domain}/images/${recipe}`,
  };
}
function expected(s) {
  return {
    packId: s.packId,
    domain: s.domain,
    runtimeProfile: s.profile,
    environment: s.environment,
  };
}
function releaseRecord(plan, result, evidence) {
  const runtimes = Object.entries(plan.input.buildArgs).filter(([k]) =>
    /(?:^|_)RUNTIME_DIGEST$/.test(k),
  );
  if (runtimes.length !== 1)
    throw Error("Exactly one runtime identity is required");
  return validateRuntimeRelease(
    {
      schemaVersion: 1,
      releaseId: "pub-" + plan.tool.identity.slice(7),
      ...expected(plan.selection),
      region: plan.tool.region,
      buildSourceCommit: plan.source,
      buildSourceContentSha256: plan.sourceContent,
      buildInputDigest: plan.input.buildInputDigest,
      runtimeDigest: runtimes[0][1],
      image: {
        reference: plan.tool.request.CustomConfiguration.Image,
        platform: plan.input.platform,
      },
      provider: "tencent-ags",
      ...result,
      qualification: {
        status: "passed",
        gates: [
          "registry-integrity",
          "ags-tool-active",
          "ags-tool-configuration",
        ],
        evidence: [evidence],
      },
    },
    expected(plan.selection),
  );
}
function resumePlan({
  plan,
  selection: s,
  source,
  sourceContent,
  input,
  region,
  configuration,
  repository,
}) {
  const image = plan.tool?.request?.CustomConfiguration?.Image;
  if (typeof image !== "string" || !image.startsWith(repository + "@"))
    throw Error("Prior intent repository mismatch");
  const tool = planTool({
    environment: s.environment,
    region,
    packId: s.packId,
    profile: s.profile,
    image,
    configuration,
  });
  const expectedPlan = {
    selection: s,
    source,
    sourceContent,
    input,
    tool,
    relative: `packs/${s.directory}/releases/${s.profile}/${s.environment}.lock.json`,
  };
  if (digest(expectedPlan) !== digest(plan))
    throw Error(
      "Prior intent differs from source, build inputs or environment configuration; reconcile before publishing",
    );
  return expectedPlan;
}
const run = (cmd, args, options = {}) =>
  execFileSync(cmd, args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"],
    ...options,
  })?.trim() || "";
const git = (...args) => run("git", args);
const write = (file, value) =>
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + "\n");
function required(name) {
  if (!process.env[name]) throw Error("Missing configuration: " + name);
  return process.env[name];
}
async function main(phase) {
  const root = path.resolve(__dirname, "..");
  process.chdir(root);
  const dir = required("PUBLICATION_DIRECTORY");
  fs.mkdirSync(dir, { recursive: true });
  const planFile = path.join(dir, "plan.json");
  if (phase === "prepare") {
    const s = selection(
      required("PUBLICATION_DOMAIN"),
      required("PUBLICATION_ENVIRONMENT"),
      required("GITHUB_REF"),
    );
    const region = required("TENCENT_REGION");
    const configuration = JSON.parse(required("TENCENT_TOOL_CONFIGURATION"));
    const prefix = required("TENCENT_IMAGE_PREFIX");
    if (git("status", "--porcelain"))
      throw Error("Publication source must be clean");
    const source = git("rev-parse", "HEAD");
    if (source !== required("GITHUB_SHA"))
      throw Error("Source commit mismatch");
    const pack = require("../lib/index.cjs");
    pack.verifyIntegrity();
    run("npm", ["run", "lock:check"]);
    const profile = pack
      .getPack(s.packId)
      .profiles.find((p) => p.id === s.profile);
    const input = prepareContext(
      { root, recipe: s.recipe, platform: profile.platform },
      path.join(dir, "context"),
    );
    if (!/^[a-z0-9.-]+\/[a-z0-9/_-]+$/.test(prefix))
      throw Error("Invalid image repository prefix");
    const repository = prefix + "/" + s.directory + "-" + s.environment;
    const relative = `packs/${s.directory}/releases/${s.profile}/${s.environment}.lock.json`;
    if (fs.existsSync(planFile)) {
      const restored = resumePlan({
        plan: JSON.parse(fs.readFileSync(planFile)),
        selection: s,
        source,
        sourceContent: pack.identity.contentSha256,
        input,
        region,
        configuration,
        repository,
      });
      const image = {
        reference: restored.tool.request.CustomConfiguration.Image,
        platform: input.platform,
      };
      const registry = await verifyImage(image, true);
      if (
        registry.identity?.digest !== image.reference.split("@")[1] ||
        registry.identity?.platform !== input.platform
      )
        throw Error("Restored image identity mismatch");
      write(planFile, restored);
      write(path.join(dir, "registry.json"), registry);
      return;
    }
    let record = null;
    if (fs.existsSync(relative))
      record = pack.getRuntimeRelease(s.packId, s.profile, s.environment);
    // Changed deployment region/repository cannot borrow a previous binding.
    if (
      record &&
      (record.region !== region ||
        !record.image.reference.startsWith(repository + "@"))
    )
      record = null;
    const reuse = await reuseImage({
      input,
      record,
      expected: expected(s),
      dockerCredentials: true,
    });
    let image = reuse.image;
    if (!reuse.reuse) {
      const tag = repository + ":input-" + input.buildInputDigest.slice(7);
      const metadata = path.join(dir, "build-metadata.json");
      const args = [
        "buildx",
        "build",
        "--platform",
        input.platform,
        "--provenance=false",
        "--sbom=false",
        "--push",
        "--tag",
        tag,
        "--metadata-file",
        metadata,
        "-f",
        path.join(dir, "context", s.recipe),
      ];
      for (const [key, value] of Object.entries(input.buildArgs))
        args.push("--build-arg", `${key}=${value}`);
      args.push(path.join(dir, "context"));
      run("docker", args, { stdio: "inherit" });
      const hash = JSON.parse(fs.readFileSync(metadata))[
        "containerimage.digest"
      ];
      if (!/^sha256:[a-f0-9]{64}$/.test(hash))
        throw Error("Missing immutable build digest");
      image = { reference: repository + "@" + hash, platform: input.platform };
    }
    const registry = await verifyImage(image, true);
    if (
      registry.identity?.digest !== image.reference.split("@")[1] ||
      registry.identity?.platform !== input.platform
    )
      throw Error("Registry identity mismatch");
    const tool = planTool({
      environment: s.environment,
      region,
      packId: s.packId,
      profile: s.profile,
      image: image.reference,
      configuration,
    });
    write(planFile, {
      selection: s,
      source,
      sourceContent: pack.identity.contentSha256,
      input,
      tool,
      relative,
    });
    write(path.join(dir, "registry.json"), registry);
  } else if (phase === "tool") {
    const plan = JSON.parse(fs.readFileSync(planFile));
    if (!/^\d+$/.test(required("PUBLICATION_INTENT_ARTIFACT_ID")))
      throw Error("Durably uploaded intent is required");
    const { ags } = require("tencentcloud-sdk-nodejs-ags");
    const client = new ags.v20250920.Client({
      credential: {
        secretId: required("TENCENT_SECRET_ID"),
        secretKey: required("TENCENT_SECRET_KEY"),
      },
      region: plan.tool.region,
      profile: {
        httpProfile: { endpoint: "ags.tencentcloudapi.com", reqTimeout: 30 },
      },
    });
    const result = await ensureTool(plan.tool, client, {
      persist: async () => {
        /* prior Action step uploaded this exact plan */
      },
    });
    write(path.join(dir, "result.json"), result);
  } else if (phase === "writeback") {
    const plan = JSON.parse(fs.readFileSync(planFile));
    if (
      git("rev-parse", "HEAD") !== plan.source ||
      git("status", "--porcelain")
    )
      throw Error("Source checkout changed");
    const result = JSON.parse(fs.readFileSync(path.join(dir, "result.json")));
    const evidence = `${required("GITHUB_SERVER_URL")}/${required("GITHUB_REPOSITORY")}/actions/runs/${required("GITHUB_RUN_ID")}`;
    const record = releaseRecord(plan, result, evidence);
    fs.mkdirSync(path.dirname(plan.relative), { recursive: true });
    write(plan.relative, record);
    git("add", "--", plan.relative);
    run("npm", ["run", "lock"]);
    run("npm", ["run", "lock:check"]);
    run("npm", ["test"], { stdio: "inherit" });
    git("add", "--", "content-lock.json", "package.json");
    git("config", "user.name", "github-actions[bot]");
    git(
      "config",
      "user.email",
      "41898282+github-actions[bot]@users.noreply.github.com",
    );
    git(
      "commit",
      "-m",
      `chore: publish ${plan.selection.directory} ${plan.selection.environment} runtime`,
    );
    pushPublication({
      cwd: root,
      branch: plan.selection.branch,
      source: plan.source,
    });
  } else throw Error("Expected prepare, tool or writeback phase");
}
module.exports = { selection, releaseRecord, resumePlan };
if (require.main === module)
  main(process.argv[2]).catch((error) => {
    console.error("Runtime publication failed:", error.code || error.message);
    process.exitCode = 1;
  });
