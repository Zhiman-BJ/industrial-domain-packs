const test = require("node:test"),
  assert = require("node:assert/strict");
const { findImage } = require("../scripts/runtime-cloud-image.cjs");
const repository = "ccr.ccs.tencentyun.com/industrial-mcp-remote/chip-rtl";
const input = {
  buildInputDigest: "sha256:" + "a".repeat(64),
  platform: "linux/amd64",
};
test("cloud image lookup uses full input hash, returns immutable digest without a lock", async () => {
  const client = {
    request: async (action, args) => {
      assert.equal(args.RepoName, "industrial-mcp-remote/chip-rtl");
      return {
        Data: {
          TagCount: 1,
          TagInfo: [
            {
              TagName: "input-" + "a".repeat(64),
              TagId: "sha256:" + "b".repeat(64),
            },
          ],
        },
      };
    },
  };
  assert.deepEqual(await findImage({ repository, input, client }), {
    reference: repository + "@sha256:" + "b".repeat(64),
    platform: "linux/amd64",
  });
});
test("only confirmed absence permits build, errors and incomplete pagination fail", async () => {
  assert.equal(
    await findImage({
      repository,
      input,
      client: { request: async () => ({ Data: { TagCount: 0, TagInfo: [] } }) },
    }),
    null,
  );
  await assert.rejects(
    findImage({
      repository,
      input,
      client: {
        request: async () => {
          throw Error("denied");
        },
      },
    }),
    /denied/,
  );
  await assert.rejects(
    findImage({
      repository,
      input,
      client: { request: async () => ({ Data: { TagCount: 1, TagInfo: [] } }) },
    }),
    /pagination/,
  );
  await assert.rejects(
    findImage({
      repository: repository.replace("industrial-mcp-remote", "other"),
      input,
      client: {},
    }),
    /namespace/,
  );
});

test("aborted lookup passes cancellation to SDK and stops pagination", async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(
    findImage({
      repository,
      input,
      signal: controller.signal,
      client: {
        request: async (_action, _args, options) => {
          calls++;
          assert.equal(options.signal, controller.signal);
          controller.abort(Error("deadline"));
          return { Data: { TagCount: 2, TagInfo: [{ TagName: "unrelated" }] } };
        },
      },
    }),
    /deadline/,
  );
  assert.equal(calls, 1);
});
