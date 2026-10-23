const test = require("node:test");
const assert = require("node:assert/strict");
const image = {
  reference:
    "ccr.ccs.tencentyun.com/industrial-mcp-remote/chip-rtl@sha256:" +
    "a".repeat(64),
  platform: "linux/amd64",
};
const candidate = {
  domain: "chip",
  repository: image.reference.split("@")[0],
  input: {
    buildInputDigest: "sha256:" + "b".repeat(64),
    platform: image.platform,
  },
};
const evidence = {
  identity: {
    digest: "sha256:" + "a".repeat(64),
    platform: image.platform,
    verification: "metadata-only",
  },
};
const api = () => require("../scripts/runtime-image-readiness.cjs");
test("ready image requires matching metadata without downloading layers", async () => {
  const result = await api().checkImage(candidate, {
    find: async (options) => {
      assert.deepEqual(options, {
        repository: candidate.repository,
        input: candidate.input,
      });
      return image;
    },
    verify: async (value, credentials, metadataOnly) => {
      assert.deepEqual(value, image);
      assert.equal(credentials, true);
      assert.equal(metadataOnly, true);
      return evidence;
    },
  });
  assert.deepEqual(result, image);
});
test("missing image blocks merge with local preparation instruction", async () => {
  await assert.rejects(
    api().checkImage(candidate, {
      find: async () => null,
      verify: async () => assert.fail("must not verify absent image"),
    }),
    /npm run publish:images/,
  );
});
test("registry query failure is not treated as reusable", async () => {
  await assert.rejects(
    api().checkImage(candidate, {
      find: async () => {
        throw Error("unavailable");
      },
    }),
    /unavailable/,
  );
});
for (const identity of [
  { ...evidence.identity, digest: "sha256:" + "c".repeat(64) },
  { ...evidence.identity, platform: "linux/arm64" },
  { ...evidence.identity, verification: undefined },
])
  test(
    "mismatched registry evidence blocks readiness: " +
      JSON.stringify(identity),
    async () => {
      await assert.rejects(
        api().checkImage(candidate, {
          find: async () => image,
          verify: async () => ({ identity }),
        }),
        /identity/,
      );
    },
  );
