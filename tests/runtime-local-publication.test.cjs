const test = require("node:test"),
  assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const required = [
  "TENCENT_SECRET_ID",
  "TENCENT_SECRET_KEY",
  "TENCENT_REGISTRY_USERNAME",
  "TENCENT_REGISTRY_PASSWORD",
];
test("local entry reports every missing variable before any tool or cloud operation", () => {
  const r = spawnSync(process.execPath, ["scripts/publish-images-local.cjs"], {
    cwd: path.resolve(__dirname, ".."),
    encoding: "utf8",
    env: { PATH: "" },
  });
  assert.equal(r.status, 1);
  for (const name of required) assert.match(r.stderr, new RegExp(name));
  assert.doesNotMatch(r.stderr, /ENOENT/);
});
test("empty and whitespace-only variables fail without printing supplied secrets", () => {
  const env = Object.fromEntries(
    required.map((k) => [k, "sensitive-test-value"]),
  );
  env.TENCENT_REGISTRY_PASSWORD = "  ";
  const r = spawnSync(process.execPath, ["scripts/publish-images-local.cjs"], {
    cwd: path.resolve(__dirname, ".."),
    encoding: "utf8",
    env: { ...env, PATH: "" },
  });
  assert.equal(r.status, 1);
  assert.match(r.stderr, /TENCENT_REGISTRY_PASSWORD/);
  assert.doesNotMatch(r.stderr, /sensitive-test-value/);
});
const fs = require("node:fs"),
  os = require("node:os");
const { publishImage } = require("../scripts/publish-images-local.cjs");
for (const scenario of [
  "build",
  "reuse",
  "verify-failure",
  "query-failure",
  "upload-failure",
])
  test(`local image handoff: ${scenario}`, async (t) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "local-image-"));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const hash = "sha256:" + "a".repeat(64),
      repo = "ccr.ccs.tencentyun.com/industrial-mcp-remote/chip-rtl";
    const image = { reference: repo + "@" + hash, platform: "linux/amd64" },
      calls = [];
    let tagged = scenario === "reuse";
    const deps = {
      find: async () => {
        calls.push("find");
        if (scenario === "query-failure") throw Error("cloud denied");
        return tagged ? image : null;
      },
      verify: async (actual, credentials) => {
        calls.push("verify");
        assert.equal(credentials, true);
        assert.deepEqual(actual, image);
        if (scenario === "verify-failure") throw Error("corrupt layer");
        return {
          identity: {
            digest: hash,
            platform: image.platform,
            verification: "registry-heads",
          },
        };
      },
      execute: (cmd, args, options) => {
        if (cmd === "regctl") {
          assert.equal(
            options.env.REGCTL_CONFIG,
            path.join(directory, "regctl.json"),
          );
          if (args[0] === "registry") {
            calls.push("upload-config");
            assert.equal(args[args.indexOf("--blob-chunk") + 1], "8388608");
            assert.equal(args[args.indexOf("--blob-max") + 1], "8388608");
          } else {
            calls.push("upload");
            assert.deepEqual(args.slice(0, 2), ["image", "copy"]);
            assert.equal(args.at(-1), image.reference);
            assert.equal(
              args.at(-2),
              "ocidir://" + path.join(directory, "image.oci") + "@" + hash,
            );
            if (scenario === "upload-failure") throw Error("upload failed");
          }
          return;
        }
        assert.equal(cmd, "docker");
        if (args[1] === "build") {
          calls.push("build");
          assert.match(
            args[args.indexOf("--output") + 1],
            /^type=oci,tar=false,dest=/,
          );
          assert.equal(args.includes("--tag"), false);
          assert.doesNotMatch(args[args.indexOf("--output") + 1], /push/);
          fs.writeFileSync(
            path.join(directory, "build-metadata.json"),
            JSON.stringify({ "containerimage.digest": hash }),
          );
        } else {
          calls.push("promote");
          assert.ok(calls.includes("verify"));
          assert.ok(args.includes("--prefer-index=false"));
          tagged = true;
        }
      },
    };
    const promise = publishImage(
      {
        repository: repo,
        input: {
          platform: image.platform,
          buildInputDigest: hash,
          buildArgs: {},
        },
        directory,
        recipe: "Dockerfile",
      },
      deps,
    );
    if (scenario.endsWith("failure")) {
      await assert.rejects(promise, /corrupt layer|cloud denied|upload failed/);
      assert.equal(calls.includes("promote"), false);
    } else {
      const r = await promise;
      assert.equal(r.operation, scenario === "reuse" ? "reused" : "built");
      assert.deepEqual(
        calls,
        scenario === "reuse"
          ? ["find", "verify"]
          : [
              "find",
              "build",
              "upload-config",
              "upload",
              "verify",
              "find",
              "promote",
              "find",
            ],
      );
    }
  });

test("repeated image attempts preserve prior evidence and use fresh contexts", (t) => {
  const { attemptDirectory } = require("../scripts/publish-images-local.cjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "local-retry-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const first = attemptDirectory(root, "a".repeat(40), "chip");
  fs.mkdirSync(path.join(first, "context"));
  fs.writeFileSync(path.join(first, "image.json"), "previous evidence");
  const second = attemptDirectory(root, "a".repeat(40), "chip");
  assert.notEqual(first, second);
  fs.mkdirSync(path.join(second, "context"));
  assert.equal(
    fs.readFileSync(path.join(first, "image.json"), "utf8"),
    "previous evidence",
  );
});

for (const scenario of ["eventual", "absent", "mismatch", "query-error"])
  test(`promotion readback: ${scenario}`, async () => {
    const { waitForImage } = require("../scripts/publish-images-local.cjs");
    const image = { reference: "repo@sha256:" + "a".repeat(64) };
    let elapsed = 0,
      calls = 0;
    const promise = waitForImage({}, image, {
      now: () => elapsed,
      sleep: async (ms) => {
        elapsed += ms;
      },
      find: async () => {
        calls++;
        if (scenario === "query-error") throw Error("denied");
        if (scenario === "mismatch")
          return { reference: "repo@sha256:" + "b".repeat(64) };
        return scenario === "eventual" && calls === 3 ? image : null;
      },
    });
    if (scenario === "eventual") {
      await promise;
      assert.equal(calls, 3);
      assert.equal(elapsed, 10000);
    } else {
      await assert.rejects(
        promise,
        scenario === "absent"
          ? /60 seconds/
          : scenario === "mismatch"
            ? /different image/
            : /denied/,
      );
      assert.equal(elapsed, scenario === "absent" ? 60000 : 0);
      if (scenario !== "absent") assert.equal(calls, 1);
    }
  });

test("readback deadline cancels an in-flight query", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { waitForImage } = require("../scripts/publish-images-local.cjs");
  let signal;
  const pending = waitForImage(
    {},
    { reference: "expected" },
    {
      find: async (options) => {
        signal = options.signal;
        return new Promise((_, reject) =>
          signal.addEventListener("abort", () => reject(signal.reason), {
            once: true,
          }),
        );
      },
    },
  );
  const assertion = assert.rejects(pending, /60 seconds/);
  t.mock.timers.tick(60000);
  await assertion;
  assert.equal(signal.aborted, true);
});
