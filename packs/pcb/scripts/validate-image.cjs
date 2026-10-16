const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn, execFile } = require("node:child_process");
const command = (args) =>
  new Promise((resolve) =>
    execFile(
      "docker",
      args,
      { timeout: 30000, maxBuffer: 65536 },
      (error, stdout, stderr) =>
        resolve({ code: error?.code || 0, stdout, stderr }),
    ),
  );
const usage =
  "Usage: ./validate.sh <board.kicad_pcb> [--output <empty-directory>] [--image <built-image>]";

function readArtifact(directory, name, limit = 16 * 1024 * 1024) {
  const fd = fs.openSync(
    path.join(directory, name),
    fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK,
  );
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || !stat.size || stat.size > limit)
      throw Error("Invalid native artifact: " + name);
    return fs.readFileSync(fd, "utf8");
  } finally {
    fs.closeSync(fd);
  }
}
function writeEnvelope(directory, name, data) {
  const target = path.join(directory, name);
  try {
    fs.renameSync(target, target + ".native-" + crypto.randomUUID());
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  fs.writeFileSync(target, data, { flag: "wx", mode: 0o600 });
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
  const flags = {};
  for (let i = 1; i < args.length; i += 2) {
    if (
      !["--output", "--image"].includes(args[i]) ||
      !args[i + 1] ||
      args[i + 1].startsWith("-") ||
      flags[args[i]]
    ) {
      console.error(usage);
      return 64;
    }
    flags[args[i]] = args[i + 1];
  }
  let input;
  try {
    input = fs.realpathSync(args[0]);
    fs.accessSync(input, fs.constants.R_OK);
  } catch {
    return 66;
  }
  if (!fs.statSync(input).isFile() || path.extname(input) !== ".kicad_pcb")
    return 65;
  if (input.includes(",") || flags["--output"]?.includes(",")) return 64;
  const explicit = Boolean(flags["--output"]);
  let output;
  try {
    if (explicit) {
      output = path.resolve(flags["--output"]);
      if (
        fs.existsSync(output) &&
        (fs.lstatSync(output).isSymbolicLink() ||
          !fs.statSync(output).isDirectory() ||
          fs.readdirSync(output).length)
      )
        return 73;
      fs.mkdirSync(output, { recursive: true, mode: 0o700 });
      output = fs.realpathSync(output);
    } else {
      const base = path.resolve("tmp/validation");
      fs.mkdirSync(base, { recursive: true });
      output = fs.mkdtempSync(
        path.join(base, path.basename(input, ".kicad_pcb") + "-"),
      );
    }
  } catch {
    return 73;
  }
  console.log("Output directory: " + output);
  const started = Date.now(),
    name = "pcb-validation-" + crypto.randomUUID();
  const image =
    flags["--image"] ||
    process.env.INDUSTRIAL_PCB_VALIDATION_IMAGE ||
    "industrial-pcb-runtime:pcb-v0.1.0";
  let code = 1,
    logs = "";
  const emit = (chunk) => {
    process.stdout.write(chunk);
    if (logs.length < 2 * 1024 * 1024)
      logs += String(chunk).slice(0, 2 * 1024 * 1024 - logs.length);
  };
  try {
    emit("Starting isolated KiCad 10.0.6 board validation.\n");
    const args = [
      "run",
      "--rm",
      "--pull",
      "never",
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
      `${process.getuid?.() ?? 1000}:${process.getgid?.() ?? 1000}`,
      "--tmpfs",
      "/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
      "--env",
      "HOME=/tmp",
      "--env",
      "PYTHONDONTWRITEBYTECODE=1",
      "--workdir",
      "/work",
      "--mount",
      `type=bind,source=${input},target=/input/board.kicad_pcb,readonly`,
      "--mount",
      `type=bind,source=${output},target=/work`,
      "--entrypoint",
      "python3",
      image,
      "/opt/industrial-pcb/validate-board.py",
      "/input/board.kicad_pcb",
      "/work",
    ];
    const exit = await new Promise((resolve) => {
      const child = spawn("docker", args, {
        stdio: ["ignore", "pipe", "pipe"],
      });
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        emit("Validation timed out.\n");
        child.kill("SIGKILL");
      }, 120000);
      child.stdout.on("data", emit);
      child.stderr.on("data", emit);
      child.on("error", (error) => {
        clearTimeout(timer);
        emit(error.message + "\n");
        resolve(1);
      });
      child.on("close", (status) => {
        clearTimeout(timer);
        resolve(timedOut ? 124 : (status ?? 1));
      });
    });
    if (exit) throw Error("Native validation exited " + exit);
    const summary = JSON.parse(
      readArtifact(output, "board-summary.json", 65536),
    );
    const drc = JSON.parse(readArtifact(output, "drc.json"));
    const board = readArtifact(output, "validated.kicad_pcb");
    if (
      summary.version !== "10.0.6" ||
      summary.errors !== 0 ||
      summary.unconnected !== 0 ||
      !(summary.width_mm > 0 && summary.height_mm > 0) ||
      !board.startsWith("(kicad_pcb") ||
      !Array.isArray(drc.violations) ||
      drc.violations.some((v) => v.severity === "error") ||
      drc.unconnected_items?.length !== 0
    )
      throw Error("Native artifacts did not pass verification");
    code = 0;
  } catch (error) {
    emit("Validation failed: " + error.message + "\n");
  } finally {
    const cleanup = await command(["rm", "-f", name]);
    if (cleanup.code && !/No such container/i.test(cleanup.stderr.toString())) {
      code = 1;
      emit("Container cleanup could not be confirmed.\n");
    }
  }
  if (explicit) writeEnvelope(output, "workload.log", logs);
  writeEnvelope(
    output,
    "result.json",
    JSON.stringify(
      {
        schema: "zhiman.eval/software-validation-result/1.0",
        status: code === 0 ? "succeeded" : "failed",
        exit_code: code,
        software: "KiCad",
        version: "10.0.6",
        platform: "linux/amd64",
        duration_ms: Date.now() - started,
        artifact: code === 0 ? "validated.kicad_pcb" : null,
        log: explicit ? "workload.log" : null,
      },
      null,
      2,
    ) + "\n",
  );
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
