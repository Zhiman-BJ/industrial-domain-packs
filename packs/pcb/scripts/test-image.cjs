// Explicit local acceptance: run after building the linux/amd64 release candidate.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFile, execFileSync } = require("node:child_process");
const { promisify } = require("node:util");
const execute = promisify(execFile);
const pack = path.resolve(__dirname, "..");
const input = path.join(pack, "examples/mounting-board/board.kicad_pcb");
const image = process.env.INDUSTRIAL_PCB_VALIDATION_IMAGE;
if (!image)
  throw Error(
    "Set INDUSTRIAL_PCB_VALIDATION_IMAGE to the built image under test",
  );
const directory = fs.mkdtempSync(
  path.join(os.tmpdir(), "pcb-image-acceptance-"),
);
const docker = execFileSync("which", ["docker"], { encoding: "utf8" }).trim();
const identity = JSON.parse(
  execFileSync(docker, ["image", "inspect", image], { encoding: "utf8" }),
)[0];
assert.equal(identity.Os, "linux");
assert.equal(identity.Architecture, "amd64");
const trace = path.join(directory, "docker-calls.jsonl");
const shim = path.join(directory, "bin");
fs.mkdirSync(shim);
fs.writeFileSync(
  path.join(shim, "docker"),
  `#!/usr/bin/env node\nconst fs=require('node:fs');const cp=require('node:child_process');const args=process.argv.slice(2);fs.appendFileSync(${JSON.stringify(trace)},JSON.stringify(args)+'\\n');const r=cp.spawnSync(${JSON.stringify(docker)},args,{stdio:'inherit'});process.exit(r.status??1);\n`,
  { mode: 0o755 },
);
const env = { ...process.env, PATH: shim + path.delimiter + process.env.PATH };
const cases = [];
async function run(name, args, expected) {
  let result;
  try {
    result = {
      ...(await execute(path.join(pack, "validate.sh"), args, {
        cwd: directory,
        env,
        timeout: 180000,
        maxBuffer: 4 * 1024 * 1024,
      })),
      code: 0,
    };
  } catch (error) {
    result = error;
  }
  assert.equal(
    result.code,
    expected,
    name + "\n" + result.stdout + result.stderr,
  );
  cases.push(name);
  console.log("PASS " + name);
  return result;
}
function report(result, explicit, succeeded = true) {
  const output = result.stdout.match(/^Output directory: (.+)$/m)[1];
  const value = JSON.parse(fs.readFileSync(path.join(output, "result.json")));
  assert.equal(value.schema, "zhiman.eval/software-validation-result/1.0");
  assert.equal(value.status, succeeded ? "succeeded" : "failed");
  assert.equal(value.exit_code, result.code);
  assert.equal(value.platform, "linux/amd64");
  assert.equal(value.version, "10.0.6");
  assert.equal(value.log, explicit ? "workload.log" : null);
  assert.equal(fs.existsSync(path.join(output, "workload.log")), explicit);
  if (explicit)
    assert.ok(fs.statSync(path.join(output, "workload.log")).size > 0);
  if (succeeded) {
    assert.equal(value.artifact, "validated.kicad_pcb");
    assert.ok(fs.statSync(path.join(output, value.artifact)).size > 0);
    const summary = JSON.parse(
      fs.readFileSync(path.join(output, "board-summary.json")),
    );
    assert.equal(summary.footprints, 2);
    assert.equal(summary.errors, 0);
    assert.equal(summary.unconnected, 0);
    // KiCad's edges bounding box includes the 0.05 mm stroke.
    assert.ok(Math.abs(summary.width_mm - 40.05) < 0.001);
    assert.ok(Math.abs(summary.height_mm - 30.05) < 0.001);
  } else {
    assert.equal(value.artifact, null);
    assert.match(result.stdout, /PCB validation failed/);
  }
  return output;
}
async function main() {
  const original = fs.readFileSync(input);
  await run("help", ["--help"], 0);
  await run("short help", ["-h"], 0);
  await run("missing arguments", [], 64);
  await run("unknown option", [input, "--invalid"], 64);
  await run("missing input", [path.join(directory, "missing.kicad_pcb")], 66);
  await run("unsupported input", [pack], 65);
  const occupied = path.join(directory, "occupied");
  fs.mkdirSync(occupied);
  fs.writeFileSync(path.join(occupied, "keep"), "preserve");
  await run("refuse occupied output", [input, "--output", occupied], 73);
  assert.equal(
    fs.readFileSync(path.join(occupied, "keep"), "utf8"),
    "preserve",
  );
  assert.equal(
    fs.existsSync(trace),
    false,
    "argument checks must not start Docker",
  );
  const first = report(await run("default output", [input], 0), false);
  report(
    await run(
      "explicit output",
      [input, "--output", path.join(directory, "explicit")],
      0,
    ),
    true,
  );
  const bad = path.join(directory, "broken.kicad_pcb");
  fs.writeFileSync(bad, "(kicad_pcb (invalid truncated");
  report(
    await run(
      "real invalid board",
      [bad, "--output", path.join(directory, "invalid")],
      1,
    ),
    true,
    false,
  );
  const concurrent = await Promise.all([
    run("concurrent A", [input], 0),
    run("concurrent B", [input], 0),
  ]);
  const outputs = concurrent.map((result) => report(result, false));
  assert.equal(new Set([first, ...outputs]).size, 3);
  assert.deepEqual(
    fs.readFileSync(input),
    original,
    "input must remain read-only",
  );
  const calls = fs
    .readFileSync(trace, "utf8")
    .trim()
    .split("\n")
    .map(JSON.parse);
  const runs = calls.filter((args) => args[0] === "run");
  assert.equal(runs.length, 5);
  assert.equal(
    new Set(runs.map((args) => args[args.indexOf("--name") + 1])).size,
    5,
  );
  for (const args of runs) {
    for (const flag of ["--rm", "--read-only", "--init"])
      assert.ok(args.includes(flag));
    for (const [flag, value] of Object.entries({
      "--network": "none",
      "--cap-drop": "ALL",
      "--security-opt": "no-new-privileges",
      "--pids-limit": "128",
      "--cpus": "2",
      "--memory": "2g",
      "--memory-swap": "2g",
      "--platform": "linux/amd64",
      "--pull": "never",
      "--user": `${process.getuid()}:${process.getgid()}`,
    }))
      assert.equal(args[args.indexOf(flag) + 1], value);
    assert.match(args[args.indexOf("--tmpfs") + 1], /size=256m/);
    assert.ok(
      args.some((arg) =>
        arg.endsWith("target=/input/board.kicad_pcb,readonly"),
      ),
    );
    const name = args[args.indexOf("--name") + 1];
    assert.ok(calls.some((call) => call.join(" ") === `rm -f ${name}`));
    assert.equal(
      execFileSync(docker, ["ps", "-aq", "--filter", "name=^/" + name + "$"], {
        encoding: "utf8",
      }).trim(),
      "",
    );
  }
  const evidence = {
    image,
    imageId: identity.Id,
    platform: "linux/amd64",
    cases,
    freshContainers: runs.length,
    isolation: "passed",
    sourceUnchanged: true,
    outputDirectory: directory,
  };
  const destination = path.resolve(
    __dirname,
    "../../../dist/pcb-release/acceptance.json",
  );
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, JSON.stringify(evidence, null, 2) + "\n");
  console.log(JSON.stringify(evidence, null, 2));
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
