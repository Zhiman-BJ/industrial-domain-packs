const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { command } = require("../../../lib/container.cjs");
const usage =
  "Usage: ./validate.sh <RTL-project-directory> [--output <empty-directory>] [--image <built-image>]";
function boundedFile(directory, name, limit) {
  const fd = fs.openSync(
    path.join(directory, name),
    fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK,
  );
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > limit)
      throw Error("Invalid or oversized native artifact.");
    return fs.readFileSync(fd, "utf8");
  } finally {
    fs.closeSync(fd);
  }
}
function ownFile(directory, name, content) {
  const target = path.join(directory, name);
  // Native code controls /work. Move aside any precreated name rather than
  // following a link when writing the host-side validation envelope.
  try {
    fs.lstatSync(target);
    fs.renameSync(target, target + ".native-" + crypto.randomUUID());
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  fs.writeFileSync(target, content, { flag: "wx", mode: 0o600 });
}
async function main(args) {
  if (args.length === 1 && ["--help", "-h"].includes(args[0])) {
    console.log(usage);
    return 0;
  }
  if (!args[0] || args[0].startsWith("-")) {
    console.error(usage);
    return 64;
  }
  let output,
    image = "industrial-domain-rtl:0.2.0",
    explicit = false;
  for (let i = 1; i < args.length; i += 2) {
    if (!args[i + 1] || !["--output", "--image"].includes(args[i])) {
      console.error(usage);
      return 64;
    }
    if (args[i] === "--output") {
      if (explicit) return 64;
      output = path.resolve(args[i + 1]);
      explicit = true;
    } else image = args[i + 1];
  }
  const input = path.resolve(args[0]);
  if (!fs.existsSync(input)) return 66;
  if (
    !fs.statSync(input).isDirectory() ||
    !fs.existsSync(path.join(input, "eda.yaml"))
  )
    return 65;
  const root = fs.realpathSync(input);
  if (explicit) {
    if (
      fs.existsSync(output) &&
      (fs.lstatSync(output).isSymbolicLink() ||
        !fs.statSync(output).isDirectory() ||
        fs.readdirSync(output).length)
    )
      return 73;
    try {
      fs.mkdirSync(output, { recursive: true, mode: 0o700 });
    } catch {
      return 73;
    }
  } else {
    const base = path.resolve(process.cwd(), "tmp/validation");
    fs.mkdirSync(base, { recursive: true });
    output = fs.mkdtempSync(path.join(base, "rtl-"));
  }
  console.log("Output directory: " + output);
  const started = Date.now(),
    name = "ihr-validation-" + crypto.randomUUID();
  const uid = process.getuid?.() || 1000,
    gid = process.getgid?.() || 1000;
  let code = 1,
    logs = "Starting isolated Verilator 5.026 validation.\n";
  console.log(logs.trim());
  try {
    const result = await command(
      [
        "run",
        "--rm",
        "--name",
        name,
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--init",
        "--pids-limit",
        "128",
        "--cpus",
        "2",
        "--memory",
        "2g",
        "--memory-swap",
        "2g",
        "--user",
        uid + ":" + gid,
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
        "--mount",
        "type=bind,source=" + root + ",target=/input,readonly",
        "--mount",
        "type=bind,source=" + output + ",target=/work",
        image,
        "/input",
        "/work",
      ],
      { timeout: 120000, maxBuffer: 65536 },
    );
    logs += result.stdout.toString() + result.stderr.toString();
    if (fs.existsSync(path.join(output, "tool.log")))
      logs += boundedFile(output, "tool.log", 2 * 1048576);
    const report = JSON.parse(boundedFile(output, "report.json", 65536));
    const wave = boundedFile(output, "wave.vcd", 8 * 1048576);
    const { identity } = require("../../../lib/index.cjs");
    const { verifier } = require("../runtime/verifier.cjs");
    const values = [
      { kind: "log.tool", bytes: Buffer.from(logs) },
      { kind: "report.simulation", bytes: Buffer.from(JSON.stringify(report)) },
      { kind: "waveform.vcd", bytes: Buffer.from(wave) },
    ];
    const verification = verifier({
      result: report,
      artifacts: values,
      readArtifact: (item) => item.bytes,
    });
    if (
      !result.code &&
      report.packIdentity?.contentSha256 === identity.contentSha256 &&
      verification.status === "passed"
    )
      code = 0;
  } catch (error) {
    logs += "Validation failed: " + error.message + "\n";
  } finally {
    const stopped = await command(["rm", "-f", name]);
    if (stopped.code && !/No such container/i.test(stopped.stderr.toString())) {
      code = 1;
      logs += "Container cleanup could not be confirmed.\n";
    }
  }
  process.stdout.write(logs);
  if (explicit) ownFile(output, "workload.log", logs);
  const result = {
    schema: "zhiman.eval/software-validation-result/1.0",
    status: code === 0 ? "succeeded" : "failed",
    exit_code: code,
    software: "Verilator",
    version: "5.026",
    platform: "linux/amd64",
    duration_ms: Date.now() - started,
    artifact: code === 0 ? "wave.vcd" : null,
    log: explicit ? "workload.log" : null,
  };
  ownFile(output, "result.json", JSON.stringify(result, null, 2) + "\n");
  return code;
}
module.exports = { main };
if (require.main === module)
  main(process.argv.slice(2))
    .then((code) => {
      process.exitCode = code;
    })
    .catch((error) => {
      console.error(error.message);
      process.exitCode = 1;
    });
