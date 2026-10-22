const { test } = require("node:test");
const assert = require("node:assert/strict");
const { checkCapacity } = require("../scripts/runtime-image-capacity.cjs");
const image =
  "ccr.ccs.tencentyun.com/industrial-mcp-remote/chip-rtl@sha256:" +
  "a".repeat(64);
const client = (used, limit) => ({
  request: async (action) =>
    action === "DescribeUserQuotaPersonal"
      ? { Data: { LimitInfo: [{ Type: "tag", Value: limit }] } }
      : {
          Data: { RepoName: "industrial-mcp-remote/chip-rtl", TagCount: used },
        },
});
test("capacity warning starts at 80 percent of actual account quota", async () => {
  for (const [used, limit, warn] of [
    [79, 100, false],
    [80, 100, true],
    [160, 200, true],
    [100, 100, true],
  ]) {
    const r = await checkCapacity({ image, client: client(used, limit) });
    assert.equal(r.warning, warn);
    assert.equal(r.remaining, limit - used);
  }
});
test("rejects other namespaces before querying cloud", async () => {
  await assert.rejects(
    checkCapacity({
      image: image.replace("industrial-mcp-remote", "other"),
      client: {
        request() {
          throw Error("should not query");
        },
      },
    }),
    /namespace/,
  );
});
test("query errors or incomplete quota never become healthy capacity", async () => {
  await assert.rejects(
    checkCapacity({
      image,
      client: {
        request: async () => {
          throw Error("denied");
        },
      },
    }),
    /denied/,
  );
  await assert.rejects(
    checkCapacity({ image, client: { request: async () => ({ Data: {} }) } }),
    /capacity/,
  );
});
