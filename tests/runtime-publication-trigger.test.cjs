const test = require("node:test");
const assert = require("node:assert/strict");
const api = () => require("../scripts/runtime-publication-trigger.cjs");
function fixture() {
  const file = "packs/chip/releases/rtl-cpu/test.lock.json";
  return {
    subject: "chore: publish chip, freecad, pcb runtimes",
    email: "41898282+github-actions[bot]@users.noreply.github.com",
    parents: ["a".repeat(40)],
    files: [file, "content-lock.json", "package.json"],
    beforePackage: { name: "pack", files: ["lib/index.cjs"] },
    afterPackage: { name: "pack", files: ["lib/index.cjs", file] },
    locks: { [file]: { buildSourceCommit: "a".repeat(40) } },
  };
}
test("generated writeback is skipped without depending on PAT actor name", () =>
  assert.equal(api().isPublicationWriteback(fixture()), true));
for (const [name, change] of Object.entries({
  "ordinary commit": (v) => (v.subject = "fix: runtime"),
  "non-bot author": (v) => (v.email = "developer@example.org"),
  "runtime code changed": (v) => v.files.push("packs/chip/runtime/index.cjs"),
  "dependency changed": (v) =>
    (v.afterPackage.dependencies = { unexpected: "1" }),
  "wrong source": (v) =>
    (Object.values(v.locks)[0].buildSourceCommit = "b".repeat(40)),
  "merge commit": (v) => v.parents.push("b".repeat(40)),
  "no release lock": (v) => (v.files = ["content-lock.json"]),
  "removed package entry": (v) =>
    (v.afterPackage.files = v.afterPackage.files.slice(1)),
}))
  test(`does not skip ${name}`, () => {
    const v = fixture();
    change(v);
    assert.equal(api().isPublicationWriteback(v), false);
  });
function successfulJobs() {
  return {
    gate: { result: "success", outputs: { publish: "true" } },
    images: { result: "success" },
    check: { result: "success" },
    plan: { result: "success" },
    publish: { result: "skipped" },
    finish: { result: "success", outputs: { written: "success" } },
  };
}
test("merge gate requires successful writeback even when all tools were reused", () => {
  const needs = successfulJobs();
  assert.equal(api().publicationReady(needs), true);
  needs.finish.outputs.written = "skipped";
  assert.equal(api().publicationReady(needs), false);
});
for (const job of ["gate", "images", "check", "plan", "publish", "finish"])
  for (const status of ["failure", "cancelled"])
    test(`merge gate blocks ${job} ${status}`, () => {
      const needs = successfulJobs();
      needs[job].result = status;
      assert.equal(api().publicationReady(needs), false);
    });
test("validated generated writeback passes after readiness without recursive publication", () => {
  const needs = successfulJobs();
  needs.gate.outputs.publish = "false";
  for (const job of ["check", "plan", "publish", "finish"])
    needs[job] = { result: "skipped" };
  assert.equal(api().publicationReady(needs), true);
  needs.images.result = "failure";
  assert.equal(api().publicationReady(needs), false);
});
test("missing or unrecognized gate output cannot bypass publication", () => {
  const needs = successfulJobs();
  needs.gate.outputs = {};
  assert.equal(api().publicationReady(needs), false);
  assert.equal(api().publicationReady({}), false);
});
