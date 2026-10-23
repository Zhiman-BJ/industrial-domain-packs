const fs = require("node:fs"),
  path = require("node:path");
const { execFileSync } = require("node:child_process");
const { inputs } = require("./runtime-build.cjs");
const { consumeEvidence } = require("./runtime-image-evidence.cjs");
const { verifyImage } = require("./runtime-image-reuse.cjs");
const {
  planTool,
  ensureTool,
  checkTool,
  digest,
  configurationDigest,
} = require("./runtime-tool-publication.cjs");
const { validateRuntimeRelease } = require("../lib/runtime-release.cjs");
const { findImage, repositoryFor } = require("./runtime-cloud-image.cjs");
const recipes = {
  chip: ["chip-pack", "chip", "rtl-cpu", "Dockerfile.rtl-cpu"],
  freecad: ["freecad-pack", "cad", "freecad-linux-cpu", "Dockerfile.linux-cpu"],
  pcb: ["pcb-pack", "pcb", "pcb-verify-cpu", "Dockerfile.pcb-verify"],
};
function selection(domain, ref) {
  if (!Object.hasOwn(recipes, domain))
    throw Error("Unknown publication domain");
  if (!ref?.startsWith("refs/heads/"))
    throw Error("Publication requires a source branch");
  const environment = ref === "refs/heads/main" ? "prod" : "test";
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
function publicationConfig(s) {
  const pack = require("../lib/index.cjs");
  pack.verifyIntegrity();
  if (!["test", "prod"].includes(s.environment))
    throw Error("Invalid publication environment");
  const relative = `publication/${s.environment}.cjs`;
  if (!Object.hasOwn(require("../content-lock.json").files, relative))
    throw Error("Publication configuration is not covered by content lock");
  return require("./runtime-publication-config.cjs").buildPublicationConfig(
    require(path.join(pack.root, relative)),
    s,
  );
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
        reference: plan.tool.request.CustomConfiguration.Image.replace(
          /:[^/:@]+(?=@sha256:)/,
          "",
        ),
        platform: plan.input.platform,
      },
      provider: "tencent-ags",
      providerToolId: result.providerToolId,
      toolConfigurationDigest: result.toolConfigurationDigest,
      qualification: {
        status: "passed",
        gates: [
          "registry-metadata",
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
  if (
    typeof image !== "string" ||
    !image.startsWith(
      repository + ":input-" + input.buildInputDigest.slice(7) + "@",
    )
  )
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
  // Same source on two test branches may share the cloud intent; only the
  // writeback destination changes. All cloud/source/environment fields remain checked.
  const rebound = {
    ...plan,
    selection: { ...plan.selection, branch: s.branch },
  };
  if (digest(expectedPlan) !== digest(rebound))
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
function agsClient(region) {
  const { ags } = require("tencentcloud-sdk-nodejs-ags");
  return new ags.v20250920.Client({
    credential: {
      secretId: required("TENCENT_SECRET_ID"),
      secretKey: required("TENCENT_SECRET_KEY"),
    },
    region,
    profile: {
      httpProfile: { endpoint: "ags.tencentcloudapi.com", reqTimeout: 30 },
    },
  });
}
function checkedImage(candidate) {
  const directory = process.env.RUNTIME_IMAGE_EVIDENCE_DIRECTORY;
  if (!directory) return null;
  try {
    return consumeEvidence(
      JSON.parse(
        fs.readFileSync(path.join(directory, candidate.domain + ".json")),
      ),
      candidate,
      process.env,
    );
  } catch {
    return null;
  }
}
async function checkPlan(plan, dir) {
  const checked = await checkTool(plan.tool, agsClient(plan.tool.region));
  if (checked.result) write(path.join(dir, "result.json"), checked.result);
  write(path.join(dir, "check.json"), {
    domain: plan.selection.directory,
    needsPublication: checked.action !== "reuse",
    toolAction: checked.action,
  });
}
async function main(phase) {
  const root = path.resolve(__dirname, "..");
  process.chdir(root);
  const dir = required("PUBLICATION_DIRECTORY");
  fs.mkdirSync(dir, { recursive: true });
  const planFile = path.join(dir, "plan.json");
  if (phase === "configure") {
    const s = selection(
      required("PUBLICATION_DOMAIN"),
      process.env.PUBLICATION_REF || required("GITHUB_REF"),
    );
    const config = publicationConfig(s);
    fs.appendFileSync(
      required("GITHUB_OUTPUT"),
      `registry-host=${config.imagePrefix.split("/")[0]}\n`,
    );
  } else if (phase === "prepare" || phase === "check") {
    const s = selection(
      required("PUBLICATION_DOMAIN"),
      process.env.PUBLICATION_REF || required("GITHUB_REF"),
    );
    const roleArn = required("TENCENT_ROLE_ARN");
    const config = publicationConfig(s);
    const region = config.region,
      prefix = config.imagePrefix;
    const configuration = { ...config.toolConfiguration, RoleArn: roleArn };
    if (git("status", "--porcelain"))
      throw Error("Publication source must be clean");
    const source = git("rev-parse", "HEAD");
    if (source !== (process.env.PUBLICATION_SOURCE || required("GITHUB_SHA")))
      throw Error("Source commit mismatch");
    const pack = require("../lib/index.cjs");
    pack.verifyIntegrity();
    run("npm", ["run", "lock:check"]);
    const profile = pack
      .getPack(s.packId)
      .profiles.find((p) => p.id === s.profile);
    const input = inputs({
      root,
      recipe: s.recipe,
      platform: profile.platform,
    });
    if (!/^[a-z0-9.-]+\/[a-z0-9/_-]+$/.test(prefix))
      throw Error("Invalid image repository prefix");
    const repository = repositoryFor(s.directory);
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
      const registry =
        checkedImage({ domain: s.directory, repository, input, image })
          ?.registry || (await verifyImage(image, true, true));
      if (
        registry.identity?.digest !== image.reference.split("@")[1] ||
        registry.identity?.platform !== input.platform
      )
        throw Error("Restored image identity mismatch");
      write(planFile, restored);
      write(path.join(dir, "registry.json"), registry);
      write(path.join(dir, "image-operation.json"), "restored");
      if (phase === "check") await checkPlan(restored, dir);
      return;
    }
    const cached = checkedImage({ domain: s.directory, repository, input });
    const image = cached?.image || (await findImage({ repository, input }));
    if (!image)
      throw Error(
        `Missing verified image for ${s.directory}; run npm run publish:images locally on this source before retrying the Action`,
      );
    write(path.join(dir, "image.json"), image);
    write(path.join(dir, "image-operation.json"), "reused");
    const registry = cached?.registry || (await verifyImage(image, true, true));
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
      // AGS personal CCR requires a tag even when the digest is pinned.
      image: image.reference.replace(
        "@",
        ":input-" + input.buildInputDigest.slice(7) + "@",
      ),
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
    write(path.join(dir, "image-operation.json"), "reused");
    if (phase === "check")
      await checkPlan(JSON.parse(fs.readFileSync(planFile)), dir);
  } else if (phase === "tool") {
    const plan = JSON.parse(fs.readFileSync(planFile));
    if (!/^\d+$/.test(required("PUBLICATION_INTENT_ARTIFACT_ID")))
      throw Error("Durably uploaded intent is required");
    const client = agsClient(plan.tool.region);
    const result = await ensureTool(plan.tool, client, {
      persist: async () => {
        /* prior Action step uploaded this exact plan */
      },
    });
    write(path.join(dir, "result.json"), result);
  } else throw Error("Expected configure, check, prepare or tool phase");
}
module.exports = {
  selection,
  publicationConfig,
  releaseRecord,
  resumePlan,
  recipes,
};
if (require.main === module)
  main(process.argv[2]).catch((error) => {
    console.error("Runtime publication failed:", error.code || error.message);
    process.exitCode = 1;
  });
