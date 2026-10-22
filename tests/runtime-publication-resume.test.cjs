const test = require("node:test"),
  assert = require("node:assert/strict");
const { findIntent } = require("../scripts/runtime-publication-resume.cjs");
const context = {
  owner: "o",
  repo: "r",
  source: "a".repeat(40),
  domain: "chip",
  environment: "test",
  runId: 20,
  runAttempt: 1,
};
function api(artifacts = [], jobs = []) {
  return {
    rest: {
      actions: {
        listArtifactsForRepo: "artifacts",
        listWorkflowRuns: "runs",
        listJobsForWorkflowRun: "jobs",
      },
    },
    paginate: async (method) =>
      method === "artifacts"
        ? artifacts
        : method === "runs"
          ? [{ id: 10 }]
          : jobs,
  };
}
const artifact = {
  id: 1,
  name: `runtime-intent-chip-test-${context.source}-10-1`,
  expired: false,
  workflow_run: { id: 10, head_sha: context.source },
};
test("later runs restore the earlier intent instead of rebuilding", async () => {
  assert.deepEqual(await findIntent(api([artifact]), context), {
    artifactId: 1,
    artifactName: artifact.name,
    runId: 10,
  });
});
test("expired intent blocks recreation", async () => {
  await assert.rejects(
    findIntent(api([{ ...artifact, expired: true }]), context),
    /expired/,
  );
});
test("missing artifact after a cloud attempt fails closed", async () => {
  await assert.rejects(
    findIntent(
      api(
        [],
        [
          {
            name: "Publish chip test",
            run_attempt: 1,
            steps: [
              {
                name: "Create or reconcile Tool and verify readback",
                status: "completed",
                conclusion: "failure",
              },
            ],
          },
        ],
      ),
      context,
    ),
    /unavailable/,
  );
});
test("build failure before any cloud attempt may start again", async () => {
  assert.equal(
    await findIntent(
      api(
        [],
        [
          {
            name: "Publish chip test",
            run_attempt: 1,
            steps: [
              {
                name: "Create or reconcile Tool and verify readback",
                status: "completed",
                conclusion: "skipped",
              },
            ],
          },
        ],
      ),
      context,
    ),
    null,
  );
});
test("other domains and later queued runs are not prior attempts", async () => {
  const a = api(
    [],
    [
      {
        name: "Publish pcb test",
        steps: [
          {
            name: "Create or reconcile Tool and verify readback",
            status: "completed",
            conclusion: "failure",
          },
        ],
      },
    ],
  );
  assert.equal(await findIntent(a, context), null);
});
