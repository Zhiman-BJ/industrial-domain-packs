const { execFileSync } = require("node:child_process");
function pushPublication({ cwd, branch, source }) {
  const git = (...args) =>
    execFileSync("git", args, {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  git("check-ref-format", "--branch", branch);
  if (!/^[a-f0-9]{40}$/.test(source)) throw Error("Invalid source commit");
  if (
    git("rev-parse", "HEAD^") !== source ||
    git("rev-list", "--parents", "-n", "1", "HEAD").split(" ").length !== 2
  )
    throw Error("Publication parent differs from source");
  const ref = "refs/heads/" + branch;
  if (git("ls-remote", "--refs", "origin", ref).split(/\s/)[0] !== source)
    throw Error(
      "Source branch moved; restart publication against current source",
    );
  // CAS protects the check/push race, including branch rewinds. HEAD has exactly
  // one parent, source, so the only permitted update is its fast-forward child.
  git("push", `--force-with-lease=${ref}:${source}`, "origin", `HEAD:${ref}`);
}
module.exports = { pushPublication };
