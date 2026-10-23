// Select changed runtime bindings, then commit their records atomically as one
// source-branch child. Each matrix job publishes cloud resources independently.
const fs = require("node:fs"),
  path = require("node:path"),
  { execFileSync } = require("node:child_process");
const {
  selection,
  publicationConfig,
  resumePlan,
  releaseRecord,
} = require("./publish-runtime.cjs");
const { inputs } = require("./runtime-build.cjs");
const { configurationDigest } = require("./runtime-tool-publication.cjs");
const { pushPublication, pushProductionBranch } = require("./runtime-publication-writeback.cjs");
const domains = ["chip", "freecad", "pcb"];
const { repositoryFor } = require("./runtime-cloud-image.cjs");
function collectResults({
  directory,
  selected,
  publishedDomains = selected,
  ref,
  source,
  runId,
  roleArn,
}) {
  const pack = require("../lib/index.cjs");
  pack.verifyIntegrity();
  if (
    !Array.isArray(selected) ||
    !selected.length ||
    new Set(selected).size !== selected.length ||
    selected.some((d) => !domains.includes(d))
  )
    throw Error("Invalid selected domains");
  return selected.map((domain) => {
    const s = selection(domain, ref),
      config = publicationConfig(s),
      profile = pack.getPack(s.packId).profiles.find((p) => p.id === s.profile);
    const row = { domain, environment: s.environment, status: "incomplete" };
    const published = path.join(directory, `runtime-result-${domain}-${runId}`);
    // Follow this check's selected domains, never a stale result from an older attempt.
    const folder = publishedDomains.includes(domain)
      ? published
      : path.join(directory, `runtime-check-${domain}-${runId}`);
    if (!fs.existsSync(path.join(folder, "plan.json"))) {
      if (fs.existsSync(path.join(folder, "image.json"))) {
        row.image = JSON.parse(
          fs.readFileSync(path.join(folder, "image.json")),
        ).reference;
        row.status = "image-only";
      }
      return { row };
    }
    const plan = resumePlan({
      plan: JSON.parse(fs.readFileSync(path.join(folder, "plan.json"))),
      selection: s,
      source,
      sourceContent: pack.identity.contentSha256,
      input: inputs({
        root: pack.root,
        recipe: s.recipe,
        platform: profile.platform,
      }),
      region: config.region,
      configuration: { ...config.toolConfiguration, RoleArn: roleArn },
      repository: repositoryFor(domain),
    });
    row.image = plan.tool.request.CustomConfiguration.Image;
    row.status = "unconfirmed";
    if (fs.existsSync(path.join(folder, "image-operation.json")))
      row.imageOperation = JSON.parse(
        fs.readFileSync(path.join(folder, "image-operation.json")),
      );
    if (!fs.existsSync(path.join(folder, "result.json"))) return { row, plan };
    const result = JSON.parse(
      fs.readFileSync(path.join(folder, "result.json")),
    );
    if (
      result.toolConfigurationDigest !== configurationDigest(plan.tool.request)
    )
      throw Error("Collected Tool configuration differs from intent");
    const record = releaseRecord(
      plan,
      result,
      `https://github.com/${process.env.GITHUB_REPOSITORY}/actions/runs/${runId}`,
    );
    Object.assign(row, {
      toolId: record.providerToolId,
      toolOperation: result.toolOperation,
      status: "published",
    });
    return { row, plan, record };
  });
}
function renderSummary(rows, writeback) {
  const cell = (value) => String(value ?? "—").replace(/[\r\n|<>]/g, " ");
  return (
    "# Remote runtime publication\n\n" +
    "| Domain | Environment | Image (immutable) | Image action | Tool ID | Tool action | Cloud status |\n|---|---|---|---|---|---|---|\n" +
    rows
      .map(
        (r) =>
          "| " +
          [
            r.domain,
            r.environment,
            r.image,
            r.imageOperation,
            r.toolId,
            r.toolOperation,
            r.status,
          ]
            .map(cell)
            .join(" | ") +
          " |",
      )
      .join("\n") +
    `\n\nRelease lock writeback: ${cell(writeback)}. Cloud publication does not deploy the Service or qualify native domain execution.\n`
  );
}
const required = (name) => {
  if (!process.env[name]) throw Error("Missing configuration: " + name);
  return process.env[name];
};
function main(phase) {
  const root = path.resolve(__dirname, "..");
  process.chdir(root);
  const run = (cmd, args, options = {}) =>
    execFileSync(cmd, args, {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "inherit"],
      ...options,
    })?.trim() || "";
  const git = (...args) => run("git", args);
  const ref = process.env.PUBLICATION_REF || required("GITHUB_REF"),
    source = process.env.PUBLICATION_SOURCE || required("GITHUB_SHA"),
    roleArn = required("TENCENT_ROLE_ARN");
  if (phase !== "summary" && git("rev-parse", "HEAD") !== source)
    throw Error("Source commit mismatch");
  const selected = JSON.parse(required("PUBLICATION_DOMAINS"));
  const options = {
    directory: required("PUBLICATION_DIRECTORY"),
    selected,
    publishedDomains: JSON.parse(
      process.env.PUBLICATION_CHANGED_DOMAINS || "[]",
    ),
    ref,
    source,
    runId: required("GITHUB_RUN_ID"),
    roleArn,
  };
  if (phase === "summary") {
    // Isolate invalid/missing artifacts per domain so partial successes stay visible.
    const saved = path.join(options.directory, "summary-rows.json");
    const rows = fs.existsSync(saved)
      ? JSON.parse(fs.readFileSync(saved))
      : selected.map((domain) => {
          try {
            return collectResults({ ...options, selected: [domain] })[0].row;
          } catch {
            return {
              domain,
              environment: selection(domain, ref).environment,
              status: "evidence-invalid",
            };
          }
        });
    fs.appendFileSync(
      required("GITHUB_STEP_SUMMARY"),
      renderSummary(rows, required("PUBLICATION_WRITEBACK_STATUS")),
    );
    return;
  }
  if (phase !== "writeback") throw Error("Expected writeback or summary");
  if (git("status", "--porcelain")) throw Error("Source checkout changed");
  const results = collectResults(options);
  fs.writeFileSync(
    path.join(options.directory, "summary-rows.json"),
    JSON.stringify(results.map((r) => r.row)),
  );
  if (results.some((r) => !r.record))
    throw Error("Batch incomplete; no release locks written");
  for (const { plan, record } of results) {
    fs.mkdirSync(path.dirname(plan.relative), { recursive: true });
    fs.writeFileSync(plan.relative, JSON.stringify(record, null, 2) + "\n");
    git("add", "--", plan.relative);
  }
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
  git("commit", "-m", `chore: publish ${selected.join(", ")} runtimes`);
  if (ref === "refs/heads/main") {
    const branch = pushProductionBranch({cwd:root, source});
    fs.appendFileSync(required("GITHUB_OUTPUT"), `branch=${branch}\nhead=${git("rev-parse", "HEAD")}\n`);
  } else {
    pushPublication({ cwd: root, branch: ref.slice(11), source });
  }
}
module.exports = { collectResults, renderSummary };
if (require.main === module) {
  try {
    main(process.argv[2]);
  } catch (error) {
    console.error("Runtime publication batch failed:", error.message);
    process.exitCode = 1;
  }
}
