const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function sourceHash(directory) {
  const hash = crypto.createHash("sha256");
  const visit = (relative) => {
    for (const entry of fs
      .readdirSync(path.join(directory, relative), { withFileTypes: true })
      .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))) {
      const file = path.join(relative, entry.name);
      if (entry.isDirectory() && entry.name !== "__pycache__") visit(file);
      else if (entry.isFile() && file.endsWith(".py"))
        hash
          .update(file.split(path.sep).join("/") + "\n")
          .update(fs.readFileSync(path.join(directory, file)));
    }
  };
  visit("src");
  for (const file of ["pyproject.toml", "uv.lock"])
    hash
      .update(file + "\n")
      .update(fs.readFileSync(path.join(directory, file)));
  return hash.digest("hex");
}

function providerRuntime(provider, environment = process.env) {
  let packDir = environment[provider.directoryEnv];
  if (packDir && !path.isAbsolute(packDir))
    throw Error(`${provider.directoryEnv} must be an absolute directory.`);
  if (!packDir && provider.installedDirectory)
    packDir = provider.installedDirectory;
  if (!packDir) packDir = path.resolve(__dirname, "..");
  if (!packDir)
    throw Error(
      `${provider.title} is not installed. Set ${provider.directoryEnv} to an installed Chip Pack.`,
    );
  const sourceDir = fs.realpathSync(path.join(packDir, "eda-harness"));
  const python =
    environment[provider.pythonEnv] ||
    path.join(
      sourceDir,
      ".venv",
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
    );
  if (
    !path.isAbsolute(python) ||
    !fs.statSync(python, { throwIfNoEntry: false })?.isFile()
  )
    throw Error(
      `${provider.title} needs its pinned Python environment. Run uv sync --frozen --no-dev in ${sourceDir}, or set ${provider.pythonEnv} to its absolute Python path.`,
    );
  if (sourceHash(sourceDir) !== provider.sourceSha256)
    throw Error(
      `${provider.title} source does not match the registered snapshot.`,
    );
  return { python: path.normalize(python), sourceDir };
}

function gatewayConfig(
  directory,
  provider,
  projectDir,
  environment,
  options = {},
) {
  if (!projectDir || !path.isAbsolute(projectDir))
    throw Error("Domain MCP requires an explicit absolute project directory.");
  const project = fs.realpathSync(projectDir);
  if (!fs.statSync(project).isDirectory())
    throw Error("Domain MCP project must be a directory.");
  const runtime = providerRuntime(provider, environment);
  const policyFile = path.join(directory, `mcp-${provider.id}.policy.json`);
  const policy = {
    schemaVersion: 1,
    providerId: provider.id,
    domain: provider.domain,
    version: provider.version,
    package: provider.package,
    module: provider.module,
    mcpVersion: provider.mcpVersion,
    tools: provider.tools,
    allowedToolIds: provider.allowedToolIds,
    projectDir: project,
    ...runtime,
    cacheDir: path.join(directory, `mcp-${provider.id}-results`),
  };
  fs.writeFileSync(policyFile, JSON.stringify(policy, null, 2), {
    mode: 0o600,
  });
  fs.chmodSync(policyFile, 0o600);
  return {
    command: runtime.python,
    args: [options.gatewayScript, policyFile],
    env: { PYTHONNOUSERSITE: "1", PYTHONUNBUFFERED: "1" },
  };
}
module.exports = { providerRuntime, sourceHash, gatewayConfig };
