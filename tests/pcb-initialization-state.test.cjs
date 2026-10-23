const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

test("empty projects can discover setup without granting layout mutations", () => {
  const manifest = require("../packs/pcb/harness-pack.json");
  const core = manifest.capabilities.find((capability) => capability.id === "pcb.design.core");
  assert.deepEqual(core.stages, []);
  const tools = new Set(core.tools.map((tool) => tool.id));
  for (const name of ["new_project", "project_status", "set_requirements", "inspect_tool"])
    assert.ok(tools.has("pcb.design." + name));
  for (const name of ["add_track", "route_board", "update_pcb"])
    assert.ok(!tools.has("pcb.design." + name));
});

test("stage follows intent and native schematic before a board exists", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "pcb-initialization-"));
  const runtimeApi = {
    executeTask() {},
    runtimeFiles: {
      projectFile: (project, relative) => path.join(project, relative),
      readBytes: (file) => fs.readFileSync(file),
    },
  };
  const { stateProvider } = require("../packs/pcb/runtime/index.cjs")
    .createRuntimePlugin({ runtimeApi });
  const state = () => stateProvider({ projectDir: root }).stage;
  try {
    assert.equal(state(), null);
    fs.writeFileSync(path.join(root, "notes.txt"), "No CAD inputs yet.");
    assert.equal(state(), null);
    fs.writeFileSync(path.join(root, "spec.json"), JSON.stringify({ parts: [], nets: {} }));
    assert.equal(state(), "schematic");
    fs.unlinkSync(path.join(root, "spec.json"));
    fs.writeFileSync(path.join(root, "board.kicad_sch"), "(kicad_sch)");
    assert.equal(state(), "schematic");
    fs.writeFileSync(path.join(root, "board.kicad_pcb"), "(kicad_pcb)");
    assert.equal(state(), "layout");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
