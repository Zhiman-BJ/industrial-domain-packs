const descriptor = {
  schemaVersion: "1",
  id: "chip.rtl.verify",
  version: "0.6.1-core.2",
  risk: "mutating",
  verification: ["chip.rtl.assertions"],
};
const verifier = ({ result, artifacts, readArtifact }) => {
  const logs = artifacts
    .filter((item) => item.kind === "log.tool")
    .map((item) => readArtifact(item).toString("utf8"))
    .join("\n");
  const report = artifacts.find((item) => item.kind === "report.simulation");
  const wave = artifacts.find((item) => item.kind === "waveform.vcd");
  const assertionFailure = /%(?:Error|Fatal).*Assertion failed/i.test(logs);
  if (assertionFailure)
    return {
      status: "failed",
      reason: "Real Verilator assertion failed; inspect collected tool logs.",
      metrics: { assertionFailure: true },
    };
  if (!report || !wave || !result.nativeVerification)
    return {
      status: "insufficient_evidence",
      reason:
        "Simulation completion report, VCD or native verifier evidence is missing.",
      metrics: {},
    };
  const complete = readArtifact(report)
    .toString("utf8")
    .includes("EDA_SIMULATION_FINISHED");
  const vcd = readArtifact(wave).toString("utf8");
  const genuineWave =
    /\$enddefinitions\s+\$end/.test(vcd) &&
    /#\d+/.test(vcd) &&
    /\$var/.test(vcd);
  const passed =
    complete &&
    genuineWave &&
    result.nativeVerification.status === "PASS" &&
    !/%Error/.test(logs);
  return {
    status: passed ? "passed" : "failed",
    reason: passed
      ? "Assertions enabled; testbench reached $finish and produced a real VCD. Functional coverage remains testbench-defined."
      : "Simulation evidence failed the assertion/completion/VCD checks.",
    metrics: {
      completed: complete,
      waveformPresent: genuineWave,
      assertionFailure: false,
    },
  };
};
module.exports = { descriptor, verifier };
