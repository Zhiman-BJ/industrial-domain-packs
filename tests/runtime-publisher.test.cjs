const test = require("node:test"),
  assert = require("node:assert/strict");
const { selection, releaseRecord } = require("../scripts/publish-runtime.cjs");
test("only explicit supported remote recipes and trusted branch/environment are selected", () => {
  assert.equal(
    selection("chip", "test", "refs/heads/feature").profile,
    "rtl-cpu",
  );
  assert.equal(
    selection("freecad", "prod", "refs/heads/main").profile,
    "freecad-linux-cpu",
  );
  assert.throws(() => selection("chip", "prod", "refs/heads/feature"), /main/);
  assert.throws(() => selection("chip", "test", "refs/tags/v1"), /branch/);
  assert.throws(
    () => selection("../chip", "test", "refs/heads/feature"),
    /domain/,
  );
});
test("release binds source A and publication checks without asserting engineering qualification", () => {
  const s = selection("chip", "test", "refs/heads/feature");
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
          Image: "example.com/rtl@sha256:" + "e".repeat(64),
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
  assert.equal(r.buildSourceCommit, p.source);
  assert.equal(r.buildSourceContentSha256, p.sourceContent);
  assert.deepEqual(r.qualification.gates, [
    "registry-integrity",
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
        PUBLICATION_ENVIRONMENT: "test",
        GITHUB_REF: "refs/heads/feature",
      },
    },
  );
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Missing configuration: TENCENT_REGION/);
  assert.equal(fs.existsSync(path.join(dir, "context")), false);
});
test("resume keeps the first immutable image and token even if a rebuild would differ", () => {
  const { resumePlan } = require("../scripts/publish-runtime.cjs");
  const { planTool } = require("../scripts/runtime-tool-publication.cjs");
  const s = selection("chip", "test", "refs/heads/feature"),
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
    image: repository + "@sha256:" + "d".repeat(64),
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
