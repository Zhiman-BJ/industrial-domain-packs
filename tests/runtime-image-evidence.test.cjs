const test = require("node:test");
const assert = require("node:assert/strict");
const {
  consumeEvidence,
  imageEvidence,
} = require("../scripts/runtime-image-evidence.cjs");
const scope = {
  GITHUB_REPOSITORY: "owner/packs",
  GITHUB_RUN_ID: "123",
  GITHUB_RUN_ATTEMPT: "1",
};
const candidate = {
  domain: "chip",
  repository: "ccr.ccs.tencentyun.com/industrial-mcp-remote/chip-rtl",
  input: {
    buildInputDigest: "sha256:" + "b".repeat(64),
    platform: "linux/amd64",
  },
};
const image = {
  reference: candidate.repository + "@sha256:" + "a".repeat(64),
  platform: "linux/amd64",
};
const registry = {
  identity: {
    digest: "sha256:" + "a".repeat(64),
    platform: image.platform,
    verification: "metadata-only",
  },
};
const record = () => imageEvidence(candidate, image, registry, scope);
test("same workflow evidence reuses the exact immutable image", () => {
  assert.deepEqual(consumeEvidence(record(), candidate, scope), {
    image,
    registry,
  });
  assert.deepEqual(
    consumeEvidence(
      record(),
      {
        ...candidate,
        image: {
          ...image,
          reference: image.reference.replace("@", ":input-abc@"),
        },
      },
      scope,
    ),
    { image, registry },
  );
});
test("missing, stale or mismatched evidence requires fresh verification", () => {
  assert.equal(consumeEvidence(null, candidate, scope), null);
  for (const env of [
    {},
    { ...scope, GITHUB_RUN_ID: "124" },
    { ...scope, GITHUB_RUN_ATTEMPT: "2" },
    { ...scope, GITHUB_REPOSITORY: "other/packs" },
  ])
    assert.equal(consumeEvidence(record(), candidate, env), null);
  for (const change of [
    { domain: "pcb" },
    { repository: "other/repo" },
    {
      input: {
        ...candidate.input,
        buildInputDigest: "sha256:" + "c".repeat(64),
      },
    },
    { input: { ...candidate.input, platform: "linux/arm64" } },
    {
      image: {
        ...image,
        reference: image.reference.replace("a".repeat(64), "d".repeat(64)),
      },
    },
  ])
    assert.equal(
      consumeEvidence(record(), { ...candidate, ...change }, scope),
      null,
    );
  for (const change of [
    { registry: {} },
    { image: { ...image, reference: "other/repo@sha256:" + "a".repeat(64) } },
    {
      registry: {
        identity: { ...registry.identity, verification: "full-blobs" },
      },
    },
  ])
    assert.equal(
      consumeEvidence({ ...record(), ...change }, candidate, scope),
      null,
    );
});
