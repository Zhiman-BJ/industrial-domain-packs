// Dependency preparation only; engineering execution uses the injected Harness backend.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const { Readable } = require("node:stream");
const { pipeline } = require("node:stream/promises");
const lock = require("./native-dependency.json");
function run(command, args) {
  const r = spawnSync(command, args, { stdio: "inherit" });
  if (r.error || r.status !== 0)
    throw r.error || Error("Dependency extraction failed: " + command);
}
async function main() {
  if (process.platform !== "darwin" || process.arch !== "arm64")
    throw Error("Only macOS Apple Silicon is qualified.");
  if (!process.argv[2])
    throw Error("Supply a new dependency preparation directory.");
  const root = path.resolve(process.argv[2]);
  fs.mkdirSync(root, { recursive: true });
  const archive = path.join(root, lock.archive);
  if (!fs.existsSync(archive)) {
    const response = await fetch(lock.url);
    if (!response.ok)
      throw Error("Official dependency download failed: " + response.status);
    const temporary = archive + ".partial";
    await pipeline(
      Readable.fromWeb(response.body),
      fs.createWriteStream(temporary, { flags: "wx" }),
    );
    fs.renameSync(temporary, archive);
  }
  const h = crypto.createHash("sha256");
  for await (const b of fs.createReadStream(archive)) h.update(b);
  if (h.digest("hex") !== lock.sha256)
    throw Error("Native dependency SHA-256 mismatch.");
  const command = path.join(root, lock.command);
  if (!fs.existsSync(command)) {
    if (lock.kind === "zip") run("ditto", ["-x", "-k", archive, root]);
    else {
      const mount = path.join(root, "mount");
      run("hdiutil", [
        "attach",
        "-readonly",
        "-nobrowse",
        "-mountpoint",
        mount,
        archive,
      ]);
      try {
        run("ditto", [
          path.join(mount, lock.bundle),
          path.join(root, "KiCad.app"),
        ]);
      } finally {
        run("hdiutil", ["detach", mount]);
      }
    }
  }
  if (!fs.existsSync(command))
    throw Error("Native executable missing after extraction.");
  const variables = { [lock.variable]: command };
  if (lock.kind === "dmg")
    variables.INDUSTRIAL_HARNESS_KICAD_PYTHON = path.join(
      root,
      "KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9",
    );
  fs.writeFileSync(
    path.join(root, "provenance.json"),
    JSON.stringify({ ...lock, variables }, null, 2),
  );
  if (process.env.GITHUB_ENV)
    for (const [key, value] of Object.entries(variables))
      fs.appendFileSync(process.env.GITHUB_ENV, key + "=" + value + "\n");
  console.log(JSON.stringify(variables));
}
main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
