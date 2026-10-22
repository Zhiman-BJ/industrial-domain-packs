const test = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs"),
  os = require("node:os"),
  path = require("node:path"),
  { execFileSync } = require("node:child_process");
const {
  pushPublication,
} = require("../scripts/runtime-publication-writeback.cjs");
function setup(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "pack-push-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const remote = path.join(root, "remote"),
    local = path.join(root, "local");
  fs.mkdirSync(local);
  const git = (...args) =>
    execFileSync("git", args, {
      cwd: local,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  execFileSync("git", ["init", "--bare", remote], { stdio: "ignore" });
  git("init", "-b", "feature");
  git("config", "user.name", "test");
  git("config", "user.email", "test@example.com");
  fs.writeFileSync(path.join(local, "source"), "A");
  git("add", ".");
  git("commit", "-m", "A");
  git("remote", "add", "origin", remote);
  git("push", "origin", "HEAD");
  const source = git("rev-parse", "HEAD");
  return {
    root,
    local,
    remote,
    git,
    source,
    commit(name) {
      fs.writeFileSync(path.join(local, "source"), name);
      git("add", ".");
      git("commit", "-m", name);
      return git("rev-parse", "HEAD");
    },
  };
}
test("publication pushes its single child commit to the exact source branch", (t) => {
  const s = setup(t),
    head = s.commit("B");
  pushPublication({ cwd: s.local, branch: "feature", source: s.source });
  assert.equal(
    s.git("ls-remote", "origin", "refs/heads/feature").split(/\s/)[0],
    head,
  );
});
test("source advancement refuses writeback without overwriting remote", (t) => {
  const s = setup(t);
  const other = s.commit("other");
  s.git("push", "origin", "HEAD");
  s.git("reset", "--hard", s.source);
  s.commit("publication");
  assert.throws(
    () =>
      pushPublication({ cwd: s.local, branch: "feature", source: s.source }),
    /moved/,
  );
  assert.equal(
    s.git("ls-remote", "origin", "refs/heads/feature").split(/\s/)[0],
    other,
  );
});
test("unrelated or multiple commits are not publication writeback", (t) => {
  const s = setup(t);
  s.commit("B");
  s.commit("C");
  assert.throws(
    () =>
      pushPublication({ cwd: s.local, branch: "feature", source: s.source }),
    /parent/,
  );
});
