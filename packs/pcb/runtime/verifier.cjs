const { jsonArtifact } = require("./support.cjs");
const ID = "pcb.kicad.requirements.v1";
function verify({ result, artifacts, readArtifact, action }) {
  if (!result.executionSucceeded || !result.inputUnchanged)
    return {
      status: "insufficient_evidence",
      reason:
        "Independent KiCad DRC/readback failed, timed out, was cancelled or project inputs changed.",
      metrics: {},
    };
  const board = jsonArtifact(artifacts, readArtifact, "report.pcb.readback"),
    drc = jsonArtifact(artifacts, readArtifact, "report.pcb.drc");
  if (
    board.schemaVersion !== 1 ||
    !Array.isArray(board.bounds) ||
    !Array.isArray(board.footprints) ||
    !Array.isArray(drc.violations) ||
    !Array.isArray(drc.unconnected_items) ||
    !Array.isArray(drc.schematic_parity)
  )
    return {
      status: "insufficient_evidence",
      reason: "Unrecognized KiCad DRC report schema.",
      metrics: {},
    };
  const errors = drc.violations.filter((x) => x.severity === "error").length;
  const warnings = drc.violations.filter(
    (x) => x.severity === "warning",
  ).length;
  const near = (a, b) =>
    Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) < 0.001;
  const expect = action.inputs.expect;
  const checks = {
    nativeVersion:
      board.version === "10.0.6" &&
      drc.kicad_version === "10.0.6" &&
      drc.coordinate_units === "mm",
    drcErrors: errors === 0,
    unconnected: drc.unconnected_items.length === 0,
    noExclusions: drc.violations.every((v) =>
      ["error", "warning"].includes(v.severity),
    ),
    boardBounds:
      board.bounds.length === 4 &&
      expect.bounds.every((v, i) => near(board.bounds[i], v)),
  };
  for (const [i, e] of (expect.footprints || []).entries()) {
    const matches = board.footprints.filter((f) => f.reference === e.reference);
    checks["footprint" + i] =
      matches.length === 1 &&
      e.position.every((v, j) => near(matches[0].position[j], v)) &&
      (e.rotation === undefined || near(matches[0].rotation, e.rotation));
  }
  if (expect.maxWarnings !== undefined)
    checks.warnings = warnings <= expect.maxWarnings;
  const passed = Object.values(checks).every(Boolean);
  return {
    status: passed ? "passed" : "failed",
    reason: passed
      ? "Independent KiCad board readback satisfies explicit bounds/placement requirements; DRC has zero errors, zero unconnected items and no excluded violations. Warnings retained. No schematic/electrical/manufacturing signoff is implied."
      : "PCB requirement/DRC checks failed: " +
        Object.keys(checks)
          .filter((k) => !checks[k])
          .join(", "),
    metrics: {
      ...checks,
      errorCount: errors,
      warningCount: warnings,
      unconnectedCount: drc.unconnected_items.length,
    },
  };
}
module.exports = { ID, verify };
