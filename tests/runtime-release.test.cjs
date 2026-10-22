const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");

const digest = (char) => "sha256:" + char.repeat(64);
const expected = () => ({
  packId: "example-pack",
  domain: "example",
  runtimeProfile: "cpu",
  environment: "test",
});
const release = () => ({
  schemaVersion: 1,
  releaseId: "example-cpu-test-1",
  ...expected(),
  region: "ap-guangzhou",
  buildInputDigest: digest("a"),
  runtimeDigest: digest("b"),
  buildSourceCommit: "c".repeat(40),
  buildSourceContentSha256: "d".repeat(64),
  image: {
    reference: "registry.example.org/workers/example@" + digest("e"),
    platform: "linux/amd64",
  },
  provider: "tencent-ags",
  providerToolId: "sdt-example1",
  toolConfigurationDigest: digest("f"),
  qualification: {
    status: "passed",
    gates: ["native-success", "native-failure"],
    evidence: ["evidence/test-run-1"],
  },
});
const api = () => require("../lib/runtime-release.cjs");

test("validates a release against trusted selection and returns an isolated immutable record", () => {
  const input = release();
  const result = api().validateRuntimeRelease(input, expected());
  assert.equal(result.providerToolId, "sdt-example1");
  assert.equal(result.buildSourceContentSha256, "d".repeat(64));
  input.qualification.gates.push("invented");
  assert.deepEqual(result.qualification.gates, [
    "native-success",
    "native-failure",
  ]);
  assert.ok(Object.isFrozen(result.image));
});

test("rejects wrong routing selection", () => {
  for (const key of Object.keys(expected())) {
    const selection = expected();
    selection[key] = "different";
    assert.throws(
      () => api().validateRuntimeRelease(release(), selection),
      /selection|mismatch/i,
      key,
    );
  }
  assert.throws(() => api().validateRuntimeRelease(release()), /selection/i);
});

test("rejects malformed, mutable, incomplete and secret-bearing release records", () => {
  const mutations = [
    (r) => {
      r.buildInputDigest = "invalid";
    },
    (r) => {
      delete r.runtimeDigest;
    },
    (r) => {
      r.image.reference = "registry.example.org/worker:latest";
    },
    (r) => {
      r.image.reference =
        "user:password@registry.example.org/worker@" + digest("e");
    },
    (r) => {
      r.image.reference = "https://registry.example.org/worker@" + digest("e");
    },
    (r) => {
      r.buildSourceCommit = "main";
    },
    (r) => {
      r.releaseId += "\n";
    },
    (r) => {
      r.buildSourceContentSha256 = "short";
    },
    (r) => {
      delete r.toolConfigurationDigest;
    },
    (r) => {
      r.schemaVersion = 2;
    },
    (r) => {
      r.provider = "arbitrary";
    },
    (r) => {
      r.region = "invalid region";
    },
    (r) => {
      r.qualification.status = "pending";
    },
    (r) => {
      r.qualification.evidence = [];
    },
    (r) => {
      r.qualification.gates = ["same", "same"];
    },
    (r) => {
      r.image.platform = "unknown";
    },
    (r) => {
      r.token = "must-not-be-accepted";
    },
    (r) => {
      r.image.command = "sh";
    },
  ];
  for (const mutate of mutations) {
    const record = release();
    mutate(record);
    assert.throws(
      () => api().validateRuntimeRelease(record, expected()),
      /Invalid runtime release/,
    );
  }
});

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "pack-release-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const relativePath = "packs/example/releases/cpu/test.lock.json";
  fs.mkdirSync(path.dirname(path.join(root, relativePath)), {
    recursive: true,
  });
  const bytes = JSON.stringify(release());
  fs.writeFileSync(path.join(root, relativePath), bytes);
  const contentLock = {
    files: {
      [relativePath]: crypto.createHash("sha256").update(bytes).digest("hex"),
    },
  };
  return { root, relativePath, contentLock, expected: expected() };
}

test("reads only hash-covered release bytes and rejects tampering or untracked locks", (t) => {
  const options = fixture(t);
  assert.equal(
    api().readRuntimeRelease(options).releaseId,
    "example-cpu-test-1",
  );
  assert.throws(
    () => api().readRuntimeRelease({ ...options, contentLock: { files: {} } }),
    /content lock/i,
  );
  fs.appendFileSync(path.join(options.root, options.relativePath), " ");
  assert.throws(() => api().readRuntimeRelease(options), /content lock/i);
});

test("rejects traversal, mismatched filename selection, symlink files and parent directories", (t) => {
  const options = fixture(t);
  for (const relativePath of [
    "../outside",
    "/tmp/a",
    "packs/example/releases/cpu/prod.lock.json",
    "packs/example/releases/other/test.lock.json",
  ])
    assert.throws(
      () => api().readRuntimeRelease({ ...options, relativePath }),
      /path/i,
    );
  const file = path.join(options.root, options.relativePath);
  const saved = path.join(options.root, "saved.json");
  fs.renameSync(file, saved);
  fs.symlinkSync(saved, file);
  assert.throws(() => api().readRuntimeRelease(options), /symlink/i);
  fs.unlinkSync(file);
  fs.renameSync(saved, file);
  const parent = path.dirname(file);
  fs.renameSync(parent, parent + "-real");
  fs.symlinkSync(parent + "-real", parent);
  assert.throws(() => api().readRuntimeRelease(options), /symlink/i);
});

test("public consumer facade derives routing from the pinned catalog and rejects unknown profiles", () => {
  const pack = require("../lib/index.cjs");
  assert.equal(typeof pack.getRuntimeRelease, "function");
  assert.throws(
    () => pack.getRuntimeRelease("missing-pack", "cpu", "test"),
    /Unknown Domain Pack/,
  );
  assert.throws(
    () => pack.getRuntimeRelease("chip-pack", "missing-profile", "test"),
    /profile/i,
  );
  assert.throws(
    () => pack.getRuntimeRelease("chip-pack", "rtl-cpu", "test"),
    /content lock/i,
  );
});

test("package consumer reads a covered lock and rejects altered bytes and a wrong platform", (t) => {
  const source = path.join(__dirname, "..");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "release-consumer-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const name of [
    "lib",
    "packs",
    "publication",
    "package.json",
    "content-lock.json",
  ])
    fs.cpSync(path.join(source, name), path.join(root, name), {
      recursive: true,
    });
  const relativePath = "packs/chip/releases/rtl-cpu/test.lock.json";
  const record = {
    ...release(),
    packId: "chip-pack",
    domain: "chip",
    runtimeProfile: "rtl-cpu",
  };
  fs.mkdirSync(path.dirname(path.join(root, relativePath)), {
    recursive: true,
  });
  const writeLock = () => {
    fs.writeFileSync(path.join(root, relativePath), JSON.stringify(record));
    const lock = JSON.parse(
      fs.readFileSync(path.join(root, "content-lock.json")),
    );
    lock.files[relativePath] = crypto
      .createHash("sha256")
      .update(JSON.stringify(record))
      .digest("hex");
    lock.files = Object.fromEntries(
      Object.entries(lock.files).sort(([a], [b]) => a.localeCompare(b)),
    );
    lock.contentSha256 = crypto
      .createHash("sha256")
      .update(JSON.stringify(lock.files))
      .digest("hex");
    fs.writeFileSync(
      path.join(root, "content-lock.json"),
      JSON.stringify(lock),
    );
  };
  const invoke = () =>
    require("node:child_process").spawnSync(
      process.execPath,
      [
        "-e",
        `
    const pack = require('./lib/index.cjs');
    const record = pack.getRuntimeRelease('chip-pack', 'rtl-cpu', 'test');
    console.log(record.providerToolId);
    if (record.region !== 'ap-guangzhou') throw Error('region missing');
    if ('workerProtocolVersion' in record) throw Error('unexpected protocol contract');
  `,
      ],
      { cwd: root, encoding: "utf8" },
    );
  writeLock();
  let result = invoke();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "sdt-example1");
  const publication = path.join(root, "publication/test.cjs");
  const original = fs.readFileSync(publication);
  fs.appendFileSync(publication, "\n// changed config bytes\n");
  result = invoke();
  assert.notEqual(result.status, 0);
  assert.match(
    result.stderr,
    /differs from its pinned release.*publication\/test.cjs/,
  );
  fs.writeFileSync(publication, original);

  fs.appendFileSync(path.join(root, relativePath), " ");
  result = invoke();
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /differs from its pinned release/);
  record.image.platform = "linux/arm64";
  writeLock();
  result = invoke();
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /platform differs/);
});
