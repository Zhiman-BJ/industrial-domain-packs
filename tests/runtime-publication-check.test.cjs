const test = require("node:test"),
  assert = require("node:assert/strict");
const fs = require("node:fs"),
  path = require("node:path"),
  os = require("node:os"),
  vm = require("node:vm");
const { createRequire } = require("node:module");
const filename = require.resolve("../scripts/publish-runtime.cjs");
const realRequire = createRequire(filename);
const { selection, publicationConfig } = realRequire("./publish-runtime.cjs");
const { planTool } = realRequire("./runtime-tool-publication.cjs");
for (const scenario of [
  "missing-image",
  "missing-tool",
  "reuse",
  "query-error",
]) {
  test(`check phase is read-only: ${scenario}`, async (t) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "publication-readonly-"));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const s = selection("chip", "refs/heads/main"),
      config = publicationConfig(s);
    const source = "a".repeat(40),
      hash = "sha256:" + "b".repeat(64);
    const image = {
      reference:
        realRequire("./runtime-cloud-image.cjs").repositoryFor("chip") +
        "@" +
        hash,
      platform: "linux/amd64",
    };
    const tool = planTool({
      environment: s.environment,
      region: config.region,
      packId: s.packId,
      profile: s.profile,
      image: image.reference,
      configuration: { ...config.toolConfiguration, RoleArn: "role" },
    });
    const row = {
      ...tool.request,
      ToolId: "sdt-example",
      Status: "ACTIVE",
      DefaultTimeoutSeconds: 1800,
    };
    const commands = [];
    const client = {
      async DescribeSandboxToolList() {
        return {
          TotalCount: scenario === "reuse" ? 1 : 0,
          SandboxToolSet: scenario === "reuse" ? [row] : [],
        };
      },
      async CreateSandboxTool() {
        assert.fail("check must not create Tools");
      },
      async UpdateSandboxTool() {
        assert.fail("check must not update Tools");
      },
    };
    const mocks = {
      "node:child_process": {
        execFileSync(command, args) {
          commands.push(command);
          if (command === "git" && args[0] === "status") return "";
          if (command === "git" && args[0] === "rev-parse") return source;
          if (command === "npm") return "";
          assert.fail("unexpected subprocess: " + command);
        },
      },
      "./runtime-build.cjs": {
        inputs: () => ({
          platform: image.platform,
          buildInputDigest: hash,
          buildArgs: {},
        }),
      },
      "./runtime-cloud-image.cjs": {
        ...realRequire("./runtime-cloud-image.cjs"),
        findImage: async () => {
          if (scenario === "query-error") throw Error("query denied");
          return scenario === "missing-image" ? null : image;
        },
      },
      "./runtime-image-reuse.cjs": {
        verifyImage: async () => ({
          identity: { digest: hash, platform: image.platform },
        }),
      },
      "tencentcloud-sdk-nodejs-ags": {
        ags: {
          v20250920: {
            Client: class {
              constructor() {
                return client;
              }
            },
          },
        },
      },
    };
    const context = {
      require: (name) => mocks[name] || realRequire(name),
      module: { exports: {} },
      __dirname: path.dirname(filename),
      console,
      structuredClone,
      process: {
        chdir() {},
        env: {
          PUBLICATION_DIRECTORY: dir,
          PUBLICATION_DOMAIN: "chip",
          GITHUB_REF: "refs/heads/main",
          GITHUB_SHA: source,
          TENCENT_ROLE_ARN: "role",
          TENCENT_SECRET_ID: "test",
          TENCENT_SECRET_KEY: "test",
        },
      },
    };
    vm.runInNewContext(
      fs.readFileSync(filename, "utf8") + "\nmodule.exports.testMain = main;",
      context,
      { filename },
    );
    if (["query-error", "missing-image"].includes(scenario)) {
      await assert.rejects(
        context.module.exports.testMain("check"),
        /query denied|Missing verified image/,
      );
      assert.equal(fs.existsSync(path.join(dir, "check.json")), false);
    } else {
      await context.module.exports.testMain("check");
      const result = JSON.parse(fs.readFileSync(path.join(dir, "check.json")));
      assert.equal(result.needsPublication, scenario !== "reuse");
      assert.equal(
        fs.existsSync(path.join(dir, "result.json")),
        scenario === "reuse",
      );
    }
    assert.equal(commands.includes("docker"), false);
  });
}
