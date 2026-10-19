const { jsonArtifact } = require("./support.cjs");

const ID = "pcb.kicad.requirements.v1";
const descriptor = {
  schemaVersion: "1",
  id: "pcb.kicad.verify",
  version: "0.1.0",
  risk: "mutating",
  verification: [ID],
};

function verify({ result, artifacts, readArtifact }) {
  if (!result?.executionSucceeded || result.inputUnchanged !== true)
    return {
      status: "insufficient_evidence",
      reason:
        "Independent KiCad DRC/readback failed, timed out, or project inputs changed.",
      metrics: {},
    };
  let report;
  let board;
  let drc;
  try {
    report = jsonArtifact(artifacts, readArtifact, "report.pcb.run");
    board = jsonArtifact(artifacts, readArtifact, "report.pcb.readback");
    drc = jsonArtifact(artifacts, readArtifact, "report.pcb.drc");
  } catch (error) {
    return {
      status: "insufficient_evidence",
      reason: error.message,
      metrics: {},
    };
  }
  const expect = result.expect;
  if (
    report.schemaVersion !== "1" ||
    report.profileId !== "pcb-verify-cpu" ||
    report.executionSucceeded !== true ||
    report.inputUnchanged !== true ||
    board.schemaVersion !== 1 ||
    !Array.isArray(board.bounds) ||
    !Array.isArray(board.footprints) ||
    !Array.isArray(drc.violations) ||
    !Array.isArray(drc.unconnected_items)
  )
    return {
      status: "insufficient_evidence",
      reason: "Unrecognized KiCad DRC/readback report schema.",
      metrics: {},
    };
  if (!expect || !Array.isArray(expect.bounds) || expect.bounds.length !== 4)
    return {
      status: "insufficient_evidence",
      reason: "Worker evidence omitted explicit board bounds.",
      metrics: {},
    };
  return verifyEvidence(board, drc, expect, report.toolVersion === "10.0.6");
}

// This entry is registered only by the trusted native Runtime plugin. The
// remote canonical entry above never falls back when its run report is absent.
function verifyLocal({ result, artifacts, readArtifact }) {
  if (!result?.executionSucceeded || result.inputUnchanged !== true)
    return {
      status: "insufficient_evidence",
      reason: "Independent KiCad DRC/readback failed, timed out, was cancelled or project inputs changed.",
      metrics: {},
    };
  let board;
  let drc;
  try {
    board = jsonArtifact(artifacts, readArtifact, "report.pcb.readback");
    drc = jsonArtifact(artifacts, readArtifact, "report.pcb.drc");
  } catch (error) {
    return {
      status: "insufficient_evidence",
      reason: error.message,
      metrics: {},
    };
  }
  if (
    !board ||
    board.schemaVersion !== 1 ||
    !Array.isArray(board.bounds) ||
    !Array.isArray(board.footprints) ||
    !drc ||
    !Array.isArray(drc.violations) ||
    !Array.isArray(drc.unconnected_items) ||
    !Array.isArray(drc.schematic_parity)
  )
    return {
      status: "insufficient_evidence",
      reason: "Unrecognized KiCad DRC report schema.",
      metrics: {},
    };
  const expect = result.expect;
  if (!expect || !Array.isArray(expect.bounds) || expect.bounds.length !== 4)
    return {
      status: "insufficient_evidence",
      reason: "Native evidence omitted explicit board bounds.",
      metrics: {},
    };
  return verifyEvidence(board, drc, expect, true, true);
}

function verifyEvidence(board, drc, expect, reportVersion, local = false) {
  const errors = drc.violations.filter((x) => x.severity === "error").length;
  const warnings = drc.violations.filter((x) => x.severity === "warning").length;
  const near = (a, b) =>
    Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) < 0.001;
  const checks = {
    nativeVersion:
      reportVersion &&
      board.version === "10.0.6" &&
      drc.kicad_version === "10.0.6" &&
      drc.coordinate_units === "mm",
    drcErrors: errors === 0,
    unconnected: drc.unconnected_items.length === 0,
    [local ? "noExclusions" : "noExcludedViolations"]: drc.violations.every((v) =>
      ["error", "warning"].includes(v.severity),
    ),
    boardBounds:
      board.bounds.length === 4 &&
      expect.bounds.every((v, i) => near(board.bounds[i], v)),
  };
  for (const [i, expected] of (expect.footprints || []).entries()) {
    const matches = board.footprints.filter(
      (footprint) => footprint.reference === expected.reference,
    );
    checks["footprint" + i] =
      matches.length === 1 &&
      Array.isArray(matches[0].position) &&
      expected.position.every((v, j) => near(matches[0].position[j], v)) &&
      (expected.rotation === undefined ||
        near(matches[0].rotation, expected.rotation));
  }
  if (expect.maxWarnings !== undefined)
    checks.warnings = warnings <= expect.maxWarnings;
  const passed = Object.values(checks).every(Boolean);
  return {
    status: passed ? "passed" : "failed",
    reason: passed
      ? local
        ? "Independent KiCad board readback satisfies explicit bounds/placement requirements; DRC has zero errors, zero unconnected items and no excluded violations. Warnings retained. No schematic/electrical/manufacturing signoff is implied."
        : "Independent KiCad board readback satisfies explicit bounds/placement requirements; DRC has zero errors and zero unconnected items. Warnings remain visible and may be bounded."
      : "PCB requirement/DRC checks failed: " +
        Object.keys(checks)
          .filter((key) => !checks[key])
          .join(", "),
    metrics: {
      ...checks,
      errorCount: errors,
      warningCount: warnings,
      unconnectedCount: drc.unconnected_items.length,
    },
  };
}

module.exports = { ID, descriptor, verify, verifyLocal };
