const { jsonArtifact } = require("./support.cjs");
const ID = "godot.scene.requirements.v1";
function verify({ result, artifacts, readArtifact, action }) {
  if (!result.executionSucceeded || !result.inputUnchanged)
    return {
      status: "insufficient_evidence",
      reason:
        "Native import/readback/frame run failed, timed out, was cancelled, or inputs changed. Inspect the native logs.",
      metrics: {},
    };
  const read = jsonArtifact(artifacts, readArtifact, "report.godot.readback");
  const run = jsonArtifact(artifacts, readArtifact, "report.godot.frames");
  const near = (a, b) =>
    Array.isArray(b)
      ? Array.isArray(a) &&
        a.length === b.length &&
        b.every((v, i) => near(a[i], v))
      : typeof b === "number"
        ? typeof a === "number" && Math.abs(a - b) <= 0.00001
        : a === b;
  const checks = {
    readbackComplete: read.schemaVersion === 1 && read.frames === 0,
    framesComplete:
      run.schemaVersion === 1 && run.frames === action.inputs.frames,
    version: [read.engine, run.engine].every(
      (e) =>
        e?.major === 4 &&
        e?.minor === 7 &&
        e?.patch === 2 &&
        e?.build === "official" &&
        e?.status === "stable",
    ),
  };
  for (const [index, expectation] of action.inputs.expect.entries()) {
    for (const [label, nodes] of [
      ["read", read.final],
      ["initial", run.initial],
      ["final", run.final],
    ]) {
      const matches = nodes?.filter((n) => n.path === expectation.node) || [];
      checks[label + index] =
        matches.length === 1 &&
        near(matches[0].properties[expectation.property], expectation.value);
    }
  }
  const passed = Object.values(checks).every(Boolean);
  return {
    status: passed ? "passed" : "failed",
    reason: passed
      ? "Independent Godot import, resolved geometry/property readback and exact frame run satisfy every explicit expectation. This verifies the supplied structural properties, not unrestricted gameplay."
      : "Godot task requirements failed: " +
        Object.keys(checks)
          .filter((k) => !checks[k])
          .join(", "),
    metrics: { ...checks, frames: run.frames },
  };
}
module.exports = { ID, verify };
