const test = require("node:test"),
  assert = require("node:assert/strict");
const { renderSummary } = require("../scripts/runtime-publication-batch.cjs");
test("summary shows immutable images, tool IDs and operations independently of writeback", () => {
  const text = renderSummary(
    [
      {
        domain: "chip",
        environment: "test",
        image: "example.com/rtl@sha256:" + "a".repeat(64),
        toolId: "sdt-example",
        imageOperation: "reused",
        toolOperation: "created",
        status: "published",
      },
      { domain: "pcb", environment: "test", status: "incomplete" },
    ],
    "failure",
  );
  assert.match(text, /sdt-example/);
  assert.match(text, /sha256:/);
  assert.match(text, /reused/);
  assert.match(text, /created/);
  assert.match(text, /incomplete/);
  assert.match(text, /lock writeback: failure/);
});
test("summary cannot turn missing cloud confirmation into a successful publication", () => {
  const text = renderSummary(
    [
      {
        domain: "freecad",
        environment: "prod",
        image: "example.com/cad@sha256:" + "b".repeat(64),
        status: "unconfirmed",
      },
    ],
    "skipped",
  );
  assert.match(text, /unconfirmed/);
  assert.match(text, /skipped/);
  assert.doesNotMatch(text, /published/);
});

test("batch validates source-bound records and rejects incomplete or altered results", (t) => {
  const fs = require("node:fs"),
    os = require("node:os"),
    path = require("node:path");
  const {
    collectResults,
  } = require("../scripts/runtime-publication-batch.cjs");
  const {
    selection,
    publicationConfig,
  } = require("../scripts/publish-runtime.cjs");
  const { inputs } = require("../scripts/runtime-build.cjs");
  const {
    planTool,
    configurationDigest,
  } = require("../scripts/runtime-tool-publication.cjs");
  const { repositoryFor } = require("../scripts/runtime-cloud-image.cjs");
  const pack = require("../lib/index.cjs");
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "publication-batch-"),
  );
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const s = selection("chip", "refs/heads/feature"),
    config = publicationConfig(s),
    source = "a".repeat(40),
    runId = "123",
    roleArn = "role";
  const profile = pack
    .getPack(s.packId)
    .profiles.find((p) => p.id === s.profile);
  const input = inputs({
    root: pack.root,
    recipe: s.recipe,
    platform: profile.platform,
  });
  const tool = planTool({
    environment: s.environment,
    region: config.region,
    packId: s.packId,
    profile: s.profile,
    image: repositoryFor("chip") + "@sha256:" + "b".repeat(64),
    configuration: { ...config.toolConfiguration, RoleArn: roleArn },
  });
  const plan = {
    selection: s,
    source,
    sourceContent: pack.identity.contentSha256,
    input,
    tool,
    relative: "packs/chip/releases/rtl-cpu/test.lock.json",
  };
  const folder = path.join(directory, "runtime-result-chip-" + runId);
  fs.mkdirSync(folder);
  fs.writeFileSync(path.join(folder, "plan.json"), JSON.stringify(plan));
  const options = {
    directory,
    selected: ["chip"],
    ref: "refs/heads/feature",
    source,
    runId,
    roleArn,
  };
  assert.equal(collectResults(options)[0].record, undefined);
  fs.writeFileSync(
    path.join(folder, "result.json"),
    JSON.stringify({
      providerToolId: "sdt-example",
      toolConfigurationDigest: configurationDigest(tool.request),
      toolOperation: "updated",
    }),
  );
  assert.equal(collectResults(options)[0].record.providerToolId, "sdt-example");
  const checked = path.join(directory, "runtime-check-chip-" + runId);
  fs.renameSync(folder, checked);
  const reuseOptions = { ...options, publishedDomains: [] };
  assert.equal(
    collectResults(reuseOptions)[0].record.providerToolId,
    "sdt-example",
  );
  // A partial publish result must supersede earlier check evidence, not hide failure.
  fs.mkdirSync(folder);
  fs.copyFileSync(
    path.join(checked, "plan.json"),
    path.join(folder, "plan.json"),
  );
  assert.equal(collectResults(options)[0].record, undefined);
  // A stale partial result from a previous attempt cannot override fresh reuse.
  assert.equal(
    collectResults(reuseOptions)[0].record.providerToolId,
    "sdt-example",
  );
  fs.rmSync(folder, { recursive: true });
  fs.renameSync(checked, folder);

  assert.equal(
    collectResults({ ...options, selected: ["chip", "pcb"] })[1].record,
    undefined,
  );
  assert.throws(
    () => collectResults({ ...options, source: "c".repeat(40) }),
    /intent/,
  );
  fs.writeFileSync(
    path.join(folder, "result.json"),
    JSON.stringify({
      providerToolId: "sdt-example",
      toolConfigurationDigest: "sha256:" + "f".repeat(64),
    }),
  );
  assert.throws(() => collectResults(options), /configuration differs/);
});
