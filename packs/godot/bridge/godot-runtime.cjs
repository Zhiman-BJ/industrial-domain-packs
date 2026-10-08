const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { validateResources } = require("../../../lib/resources.cjs");

function findPack(provider) {
  return provider.installedDirectory || path.resolve(__dirname, "..");
}

function findBinary(environment) {
  const configured = environment.INDUSTRIAL_HARNESS_GODOT_BIN;
  if (
    configured &&
    (!path.isAbsolute(configured) ||
      !fs.statSync(configured, { throwIfNoEntry: false })?.isFile())
  )
    throw Error(
      "INDUSTRIAL_HARNESS_GODOT_BIN must name an absolute Godot executable.",
    );
  if (configured) return fs.realpathSync(configured);
  for (const directory of (environment.PATH || "").split(path.delimiter)) {
    if (!directory) continue;
    const candidate = path.join(
      directory,
      process.platform === "win32" ? "godot.exe" : "godot",
    );
    if (fs.statSync(candidate, { throwIfNoEntry: false })?.isFile())
      return fs.realpathSync(candidate);
  }
  throw Error(
    "Godot 4 is required. Set INDUSTRIAL_HARNESS_GODOT_BIN to its absolute executable path.",
  );
}

function godotRuntime(provider, environment = process.env) {
  const packDir = validateResources(findPack(provider), provider);
  const binary = findBinary(environment);
  const versionResult = spawnSync(binary, ["--version"], {
    encoding: "utf8",
    timeout: 5000,
    maxBuffer: 4096,
  });
  const binaryVersion = (versionResult.stdout || "").trim();
  if (versionResult.status !== 0 || !/^4\./.test(binaryVersion))
    throw Error("Godot local tools require a working Godot 4 executable.");
  return {
    packDir,
    binary,
    binaryVersion,
    sourceSha256: provider.sourceSha256,
  };
}

function godotGatewayConfig(
  directory,
  provider,
  project,
  environment = process.env,
) {
  const runtime = godotRuntime(provider, environment);
  const policyFile = path.join(directory, `mcp-${provider.id}.policy.json`);
  const policy = {
    schemaVersion: 1,
    providerId: provider.id,
    domain: provider.domain,
    version: provider.version,
    projectDir: project,
    allowedToolIds: provider.allowedToolIds,
    tools: provider.tools,
    sourceFiles: provider.sourceFiles,
    resourceRoots: provider.resourceRoots,
    ...runtime,
    receiptDir: path.join(directory, `mcp-${provider.id}-receipts`),
    cacheDir: path.join(directory, `mcp-${provider.id}-results`),
  };
  fs.writeFileSync(policyFile, JSON.stringify(policy, null, 2), {
    mode: 0o600,
  });
  fs.chmodSync(policyFile, 0o600);
  return {
    command: process.execPath,
    args: [path.join(__dirname, "godot-gateway.cjs"), policyFile],
    env: {
      ELECTRON_RUN_AS_NODE: "1",
      ...(environment.INDUSTRIAL_HARNESS_ACTION_DIR
        ? {
            INDUSTRIAL_HARNESS_ACTION_DIR:
              environment.INDUSTRIAL_HARNESS_ACTION_DIR,
          }
        : {}),
    },
  };
}

module.exports = { godotRuntime, godotGatewayConfig };
