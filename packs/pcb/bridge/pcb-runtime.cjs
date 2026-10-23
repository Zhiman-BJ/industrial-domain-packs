const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { resourceDirectory } = require("../../../lib/resources.cjs");

// The container root remains read-only. This receipt is a pinned expectation,
// not an observation: the controller checks CLI, pcbnew and all native packages
// before the unchanged upstream resolver is allowed to consume it.
function writeVersionReceipt(directory, provider) {
  if (!/^\d+\.\d+\.\d+$/.test(provider.kicadVersion) ||
      typeof provider.kicadPackageVersion !== "string" || !provider.kicadPackageVersion)
    throw Error("PCB native version metadata is missing.");
  const bytes = JSON.stringify({schema_version:1, kicad_version:provider.kicadVersion,
    kicad_package_version:provider.kicadPackageVersion, source:"pinned-runtime-policy"}) + "\n";
  const identity = crypto.createHash("sha256").update(bytes).digest("hex").slice(0,24);
  const file = path.join(directory, `pcb-native-version-${identity}.json`);
  try { fs.writeFileSync(file, bytes, {flag:"wx",mode:0o444}); }
  catch (error) { if (error.code !== "EEXIST") throw error; }
  if (fs.lstatSync(file).isSymbolicLink() || !fs.statSync(file).isFile() ||
      fs.readFileSync(file,"utf8") !== bytes)
    throw Error("PCB native version receipt differs from its pinned metadata.");
  return file;
}

function pcbRuntime(provider, environment = process.env) {
  const sourceDir = resourceDirectory(provider, environment);
  const packDir = provider.installedDirectory || path.resolve(__dirname, "..");
  const python =
    environment[provider.pythonEnv] ||
    (packDir &&
      path.join(
        packDir,
        ".venv",
        process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
      ));
  if (
    !python ||
    !path.isAbsolute(python) ||
    !fs.statSync(python, { throwIfNoEntry: false })?.isFile()
  )
    throw Error(
      `${provider.title} needs its gateway Python environment. Run uv sync --frozen --no-dev in domain-packs/pcb or set ${provider.pythonEnv} to its absolute Python path.`,
    );
  const docker = environment.INDUSTRIAL_HARNESS_PCB_DOCKER || "docker";
  if (
    docker !== "docker" &&
    (!path.isAbsolute(docker) ||
      !fs.statSync(docker, { throwIfNoEntry: false })?.isFile())
  )
    throw Error(
      "INDUSTRIAL_HARNESS_PCB_DOCKER must be an absolute Docker executable.",
    );
  const requirements = environment.INDUSTRIAL_HARNESS_PCB_REQUIREMENTS;
  if (
    requirements &&
    (!path.isAbsolute(requirements) ||
      !fs.statSync(requirements, { throwIfNoEntry: false })?.isFile())
  )
    throw Error(
      "INDUSTRIAL_HARNESS_PCB_REQUIREMENTS must name an absolute public requirements file.",
    );
  const requirementsPath = requirements ? fs.realpathSync(requirements) : null;
  const requirementsSha256 = requirementsPath
    ? crypto
        .createHash("sha256")
        .update(fs.readFileSync(requirementsPath))
        .digest("hex")
    : null;
  const dockerEnvironment = Object.fromEntries(
    ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"]
      .filter((key) => environment[key])
      .map((key) => [key, environment[key]]),
  );
  const developmentImageId = environment.INDUSTRIAL_HARNESS_PCB_DEV_IMAGE_ID;
  if (developmentImageId && !/^sha256:[0-9a-f]{64}$/.test(developmentImageId))
    throw Error(
      "INDUSTRIAL_HARNESS_PCB_DEV_IMAGE_ID must be an immutable Docker image ID.",
    );
  return {
    python: path.normalize(python),
    sourceDir,
    docker,
    dockerEnvironment,
    imageId: developmentImageId || provider.imageId,
    requirementsPath,
    requirementsSha256,
  };
}

function pcbGatewayConfig(
  directory,
  provider,
  project,
  environment,
  options = {},
) {
  const runtime = pcbRuntime(provider, environment);
  if (
    runtime.requirementsPath &&
    (runtime.requirementsPath === project ||
      runtime.requirementsPath.startsWith(project + path.sep))
  )
    throw Error(
      "Public PCB requirements must be outside the writable candidate project.",
    );
  for (const value of [
    project,
    runtime.sourceDir,
    directory,
    runtime.requirementsPath,
  ].filter(Boolean))
    if (value.includes(","))
      throw Error("PCB Docker bind paths cannot contain commas.");
  const policyFile = path.join(directory, `mcp-${provider.id}.policy.json`);
  const policy = {
    schemaVersion: 1,
    ...provider,
    ...runtime,
    projectDir: project,
    imageInput: Boolean(options.imageInput),
    cacheDir: path.join(directory, `mcp-${provider.id}-results`),
    controller: path.join(__dirname, "pcb-controller.py"),
    versionReceipt: writeVersionReceipt(directory, provider),
  };
  fs.writeFileSync(policyFile, JSON.stringify(policy, null, 2), {
    mode: 0o600,
  });
  fs.chmodSync(policyFile, 0o600);
  return {
    command: runtime.python,
    args: [path.join(__dirname, "pcb-gateway.py"), policyFile],
    env: {
      PYTHONNOUSERSITE: "1",
      PYTHONUNBUFFERED: "1",
      ...runtime.dockerEnvironment,
    },
  };
}

module.exports = { pcbRuntime, pcbGatewayConfig, writeVersionReceipt };
