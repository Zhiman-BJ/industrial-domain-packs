const test = require("node:test"),
  assert = require("node:assert/strict");
const fs = require("node:fs"),
  os = require("node:os"),
  path = require("node:path");
const {
  publicationMatrix,
} = require("../scripts/runtime-publication-matrix.cjs");
test("only changed packs enter publication; all-reuse is empty and incomplete checks fail", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "publication-check-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const write = (domain, needsPublication) => {
    const folder = path.join(dir, `runtime-check-${domain}-123`);
    fs.mkdirSync(folder, { recursive: true });
    fs.writeFileSync(
      path.join(folder, "check.json"),
      JSON.stringify({ domain, needsPublication }),
    );
  };
  write("chip", false);
  write("freecad", true);
  assert.throws(() => publicationMatrix(dir, "123"), /ENOENT/);
  write("pcb", false);
  assert.deepEqual(publicationMatrix(dir, "123"), ["freecad"]);
  write("freecad", false);
  assert.deepEqual(publicationMatrix(dir, "123"), []);
  write("pcb", "false");
  assert.throws(
    () => publicationMatrix(dir, "123"),
    /Invalid publication check/,
  );
});
