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
function pushProductionBranch({cwd, source}) {
  const git = (...args) => execFileSync("git", args, {cwd, encoding:"utf8", stdio:["ignore","pipe","pipe"]}).trim();
  if (!/^[a-f0-9]{40}$/.test(source)) throw Error("Invalid source commit");
  if (git("rev-list", "--parents", "-n", "1", "HEAD").split(" ").slice(1).join(" ") !== source)
    throw Error("Publication parent differs from source");
  if (git("ls-remote", "--refs", "origin", "refs/heads/main").split(/\s/)[0] !== source)
    throw Error("Source branch moved; restart publication against current main");
  const branch = `automation/runtime-prod/${source}`;
  const ref = `refs/heads/${branch}`;
  const existing = git("ls-remote", "--refs", "origin", ref).split(/\s/)[0];
  if (existing && existing !== git("rev-parse", "HEAD")) throw Error("Production writeback branch already differs; inspect its existing PR");
  if (!existing) git("push", `--force-with-lease=${ref}:`, "origin", `HEAD:${ref}`);
  return branch;
}
async function openProductionPR({github, owner, repo, source, head}) {
  if (![source, head].every(sha => /^[a-f0-9]{40}$/.test(sha))) throw Error("Invalid publication commit");
  const branch = `automation/runtime-prod/${source}`;
  const {data: existing} = await github.rest.pulls.list({owner, repo, head:`${owner}:${branch}`, base:"main", state:"open"});
  const pr = existing[0] || (await github.rest.pulls.create({
    owner, repo, head:branch, base:"main",
    title:"chore: update prod runtime locks",
    body:`Published prod runtime records for source ${source}. Generated locks and final content hash only. Required checks must pass before automatic merge. Review status: generated output validated by publication and PR gates.`,
  })).data;
  if (pr.head.sha !== head) throw Error("Production PR head differs from published commit");
  await github.graphql(`mutation($id: ID!) {
    enablePullRequestAutoMerge(input:{pullRequestId:$id, mergeMethod:MERGE}) { pullRequest { number } }
  }`, {id:pr.node_id});
  return pr.html_url;
}
module.exports = { pushPublication, pushProductionBranch, openProductionPR };
