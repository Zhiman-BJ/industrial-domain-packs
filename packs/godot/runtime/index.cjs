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
  runDirectory,
  applyFiles,
} = s;
const { edit } = require("./edit.cjs");
const { ID, verify } = require("./verifier.cjs");
const VERSION = "0.2.1";
const guides = {
  "godot.scene.edit": {
    inputs: {
      file: "scene.tscn",
      expectedSha256: "current SHA-256",
      changes: [
        {
          section: "node:Box",
          property: "position",
          value: { type: "Vector3", value: [1, 2, 3] },
        },
      ],
    },
    description:
      "Apply typed node transforms/visibility or BoxMesh size to a text scene after a source-hash check. Transform vectors must match the node family: Vector3 on 3D nodes, Vector2 on 2D nodes; untyped or mismatched values are rejected before the scene changes. Preserves before/after artifacts and invalidates acceptance. Run godot.scene.verify with explicit expectations afterwards.",
  },
  "godot.scene.verify": {
    inputs: {
      file: "scene.tscn",
      frames: 10,
      expect: [{ node: "Box", property: "mesh_size", value: [2, 3, 4] }],
      timeoutMs: 60000,
    },
    description:
      "Offline protected snapshot: native import, separate resolved readback and exact 1..180-frame run. Every explicit position/rotation/scale/visibility/BoxMesh-size expectation must match before and after the run. No acceptance from process exit alone.",
  },
};
function validateExpect(inputs) {
  exact(inputs, ["file", "frames", "expect", "timeoutMs"]);
  relative(inputs.file, ".tscn");
  timeout(inputs);
  if (
    !Number.isInteger(inputs.frames) ||
    inputs.frames < 1 ||
    inputs.frames > 180
  )
    throw Error("frames must be 1..180.");
  if (
    !Array.isArray(inputs.expect) ||
    !inputs.expect.length ||
    inputs.expect.length > 32
  )
    throw Error("Supply 1..32 explicit scene expectations.");
  for (const e of inputs.expect) {
    exact(e, ["node", "property", "value"]);
    if (
      typeof e.node !== "string" ||
      !/^\.(?:\/\w+)*$|^[\w .-]+(?:\/[\w .-]+)*$/.test(e.node) ||
      e.node.split("/").includes("..")
    )
      throw Error("Use a bounded node path.");
    if (
      ![
        "position",
        "rotation_degrees",
        "scale",
        "visible",
        "mesh_size",
      ].includes(e.property)
    )
      throw Error("Unsupported property expectation.");
    if (e.property === "visible") {
      if (typeof e.value !== "boolean")
        throw Error("visible expects a boolean.");
    } else {
      if (!Array.isArray(e.value) || ![2, 3].includes(e.value.length))
        throw Error("Expected a vector.");
      e.value.forEach(number);
    }
  }
}
function createRuntimePlugin({ environment = process.env, runtimeApi } = {}) {
  s.configure(runtimeApi);
  const stateProvider = ({ projectDir }) => {
    const inputs = sources(projectDir);
    return {
      stage: inputs["project.godot"] ? "development" : null,
      inputHashes: {
        ...inputs,
        "runtime-identity/godot": hash(
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
  };
  return {
    stateProvider,
    workspaceProtectedPaths: (projectDir) => [path.join(projectDir, ".godot")],
    tools: [
      {
        descriptor: {
          schemaVersion: "1",
          id: "godot.scene.edit",
          version: VERSION,
          risk: "mutating",
          effect: "inputs",
          verification: ["godot.scene.source.v1"],
        },
        guide: guides["godot.scene.edit"],
        execute: async ({ projectDir, inputs, action }) => {
          exact(inputs, ["file", "expectedSha256", "changes"]);
          relative(inputs.file, ".tscn");
          const file = projectFile(projectDir, inputs.file, [
              path.join(projectDir, ".godot"),
            ]),
            before = readBytes(file),
            text = edit(before.toString("utf8"), inputs.changes);
          if (hash(before) !== inputs.expectedSha256)
            throw Error("Scene input hash is stale.");
          const directory = runDirectory(projectDir, action.id);
          fs.writeFileSync(path.join(directory, "before.tscn"), before);
          applyFiles(projectDir, [
            {
              path: inputs.file,
              expectedSha256: inputs.expectedSha256,
              content: text,
            },
          ]);
          fs.writeFileSync(path.join(directory, "after.tscn"), text);
          return {
            executionSucceeded: true,
            diagnostics: [
              "Scene source changed; engineering verification must be rerun.",
            ],
            artifacts: outputs(
              { directory },
              {
                "before.tscn": "input.godot.scene",
                "after.tscn": "source.godot.scene",
              },
            ),
          };
        },
      },
      {
        descriptor: {
          schemaVersion: "1",
          id: "godot.scene.verify",
          version: VERSION,
          risk: "mutating",
          verification: [ID],
        },
        guide: guides["godot.scene.verify"],
        execute: async ({ projectDir, inputs, action, signal }) => {
          if (process.platform !== "darwin" || process.arch !== "arm64")
            throw Error(
              "This native profile is qualified only on macOS Apple Silicon.",
            );
          validateExpect(inputs);
          projectFile(projectDir, "project.godot");
          projectFile(projectDir, inputs.file, [
            path.join(projectDir, ".godot"),
          ]);
          const ctx = snapshot(projectDir, action),
            command = binary(
              environment,
              "INDUSTRIAL_HARNESS_GODOT_CMD",
              process.platform === "darwin"
                ? "/Applications/Godot.app/Contents/MacOS/Godot"
                : "godot",
            );
          const project = path.join(ctx.work, "project");
          fs.cpSync(ctx.input, project, { recursive: true });
          const fontDir = path.join(
            require("node:os").homedir(),
            "Library/Fonts",
          );
          const deps = [
            appRoot(command),
            __dirname,
            ...(process.platform === "darwin" && fs.existsSync(fontDir)
              ? [fontDir]
              : []),
          ];
          const runs = [];
          runs.push(
            await phase(
              ctx,
              "import",
              [
                command,
                "--headless",
                "--text-driver",
                "Dummy",
                "--path",
                project,
                "--import",
                "--quit",
              ],
              environment,
              signal,
              timeout(inputs),
              deps,
            ),
          );
          const okay = require("./diagnostics.cjs").completed;
          if (okay(runs[0]) && !signal.aborted)
            runs.push(
              await phase(
                ctx,
                "readback",
                [
                  command,
                  "--headless",
                  "--text-driver",
                  "Dummy",
                  "--path",
                  project,
                  "--script",
                  path.join(__dirname, "readback.gd"),
                  "--",
                  inputs.file,
                  path.join(ctx.work, "readback.json"),
                  "0",
                ],
                environment,
                signal,
                timeout(inputs),
                deps,
              ),
            );
          if (runs.length === 2 && runs.every(okay) && !signal.aborted)
            runs.push(
              await phase(
                ctx,
                "frames",
                [
                  command,
                  "--headless",
                  "--text-driver",
                  "Dummy",
                  "--path",
                  project,
                  "--script",
                  path.join(__dirname, "readback.gd"),
                  "--",
                  inputs.file,
                  path.join(ctx.work, "frames.json"),
                  String(inputs.frames),
                ],
                environment,
                signal,
                timeout(inputs),
                deps,
              ),
            );
          const kinds = {
            "inputs.json": "input.godot.manifest",
            "work/readback.json": "report.godot.readback",
            "work/frames.json": "report.godot.frames",
          };
          for (const name of ["import", "readback", "frames"]) {
            kinds[name + ".json"] = "receipt.native";
            kinds[name + ".log"] = "log.tool";
          }
          kinds["input/" + inputs.file] = "input.godot.scene";
          kinds["input/project.godot"] = "input.godot.project";
          return {
            executionSucceeded: runs.length === 3 && runs.every(okay),
            inputUnchanged: unchanged(projectDir, ctx.hashes),
            diagnostics: runs.map((r) => r.status + " " + r.log.slice(-2000)),
            artifacts: outputs(ctx, kinds),
            toolVersion: VERSION,
          };
        },
      },
    ],
    verifiers: {
      [ID]: verify,
      "godot.scene.source.v1": () => ({
        status: "not_run",
        reason:
          "Source edit preserved; engineering requirements must be independently verified.",
        metrics: {},
      }),
    },
  };
}
module.exports = { createRuntimePlugin, guides, validateExpect };
