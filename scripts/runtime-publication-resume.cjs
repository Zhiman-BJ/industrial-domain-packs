// Recover uploaded intent before any rebuild. GitHub artifacts are not an
// infinite-retention journal: if evidence disappeared after a cloud attempt,
// fail closed instead of changing the image digest and creating another Tool.
async function findIntent(
  github,
  { owner, repo, source, domain, environment, runId, runAttempt },
) {
  const actions = github.rest.actions,
    base = { owner, repo, per_page: 100 };
  const prefix = `runtime-intent-${domain}-${environment}-${source}-`;
  const artifacts = await github.paginate(actions.listArtifactsForRepo, base);
  const candidates = artifacts
    .filter(
      (a) =>
        a.name.startsWith(prefix) &&
        a.workflow_run?.head_sha === source &&
        a.workflow_run.id <= runId,
    )
    .sort((a, b) => b.id - a.id);
  if (candidates.length) {
    const artifact = candidates[0];
    if (artifact.expired)
      throw Error(
        "Prior publication intent expired; reconcile the previous cloud attempt before publishing",
      );
    return {
      artifactId: artifact.id,
      artifactName: artifact.name,
      runId: artifact.workflow_run.id,
    };
  }
  const runs = await github.paginate(actions.listWorkflowRuns, {
    ...base,
    workflow_id: "publish-runtime.yml",
    head_sha: source,
  });
  for (const run of runs) {
    if (run.id > runId || (run.id === runId && runAttempt === 1)) continue;
    const jobs = await github.paginate(actions.listJobsForWorkflowRun, {
      ...base,
      run_id: run.id,
      filter: "all",
    });
    for (const job of jobs) {
      if (
        job.name !== `Publish ${domain} ${environment}` ||
        (run.id === runId && job.run_attempt >= runAttempt)
      )
        continue;
      const step = job.steps?.find(
        (s) => s.name === "Create or reconcile Tool and verify readback",
      );
      if (
        step &&
        ["in_progress", "completed"].includes(step.status) &&
        step.conclusion !== "skipped"
      )
        throw Error(
          "Prior publication intent unavailable after a cloud attempt; reconcile manually rather than rebuild",
        );
    }
  }
  return null;
}
module.exports = { findIntent };
