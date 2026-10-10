const s = require("./support.cjs");
const {
  fs,
  path,
  projectFile,
  hash,
  readBytes,
  sources,
  snapshot,
  unchanged,
  binary,
  appRoot,
  phase,
  outputs,
  timeout,
  exact,
  relative,
  number,
  applyFiles,
} = s;
const { ID, verify } = require("./verifier.cjs");
const benchSnapshot = require("./bench-upstream.json");
const VERSION = "0.2.0-bench.2";
const BENCH_PROFILE =
  "Executes inside the pinned PCB-bench Linux container (image " +
  benchSnapshot.imageTag +
  ", upstream commit " +
  benchSnapshot.sourceCommit.slice(0, 12) +
  "). Local prerequisites: an authorized PCB-bench checkout in INDUSTRIAL_HARNESS_PCB_BENCH_DIR, Docker with the pinned image, and the gateway Python environment (uv sync --frozen --no-dev inside packs/pcb).";
const guides = {
  "pcb.kicad.edit": {
    inputs: {
      file: "board.kicad_pcb",
      expectedSha256: "current SHA-256",
      rectangle: { origin: [0, 0], size: [40, 30] },
      moves: [{ reference: "H1", position: [10, 10], rotation: 0 }],
      timeoutMs: 60000,
    },
    description:
      "Change a single rectangular board outline or move existing footprints with native KiCad on a protected snapshot, then hash-checked source replacement. No library authoring, routing or private PCB-bench environment. Rerun pcb.kicad.verify afterwards.",
  },
  "pcb.kicad.verify": {
    inputs: {
      file: "board.kicad_pcb",
      expect: {
        bounds: [0, 0, 40, 30],
        footprints: [{ reference: "H1", position: [10, 10] }],
        maxWarnings: 10,
      },
      timeoutMs: 60000,
    },
    description:
      "Run KiCad DRC with all severities, then independent native board readback against explicit bounds and footprint expectations. Require zero errors and unconnected items; warnings are recorded and may be bounded. Schematic parity/ERC/electrical behavior are outside this first task profile.",
  },
};
function vector(value, positive = false) {
  if (!Array.isArray(value) || value.length !== 2)
    throw Error("Expected a 2D vector.");
  value.forEach((n) => {
    number(n);
    if (positive && n <= 0) throw Error("Board size must be positive.");
  });
}
function placements(items) {
  if (!Array.isArray(items) || items.length > 64)
    throw Error("Use at most 64 footprint placements.");
  const seen = new Set();
  for (const m of items) {
    exact(m, ["reference", "position", "rotation"]);
    if (
      typeof m.reference !== "string" ||
      !/^\w[\w.-]{0,63}$/.test(m.reference) ||
      seen.has(m.reference)
    )
      throw Error("Expected unique footprint references.");
    seen.add(m.reference);
    vector(m.position);
    if (m.rotation !== undefined) number(m.rotation);
  }
}
function validate(operation, inputs) {
  exact(
    inputs,
    operation === "edit"
      ? ["file", "expectedSha256", "rectangle", "moves", "timeoutMs"]
      : ["file", "expect", "timeoutMs"],
  );
  relative(inputs.file, ".kicad_pcb");
  timeout(inputs);
  if (operation === "edit") {
    if (!/^[a-f0-9]{64}$/.test(inputs.expectedSha256 || ""))
      throw Error("Supply the current board SHA-256.");
    if (inputs.rectangle) {
      exact(inputs.rectangle, ["origin", "size"]);
      vector(inputs.rectangle.origin);
      vector(inputs.rectangle.size, true);
    }
    if (inputs.moves) placements(inputs.moves);
    if (!inputs.rectangle && !inputs.moves?.length)
      throw Error("Supply a rectangle or footprint move.");
  } else {
    exact(inputs.expect, ["bounds", "footprints", "maxWarnings"]);
    if (
      !Array.isArray(inputs.expect.bounds) ||
      inputs.expect.bounds.length !== 4
    )
      throw Error("Supply explicit board [x,y,width,height] bounds.");
    inputs.expect.bounds.forEach(number);
    if (inputs.expect.footprints) placements(inputs.expect.footprints);
    if (
      inputs.expect.maxWarnings !== undefined &&
      (!Number.isInteger(inputs.expect.maxWarnings) ||
        inputs.expect.maxWarnings < 0)
    )
      throw Error("maxWarnings must be nonnegative.");
  }
}
function dependencies(environment) {
  const cli = binary(
    environment,
    "INDUSTRIAL_HARNESS_KICAD_CLI",
    process.platform === "darwin"
      ? "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
      : "kicad-cli",
  );
  const root = appRoot(cli);
  const python = binary(
    environment,
    "INDUSTRIAL_HARNESS_KICAD_PYTHON",
    process.platform === "darwin"
      ? path.join(
          root,
          "Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9",
        )
      : "python3",
  );
  return {
    cli,
    python,
    roots: [
      root,
      appRoot(python),
      __dirname,
      ...(process.platform === "darwin" ? ["/private/etc/ssl"] : []),
    ],
  };
}
const benchTools = (environment) =>
  benchSnapshot.tools.map((tool) => ({
  descriptor: {
    schemaVersion: "1",
    id: tool.id,
    version: VERSION,
    risk: tool.risk,
    ...(tool.risk === "mutating" ? { effect: "inputs" } : {}),
    verification: tool.verification || [],
  },
  guide: {
    description: tool.summary + " " + BENCH_PROFILE,
  },
  execute: async ({ projectDir, inputs, signal }) => {
    const { gatewaySession } = require("./bench-gateway.cjs");
    const session = await gatewaySession({ projectDir, environment });
    const payload = await session.callTool(
      "domain_tool_call",
      { toolId: tool.id, arguments: inputs ?? {} },
      signal,
    );
    // The full observation payload rides in diagnostics: bench observations
    // (paged requirements, board collections, receipts) are the model-facing
    // result of this action. callTool throws on gateway/native errors, so
    // reaching here means the transport accepted the call.
    return {
      executionSucceeded: true,
      diagnostics: [JSON.stringify(payload)],
      artifacts: [],
      toolVersion: VERSION,
    };
  },
}));
function createRuntimePlugin({ environment = process.env, runtimeApi } = {}) {
  s.configure(runtimeApi);
  return {
    stateProvider: ({ projectDir }) => {
      const inputs = sources(projectDir);
      return {
        stage: Object.keys(inputs).some((n) => n.endsWith(".kicad_pcb"))
          ? "layout"
          : null,
        inputHashes: {
          ...inputs,
          "runtime-identity/pcb": hash(
            JSON.stringify(
              Object.fromEntries(
                fs
                  .readdirSync(__dirname)
                  .sort()
                  .filter((n) => fs.statSync(path.join(__dirname, n)).isFile())
                  .map((n) => [
                    n,
                    hash(fs.readFileSync(path.join(__dirname, n))),
                  ]),
              ),
            ),
          ),
        },
      };
    },
    tools: [
      ...["edit", "verify"].map((operation) => ({
      descriptor: {
        schemaVersion: "1",
        id: "pcb.kicad." + operation,
        version: VERSION,
        risk: "mutating",
        ...(operation === "edit" ? { effect: "inputs" } : {}),
        verification: [operation === "edit" ? "pcb.kicad.source.v1" : ID],
      },
      guide: guides["pcb.kicad." + operation],
      execute: async ({ projectDir, inputs, action, signal }) => {
        if (process.platform !== "darwin" || process.arch !== "arm64")
          throw Error(
            "This native profile is qualified only on macOS Apple Silicon.",
          );
        validate(operation, inputs);
        const source = projectFile(projectDir, inputs.file),
          bytes = readBytes(source);
        if (operation === "edit" && hash(bytes) !== inputs.expectedSha256)
          throw Error("Board input hash is stale.");
        const ctx = snapshot(projectDir, action),
          native = dependencies(environment),
          board = path.join(ctx.input, inputs.file),
          runs = [];
        const nativeScript = path.join(__dirname, "native.py");
        let succeeded = false;
        const kinds = {
          "inputs.json": "input.pcb.manifest",
          ["input/" + inputs.file]: "input.pcb.board",
        };
        if (operation === "edit") {
          const request = path.join(ctx.work, "request.json");
          fs.writeFileSync(request, JSON.stringify(inputs));
          const output = path.join(ctx.work, "board.kicad_pcb");
          runs.push(
            await phase(
              ctx,
              "edit",
              [native.python, nativeScript, "edit", board, output, request],
              environment,
              signal,
              timeout(inputs),
              native.roots,
            ),
          );
          succeeded = runs[0].status === "COMPLETED" && !signal.aborted;
          if (succeeded && unchanged(projectDir, ctx.hashes)) {
            applyFiles(projectDir, [
              {
                path: inputs.file,
                expectedSha256: inputs.expectedSha256,
                content: readBytes(output).toString("utf8"),
              },
            ]);
          } else succeeded = false;
          kinds["work/board.kicad_pcb"] = "source.pcb.board";
          kinds["work/request.json"] = "input.pcb.edit";
        } else {
          runs.push(
            await phase(
              ctx,
              "drc",
              [
                native.cli,
                "pcb",
                "drc",
                "--format",
                "json",
                "--severity-all",
                "--output",
                path.join(ctx.work, "drc.json"),
                board,
              ],
              environment,
              signal,
              timeout(inputs),
              native.roots,
            ),
          );
          if (runs[0].status === "COMPLETED" && !signal.aborted)
            runs.push(
              await phase(
                ctx,
                "readback",
                [
                  native.python,
                  nativeScript,
                  "read",
                  board,
                  path.join(ctx.work, "readback.json"),
                ],
                environment,
                signal,
                timeout(inputs),
                native.roots,
              ),
            );
          succeeded =
            runs.length === 2 && runs.every((r) => r.status === "COMPLETED");
          kinds["work/drc.json"] = "report.pcb.drc";
          kinds["work/readback.json"] = "report.pcb.readback";
        }
        for (const name of operation === "edit"
          ? ["edit"]
          : ["drc", "readback"]) {
          kinds[name + ".json"] = "receipt.native";
          kinds[name + ".log"] = "log.tool";
        }
        return {
          executionSucceeded: succeeded,
          inputUnchanged:
            operation === "edit"
              ? undefined
              : unchanged(projectDir, ctx.hashes),
          diagnostics: runs.map((r) => r.status + " " + r.log.slice(-2000)),
          artifacts: outputs(ctx, kinds),
          toolVersion: VERSION,
        };
      },
    })),
      ...benchTools(environment),
    ],
    verifiers: {
      [ID]: verify,
      "pcb.kicad.source.v1": () => ({
        status: "not_run",
        reason:
          "Source edit preserved; rerun independent board requirements and DRC.",
        metrics: {},
      }),
      "pcb.bench.receipts.v1": () => ({
        status: "not_run",
        reason:
          "Bench controller receipts live inside the container session; execution is not acceptance.",
        metrics: {},
      }),
    },
  };
}
module.exports = { createRuntimePlugin, guides, validate };
