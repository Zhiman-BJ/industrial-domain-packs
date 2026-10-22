const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const api = () => require("../scripts/runtime-build.cjs");
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "runtime-build-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const write = (name, data) => {
    fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    fs.writeFileSync(path.join(root, name), data);
  };
  const recipe = "packs/example/Dockerfile";
  write(
    recipe,
    "# syntax=docker/dockerfile:1@sha256:" +
      "1".repeat(64) +
      "\nFROM ubuntu@sha256:" +
      "2".repeat(64) +
      "\nARG RUNTIME_DIGEST=runtime-1\nCOPY lib/worker.py packs/example/run.py /app/\nRUN echo ok\n",
  );
  write("lib/worker.py", "worker");
  write("packs/example/run.py", "run");
  write("README.md", "docs");
  write(".dockerignore", "lib/worker.py");
  write("packs/example/releases/test.lock.json", "{}");
  return { root, write, recipe, platform: "linux/amd64" };
}
test("fingerprint follows actual inputs, not publications or documentation; prepared context matches", (t) => {
  const f = fixture(t);
  const original = api().inputs(f);
  f.write("README.md", "new docs");
  f.write("packs/example/releases/test.lock.json", "new lock");
  f.write("content-lock.json", "new full hash");
  assert.equal(api().inputs(f).buildInputDigest, original.buildInputDigest);
  for (const name of ["lib/worker.py", "packs/example/run.py", f.recipe]) {
    const bytes = fs.readFileSync(path.join(f.root, name));
    f.write(name, bytes + "\n");
    assert.notEqual(
      api().inputs(f).buildInputDigest,
      original.buildInputDigest,
    );
    f.write(name, bytes);
  }
  assert.notEqual(
    api().inputs({ ...f, platform: "linux/arm64" }).buildInputDigest,
    original.buildInputDigest,
  );
  assert.notEqual(
    api().inputs({ ...f, buildArgs: { RUNTIME_DIGEST: "runtime-2" } })
      .buildInputDigest,
    original.buildInputDigest,
  );
  const target = path.join(f.root, "context");
  api().prepareContext(f, target);
  assert.deepEqual(fs.readdirSync(target).sort(), ["lib", "packs"]);
  assert.equal(
    fs.existsSync(path.join(target, "packs/example/releases")),
    false,
  );
  assert.equal(
    api().inputs({ ...f, root: target }).buildInputDigest,
    original.buildInputDigest,
  );
});
test("unknown Docker input syntax, mutable base images and unsafe sources fail closed", (t) => {
  const f = fixture(t);
  const recipe = fs.readFileSync(path.join(f.root, f.recipe), "utf8");
  for (const line of [
    "ADD https://example.org/a /a",
    "COPY . /app",
    "COPY ../outside /app",
    "COPY *.py /app",
    "RUN --mount=type=bind,source=hidden,target=/x cat /x",
    "RUN --mount=type=cache,from=ubuntu:latest,target=/x cat /x",
    "FROM ubuntu:latest",
    "COPY --from=unknown:latest /x /x",
    "COPY content-lock.json /app/",
    "COPY .dockerignore /app/",
  ]) {
    f.write(f.recipe, recipe + "\n" + line + "\n");
    assert.throws(
      () => api().inputs(f),
      /unsupported|immutable|publication|unsafe|broad/i,
      line,
    );
  }
  f.write(f.recipe, recipe);
  fs.unlinkSync(path.join(f.root, "lib/worker.py"));
  fs.symlinkSync("../packs/example/run.py", path.join(f.root, "lib/worker.py"));
  assert.throws(() => api().inputs(f), /symlink/);
});
test("real profiles exclude publication metadata and respond to shared worker changes", (t) => {
  const root = path.join(__dirname, "..");
  for (const recipe of [
    "packs/chip/images/Dockerfile.rtl-cpu",
    "packs/freecad/images/Dockerfile.linux-cpu",
    "packs/pcb/images/Dockerfile.pcb-verify",
  ]) {
    const input = api().inputs({ root, recipe, platform: "linux/amd64" });
    assert.ok(input.files["lib/worker-http.py"]);
    assert.ok(!input.files["content-lock.json"]);
    assert.ok(
      !Object.keys(input.files).some((name) => name.includes("/releases/")),
    );
    const parent = fs.mkdtempSync(
      path.join(os.tmpdir(), "real-runtime-context-"),
    );
    t.after(() => fs.rmSync(parent, { recursive: true, force: true }));
    const context = path.join(parent, "build");
    api().prepareContext({ root, recipe, platform: input.platform }, context);
    assert.equal(
      api().inputs({ root: context, recipe, platform: input.platform })
        .buildInputDigest,
      input.buildInputDigest,
    );
    fs.appendFileSync(
      path.join(context, "lib/worker-http.py"),
      "\n# worker change\n",
    );
    assert.notEqual(
      api().inputs({ root: context, recipe, platform: input.platform })
        .buildInputDigest,
      input.buildInputDigest,
    );
  }
});
test("reuse requires matching validated publication and live immutable image evidence", async (t) => {
  const f = fixture(t),
    input = api().inputs(f);
  const expected = {
    packId: "example-pack",
    domain: "example",
    runtimeProfile: "cpu",
    environment: "test",
  };
  const record = {
    ...expected,
    schemaVersion: 1,
    releaseId: "example-1",
    region: "ap-guangzhou",
    buildInputDigest: input.buildInputDigest,
    runtimeDigest: "runtime-1",
    buildSourceCommit: "a".repeat(40),
    buildSourceContentSha256: "b".repeat(64),
    image: {
      reference: "registry.example/worker@sha256:" + "c".repeat(64),
      platform: input.platform,
    },
    provider: "tencent-ags",
    providerToolId: "sdt-example",
    toolConfigurationDigest: "sha256:" + "d".repeat(64),
    qualification: {
      status: "passed",
      gates: ["native-success"],
      evidence: ["evidence/run-1"],
    },
  };
  let calls = 0;
  const check = async (image) => {
    calls++;
    return {
      identity: {
        digest: image.reference.split("@")[1],
        platform: image.platform,
      },
    };
  };
  const reuse = require("../scripts/runtime-image-reuse.cjs").reuseImage;
  assert.equal(
    (await reuse({ input, record, expected, verifyImage: check })).reuse,
    true,
  );
  assert.equal(calls, 1);
  assert.equal(
    (
      await reuse({
        input: { ...input, buildInputDigest: "sha256:" + "e".repeat(64) },
        record,
        expected,
        verifyImage: check,
      })
    ).reuse,
    false,
  );
  assert.equal(calls, 1);
  await assert.rejects(
    () =>
      reuse({
        input,
        record: { ...record, runtimeDigest: "wrong-runtime" },
        expected,
        verifyImage: check,
      }),
    /runtime/,
  );
  await assert.rejects(
    () =>
      reuse({
        input,
        record: {
          ...record,
          qualification: { ...record.qualification, status: "pending" },
        },
        expected,
        verifyImage: check,
      }),
    /qualification/,
  );
  await assert.rejects(
    () =>
      reuse({
        input,
        record,
        expected,
        verifyImage: async () => {
          throw Error("registry unavailable");
        },
      }),
    /registry unavailable/,
  );
  await assert.rejects(
    () =>
      reuse({
        input,
        record,
        expected,
        verifyImage: async () => ({
          identity: { digest: "wrong", platform: input.platform },
        }),
      }),
    /identity/,
  );
  await assert.rejects(
    () =>
      reuse({
        input,
        record,
        expected,
        verifyImage: async () => ({
          identity: {
            digest: record.image.reference.split("@")[1],
            platform: "linux/arm64",
          },
        }),
      }),
    /identity/,
  );
});
