const test = require("node:test"),
  assert = require("node:assert/strict");
const { selection, releaseRecord } = require("../scripts/publish-runtime.cjs");
test("only explicit supported remote recipes and trusted branch/environment are selected", () => {
  assert.equal(selection("chip", "refs/heads/feature").profile, "rtl-cpu");
  assert.equal(
    selection("freecad", "refs/heads/main").profile,
    "freecad-linux-cpu",
  );
  assert.equal(selection("chip", "refs/heads/feature").environment, "test");
  assert.equal(selection("chip", "refs/heads/main").environment, "prod");
  assert.equal(selection("chip", "refs/heads/main-fix").environment, "test");
  assert.throws(() => selection("chip", "refs/tags/v1"), /branch/);
  assert.throws(() => selection("../chip", "refs/heads/feature"), /domain/);
});
test("release binds source A and publication checks without asserting engineering qualification", () => {
  const s = selection("chip", "refs/heads/feature");
  const p = {
    selection: s,
    source: "a".repeat(40),
    sourceContent: "b".repeat(64),
    input: {
      buildInputDigest: "sha256:" + "c".repeat(64),
      platform: "linux/amd64",
      buildArgs: { CHIP_RUNTIME_DIGEST: "runtime-v1" },
    },
    tool: {
      identity: "sha256:" + "d".repeat(64),
      region: "ap-guangzhou",
      request: {
        CustomConfiguration: {
          Image:
            "example.com/rtl:input-" +
            "c".repeat(64) +
            "@sha256:" +
            "e".repeat(64),
        },
      },
    },
  };
  const r = releaseRecord(
    p,
    {
      providerToolId: "sdt-test1234",
      toolConfigurationDigest: "sha256:" + "f".repeat(64),
    },
    "https://github.com/example/pack/actions/runs/1",
  );
  assert.equal(r.image.reference, "example.com/rtl@sha256:" + "e".repeat(64));
  assert.equal(r.buildSourceCommit, p.source);
  assert.equal(r.buildSourceContentSha256, p.sourceContent);
  assert.deepEqual(r.qualification.gates, [
    "registry-metadata",
    "ags-tool-active",
    "ags-tool-configuration",
  ]);
});
test("missing cloud configuration fails before checkout/build side effects", (t) => {
  const fs = require("node:fs"),
    os = require("node:os"),
    path = require("node:path");
  const { spawnSync } = require("node:child_process");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "publisher-preflight-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const result = spawnSync(
    process.execPath,
    ["scripts/publish-runtime.cjs", "prepare"],
    {
      cwd: path.resolve(__dirname, ".."),
      encoding: "utf8",
      env: {
        PATH: process.env.PATH,
        PUBLICATION_DIRECTORY: dir,
        PUBLICATION_DOMAIN: "chip",
        PUBLICATION_ENVIRONMENT: "prod", // obsolete variable must not choose routing
        GITHUB_REF: "refs/heads/feature",
      },
    },
  );
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Missing configuration: TENCENT_ROLE_ARN/);
  assert.equal(fs.existsSync(path.join(dir, "context")), false);
});
test("resume keeps the first immutable image and token even if a rebuild would differ", () => {
  const { resumePlan } = require("../scripts/publish-runtime.cjs");
  const { planTool } = require("../scripts/runtime-tool-publication.cjs");
  const s = selection("chip", "refs/heads/feature"),
    repository = "example.com/test/chip-test";
  const configuration = {
    ToolType: "custom",
    Persistent: false,
    DefaultTimeout: "300s",
    RoleArn: "role",
    NetworkConfiguration: { NetworkMode: "PUBLIC" },
    CustomConfiguration: { ImageRegistryType: "personal" },
  };
  const options = {
    selection: s,
    source: "a".repeat(40),
    sourceContent: "b".repeat(64),
    input: { buildInputDigest: "sha256:" + "c".repeat(64) },
    region: "ap-guangzhou",
    configuration,
    repository,
  };
  const tool = planTool({
    environment: "test",
    region: options.region,
    packId: s.packId,
    profile: s.profile,
    image:
      repository +
      ":input-" +
      options.input.buildInputDigest.slice(7) +
      "@sha256:" +
      "d".repeat(64),
    configuration,
  });
  const plan = {
    selection: s,
    source: options.source,
    sourceContent: options.sourceContent,
    input: options.input,
    tool,
    relative: "packs/chip/releases/rtl-cpu/test.lock.json",
  };
  assert.deepEqual(resumePlan({ ...options, plan }), plan);
  const otherSelection = selection("chip", "refs/heads/another-test-branch");
  assert.equal(
    resumePlan({ ...options, plan, selection: otherSelection }).selection
      .branch,
    "another-test-branch",
  );
  assert.throws(
    () =>
      resumePlan({
        ...options,
        plan,
        selection: selection("chip", "refs/heads/main"),
      }),
    /intent/,
  );
  const rebuilt = planTool({
    environment: "test",
    region: options.region,
    packId: s.packId,
    profile: s.profile,
    image: repository + "@sha256:" + "e".repeat(64),
    configuration,
  });
  assert.notEqual(
    resumePlan({ ...options, plan }).tool.request.ClientToken,
    rebuilt.request.ClientToken,
  );
  assert.throws(
    () => resumePlan({ ...options, plan, region: "ap-shanghai" }),
    /intent/,
  );
  assert.throws(
    () =>
      resumePlan({ ...options, plan, input: { buildInputDigest: "changed" } }),
    /intent/,
  );
});

test("each branch selects the checked-in environment configuration with existing domain limits", () => {
  const { publicationConfig } = require("../scripts/publish-runtime.cjs");
  for (const domain of ["chip", "freecad", "pcb"]) {
    for (const [ref, environment, region] of [
      ["refs/heads/feature", "test", "ap-guangzhou"],
      ["refs/heads/main", "prod", "ap-beijing"],
    ]) {
      const s = selection(domain, ref),
        config = publicationConfig(s);
      assert.equal(s.environment, environment);
      assert.equal(config.region, region);
      assert.equal(
        config.toolConfiguration.DefaultTimeout,
        domain === "chip" ? "1800s" : "900s",
      );
      assert.match(
        config.toolConfiguration.CustomConfiguration.Resources.Memory,
        /^[1-9][0-9]*(Mi|Gi)$/,
      );
      assert.equal(Object.hasOwn(config.toolConfiguration, "RoleArn"), false);
      assert.equal(
        Object.hasOwn(config.toolConfiguration.CustomConfiguration, "Image"),
        false,
      );
    }
  }
});
