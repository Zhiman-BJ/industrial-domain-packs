const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const image = {
  reference: "registry.example/runtime@sha256:" + "a".repeat(64),
  platform: "linux/amd64",
};
function load(run) {
  const logs = [],
    cleared = [];
  const context = {
    module: { exports: {} },
    __dirname: path.resolve(__dirname, "../scripts"),
    require(name) {
      if (name === "node:util") return { promisify: () => run };
      if (name === "../lib/runtime-release.cjs") return {};
      return require(name);
    },
    console: { error: (line) => logs.push(line) },
    Date,
    setInterval(callback) {
      callback();
      return 42;
    },
    clearInterval: (id) => cleared.push(id),
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(context.__dirname, "runtime-image-reuse.cjs"),
      "utf8",
    ),
    context,
  );
  return { ...context.module.exports, logs, cleared };
}
test("full verification has a bounded 90 minute timeout and clears progress timer", async () => {
  const api = load(async (cmd, args, options) => {
    assert.equal(options.timeout, 90 * 60 * 1000);
    return { stdout: '{"ok":true}' };
  });
  assert.equal((await api.verifyImage(image, true)).ok, true);
  assert.ok(api.logs.some((line) => /still verifying/.test(line)));
  assert.deepEqual(api.cleared, [42]);
});
test("metadata verification retains three minute bound without progress timer", async () => {
  const api = load(async (cmd, args, options) => {
    assert.equal(options.timeout, 3 * 60 * 1000);
    return { stdout: "{}" };
  });
  await api.verifyImage(image, true, true);
  assert.deepEqual(api.logs, []);
  assert.deepEqual(api.cleared, []);
});
test("timeout reports a safe explicit failure and clears progress timer", async () => {
  const api = load(async () => {
    throw Object.assign(Error("SECRET signed URL"), {
      killed: true,
      signal: "SIGTERM",
    });
  });
  await assert.rejects(
    api.verifyImage(image, true),
    /Full image verification timed out after 90 minutes/,
  );
  assert.deepEqual(api.cleared, [42]);
  assert.ok(!api.logs.join("").includes("SECRET"));
});
test("other verification failures fail closed without leaking subprocess output", async () => {
  const api = load(async () => {
    throw Error("SECRET signed URL");
  });
  await assert.rejects(
    api.verifyImage(image, true),
    /^Error: Full image verification failed/,
  );
  assert.deepEqual(api.cleared, [42]);
});
test("normal publication uses blob HEAD checks with a three minute bound and no full readback", async () => {
  const api = load(async (cmd, args, options) => {
    assert.ok(args.includes("--blob-heads"));
    assert.ok(!args.includes("--metadata-only"));
    assert.equal(options.timeout, 3 * 60 * 1000);
    return { stdout: '{"identity":{"verification":"registry-heads"}}' };
  });
  assert.equal(
    (await api.verifyRegistryImage(image, true)).identity.verification,
    "registry-heads",
  );
  assert.deepEqual(api.logs, []);
  assert.deepEqual(api.cleared, []);
});

test("tag plus digest uses untagged repository for registry API requests", async () => {
  const api = load(async (cmd, args) => {
    assert.equal(
      args[2],
      "https://registry.example/v2/runtime/manifests/sha256:" + "a".repeat(64),
    );
    return { stdout: "{}" };
  });
  await api.verifyImage(
    { ...image, reference: image.reference.replace("@", ":input-tag@") },
    true,
    true,
  );
});
