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
function publicationReady(needs) {
  if (needs.gate?.result !== "success" || needs.images?.result !== "success")
    return false;
  if (needs.gate.outputs?.publish === "false") return true;
  return (
    needs.gate.outputs?.publish === "true" &&
    needs.check?.result === "success" &&
    needs.plan?.result === "success" &&
    ["success", "skipped"].includes(needs.publish?.result) &&
    needs.finish?.result === "success" &&
    needs.finish.outputs?.written === "success"
  );
}
function inspectWriteback(cwd = process.cwd()) {
  const { execFileSync } = require("node:child_process");
  const git = (...args) => execFileSync("git", args, {cwd, encoding:"utf8"}).trim();
  const parentsOf = (rev) => git("rev-list", "--parents", "-n", "1", rev).split(" ").slice(1);
  let revision = "HEAD";
  const headParents = parentsOf(revision);
  if (headParents.length === 2) {
    // Only the exact merge of a generated child of the unchanged source counts.
    revision = headParents[1];
    if (parentsOf(revision).join(" ") !== headParents[0] ||
        git("rev-parse", "HEAD^{tree}") !== git("rev-parse", `${revision}^{tree}`)) return null;
  }
  const parents = parentsOf(revision);
  if (parents.length !== 1) return null;
  const files = git("diff", "--name-only", `${revision}^`, revision).split("\n").filter(Boolean);
  const locks = Object.fromEntries(files.filter(file => lockPaths.has(file)).map(file => {
    try { return [file, JSON.parse(git("show", `${revision}:${file}`))]; }
    catch { return [file, null]; }
  }));
  const valid = isPublicationWriteback({
    subject: git("log", "-1", "--format=%s", revision),
    email: git("log", "-1", "--format=%ae", revision), parents, files,
    beforePackage: JSON.parse(git("show", `${revision}^:package.json`)),
    afterPackage: JSON.parse(git("show", `${revision}:package.json`)), locks,
  });
  if (!valid) return null;
  const environments = new Set(Object.keys(locks).map(file => file.split("/").at(-1).split(".")[0]));
  if (environments.size !== 1) return null;
  const environment = [...environments][0];
  if (headParents.length === 2 && environment !== "prod") return null;
  return { environment, source: parents[0] };
}
function selectWriteback(ref, generated) {
  if (ref?.startsWith("refs/heads/automation/runtime-prod/")) {
    if (!generated || generated.environment !== "prod" || ref !== `refs/heads/automation/runtime-prod/${generated.source}`)
      throw Error("Invalid production lock writeback branch");
    return generated;
  }
  const environment = ref === "refs/heads/main" ? "prod" : "test";
  return generated?.environment === environment ? generated : null;
}
function main() {
  const fs = require("node:fs");
  const generated = selectWriteback(process.env.PUBLICATION_REF, inspectWriteback());
  const skip = Boolean(generated);
  if (skip) {
    const pack = require("../lib/index.cjs");
    const environment = generated.environment;
    for (const [id, profile] of [
      ["chip-pack", "rtl-cpu"],
      ["freecad-pack", "freecad-linux-cpu"],
      ["pcb-pack", "pcb-verify-cpu"],
    ]) {
      const record = pack.getRuntimeRelease(id, profile, environment);
      if (record.buildSourceCommit !== generated.source)
        throw Error("Generated release source mismatch");
    }
  }
  fs.appendFileSync(process.env.GITHUB_OUTPUT, `publish=${!skip}\n`);
  console.log(
    skip
      ? "Generated lock writeback: ordinary checks only"
      : "Source change: check images and publish runtime",
  );
}
module.exports = { isPublicationWriteback, publicationReady, inspectWriteback, selectWriteback };
if (require.main === module) main();
