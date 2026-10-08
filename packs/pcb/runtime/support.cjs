const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
let runtimeApi;
function configure(api) {
  if (!api || typeof api.executeTask !== "function" || !api.runtimeFiles)
    throw Error("This Pack requires the Harness trusted Runtime API.");
  runtimeApi = api;
}
const core = () => {
  if (!runtimeApi)
    throw Error(
      "Create the installed Pack through the Harness Runtime factory.",
    );
  return runtimeApi;
};
const executeTask = (...args) => core().executeTask(...args);
const projectFile = (...args) => core().runtimeFiles.projectFile(...args);
const readBytes = (...args) => core().runtimeFiles.readBytes(...args);
const runDirectory = (...args) => core().runtimeFiles.runDirectory(...args);
const applyFiles = (...args) => core().runtimeFiles.applyFiles(...args);
const hash = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const ignored = new Set([
  ".git",
  ".godot",
  ".harness-runs",
  "node_modules",
  ".venv",
  "__pycache__",
]);
function sources(projectDir) {
  const result = {};
  let bytes = 0,
    count = 0;
  function visit(relative, depth) {
    if (depth > 24) throw Error("Project input scan limit exceeded.");
    for (const name of fs.readdirSync(path.join(projectDir, relative)).sort()) {
      if (ignored.has(name)) continue;
      if (!relative && name === "runtime-identity")
        throw Error(
          "runtime-identity is reserved for the execution/Verifier identity.",
        );
      if (++count > 10000) throw Error("Project input scan limit exceeded.");
      const rel = relative ? relative + "/" + name : name;
      const file = path.join(projectDir, rel),
        stat = fs.lstatSync(file);
      if (stat.isSymbolicLink())
        throw Error("Project inputs cannot contain symlinks: " + rel);
      if (stat.isDirectory()) visit(rel, depth + 1);
      else {
        projectFile(projectDir, rel);
        bytes += stat.size;
        if (bytes > 128 * 1024 * 1024)
          throw Error("Project exceeds 128 MiB input bound.");
        result[rel] = hash(readBytes(file, 16 * 1024 * 1024));
      }
    }
  }
  visit("", 0);
  return result;
}
function snapshot(projectDir, action) {
  const hashes = sources(projectDir),
    directory = runDirectory(projectDir, action.id);
  const input = path.join(directory, "input"),
    work = path.join(directory, "work");
  fs.mkdirSync(input);
  fs.mkdirSync(work);
  for (const [rel, digest] of Object.entries(hashes)) {
    const bytes = readBytes(projectFile(projectDir, rel), 16 * 1024 * 1024);
    if (hash(bytes) !== digest)
      throw Error("Input changed while preparing snapshot.");
    const target = path.join(input, rel);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, bytes, { flag: "wx", mode: 0o400 });
  }
  fs.writeFileSync(
    path.join(directory, "inputs.json"),
    JSON.stringify(hashes, null, 2),
  );
  return { directory, input, work, hashes };
}
function unchanged(projectDir, hashes) {
  try {
    return JSON.stringify(sources(projectDir)) === JSON.stringify(hashes);
  } catch {
    return false;
  }
}
function binary(environment, name, fallback) {
  const selected = environment[name] || fallback;
  const file = selected.includes("/")
    ? selected
    : (environment.PATH || "")
        .split(path.delimiter)
        .map((p) => path.join(p, selected))
        .find((p) => fs.existsSync(p));
  if (!file || !fs.existsSync(file))
    throw Error(
      "Install the declared native dependency or configure " + name + ".",
    );
  fs.accessSync(file, fs.constants.X_OK);
  return fs.realpathSync(file);
}
function appRoot(file) {
  const match = file.match(/^(.+?\.app)\//);
  return match ? match[1] : path.dirname(path.dirname(file));
}
async function phase(
  ctx,
  name,
  command,
  environment,
  signal,
  timeoutMs,
  readOnlyDirs,
) {
  const project = path.resolve(ctx.directory, "../..");
  for (const dependency of readOnlyDirs) {
    const root = fs.realpathSync(dependency);
    if (
      root === project ||
      root.startsWith(project + path.sep) ||
      project.startsWith(root + path.sep)
    )
      throw Error(
        "A native dependency cannot expose the project or an enclosing directory.",
      );
  }
  const result = await executeTask(
    { command, timeoutMs, runtime: { kind: "local", readOnlyDirs } },
    {
      ...ctx,
      actionId: name,
      environment,
      signal,
    },
  );
  fs.writeFileSync(
    path.join(ctx.directory, name + ".json"),
    JSON.stringify(result, null, 2),
  );
  fs.writeFileSync(path.join(ctx.directory, name + ".log"), result.log);
  return result;
}
function outputs(ctx, kinds) {
  const result = [];
  for (const [name, kind] of Object.entries(kinds)) {
    const file = path.join(ctx.directory, name),
      stat = fs.lstatSync(file, { throwIfNoEntry: false });
    if (!stat) continue;
    if (
      !stat.isFile() ||
      stat.isSymbolicLink() ||
      stat.nlink !== 1 ||
      stat.size > 16 * 1024 * 1024
    )
      throw Error("Unsafe native output.");
    result.push({ file, kind, sha256: hash(fs.readFileSync(file)) });
  }
  return result;
}
function timeout(inputs) {
  const value = inputs.timeoutMs ?? 60000;
  if (!Number.isInteger(value) || value < 1 || value > 120000)
    throw Error("timeoutMs must be 1..120000.");
  return value;
}
function exact(object, keys) {
  if (
    !object ||
    typeof object !== "object" ||
    Array.isArray(object) ||
    Object.keys(object).some((k) => !keys.includes(k))
  )
    throw Error("Unexpected or invalid input fields.");
}
function relative(value, extension) {
  if (typeof value !== "string" || !value.endsWith(extension))
    throw Error("Use a project-relative " + extension + " file.");
  return value;
}
function number(value) {
  if (!Number.isFinite(value) || Math.abs(value) > 100000)
    throw Error("Expected a bounded finite number.");
  return value;
}
function jsonArtifact(artifacts, readArtifact, kind) {
  const item = artifacts.find((a) => a.kind === kind);
  if (!item) throw Error("Missing independent evidence: " + kind);
  return JSON.parse(readArtifact(item));
}
module.exports = {
  configure,
  fs,
  path,
  crypto,
  projectFile,
  hash,
  readBytes,
  runDirectory,
  applyFiles,
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
  jsonArtifact,
};
