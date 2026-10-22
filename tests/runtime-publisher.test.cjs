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
