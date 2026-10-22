// Recognize only the publisher's own generated child commit. Ordinary PR checks
// still run on this commit; this guard only prevents recursive cloud publication.
const { isDeepStrictEqual } = require("node:util");
const lockPaths = new Set([
  "packs/chip/releases/rtl-cpu/test.lock.json",
  "packs/freecad/releases/freecad-linux-cpu/test.lock.json",
  "packs/pcb/releases/pcb-verify-cpu/test.lock.json",
  "packs/chip/releases/rtl-cpu/prod.lock.json",
  "packs/freecad/releases/freecad-linux-cpu/prod.lock.json",
  "packs/pcb/releases/pcb-verify-cpu/prod.lock.json",
]);
function isPublicationWriteback({
  subject,
  email,
  parents,
  files,
  beforePackage,
  afterPackage,
  locks,
}) {
  if (
    subject !== "chore: publish chip, freecad, pcb runtimes" ||
    email !== "41898282+github-actions[bot]@users.noreply.github.com" ||
    parents.length !== 1
  )
    return false;
  const changedLocks = files.filter((file) => lockPaths.has(file));
  if (
    !changedLocks.length ||
    files.some(
      (file) =>
        !lockPaths.has(file) &&
        !["content-lock.json", "package.json"].includes(file),
    )
  )
    return false;
  if (
    changedLocks.some((file) => locks[file]?.buildSourceCommit !== parents[0])
  )
    return false;
  const { files: beforeFiles, ...before } = beforePackage;
  const { files: afterFiles, ...after } = afterPackage;
  if (
    !isDeepStrictEqual(before, after) ||
    !Array.isArray(beforeFiles) ||
    !Array.isArray(afterFiles)
  )
    return false;
  return (
    beforeFiles.every((file) => afterFiles.includes(file)) &&
    afterFiles.every(
      (file) => beforeFiles.includes(file) || changedLocks.includes(file),
    )
  );
}
function main() {
  const fs = require("node:fs");
  const { execFileSync } = require("node:child_process");
  const git = (...args) =>
    execFileSync("git", args, { encoding: "utf8" }).trim();
  const parents = git("rev-list", "--parents", "-n", "1", "HEAD")
    .split(" ")
    .slice(1);
  let skip = false;
  if (parents.length === 1) {
    const files = git("diff", "--name-only", "HEAD^", "HEAD")
      .split("\n")
      .filter(Boolean);
    skip = isPublicationWriteback({
      subject: git("log", "-1", "--format=%s"),
      email: git("log", "-1", "--format=%ae"),
      parents,
      files,
      beforePackage: JSON.parse(git("show", "HEAD^:package.json")),
      afterPackage: JSON.parse(fs.readFileSync("package.json")),
      locks: Object.fromEntries(
        files
          .filter((file) => lockPaths.has(file))
          .map((file) => [
            file,
            fs.existsSync(file) ? JSON.parse(fs.readFileSync(file)) : null,
          ]),
      ),
    });
  }
  fs.appendFileSync(process.env.GITHUB_OUTPUT, `publish=${!skip}\n`);
  console.log(
    skip
      ? "Generated lock writeback: ordinary checks only"
      : "Source change: check images and publish runtime",
  );
}
module.exports = { isPublicationWriteback };
if (require.main === module) main();
