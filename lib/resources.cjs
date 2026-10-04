const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function safeResourcePath(value) {
  if (
    typeof value !== "string" ||
    !/^[a-zA-Z0-9._-]+(?:\/[a-zA-Z0-9._-]+)*$/.test(value) ||
    value.split("/").some((part) => part === "." || part === "..")
  )
    throw Error("Unsafe Domain Pack resource path.");
  return value;
}

function resourceFiles(directory, roots) {
  const files = [];
  const visit = (relative) => {
    const file = path.join(directory, relative);
    const info = fs.lstatSync(file);
    if (info.isSymbolicLink())
      throw Error("Domain Pack resources cannot contain symlinks.");
    if (info.isDirectory()) {
      for (const name of fs.readdirSync(file).sort()) {
        if (
          name !== "__pycache__" &&
          name !== ".DS_Store" &&
          !name.endsWith(".pyc")
        )
          visit(path.join(relative, name));
      }
    } else if (info.isFile()) {
      if (files.length >= 20000)
        throw Error("Domain Pack resource file limit exceeded.");
      files.push(relative.split(path.sep).join("/"));
    } else throw Error("Domain Pack resources must be ordinary files.");
  };
  for (const root of roots) {
    safeResourcePath(root);
    let prefix = directory;
    for (const part of root.split("/")) {
      prefix = path.join(prefix, part);
      if (fs.lstatSync(prefix).isSymbolicLink())
        throw Error("Domain Pack resources cannot contain symlinks.");
    }
    visit(root);
  }
  return files.sort();
}

function validateResources(directory, provider) {
  if (
    !provider.sourceFiles ||
    Array.isArray(provider.sourceFiles) ||
    !Array.isArray(provider.resourceRoots) ||
    !provider.resourceRoots.length ||
    !/^[a-f0-9]{64}$/.test(provider.sourceSha256 || "")
  )
    throw Error("Invalid Domain Pack resource inventory.");
  const expected = Object.keys(provider.sourceFiles).sort();
  for (const file of expected) {
    safeResourcePath(file);
    if (
      !provider.resourceRoots.some((root) =>
        file.startsWith(safeResourcePath(root) + "/"),
      ) ||
      !/^[a-f0-9]{64}$/.test(provider.sourceFiles[file])
    )
      throw Error("Invalid Domain Pack resource inventory.");
  }
  const actual = resourceFiles(directory, provider.resourceRoots);
  if (JSON.stringify(actual) !== JSON.stringify(expected))
    throw Error(
      `${provider.title} resource inventory differs from the registered snapshot.`,
    );
  const hashes = {};
  let bytes = 0;
  for (const relative of expected) {
    const file = path.join(directory, relative);
    const size = fs.statSync(file).size;
    bytes += size;
    if (size > 8 * 1024 * 1024 || bytes > 32 * 1024 * 1024)
      throw Error("Domain Pack resource size limit exceeded.");
    hashes[relative] = crypto
      .createHash("sha256")
      .update(fs.readFileSync(file))
      .digest("hex");
    if (hashes[relative] !== provider.sourceFiles[relative])
      throw Error(
        `${provider.title} resource ${relative} differs from the registered snapshot.`,
      );
  }
  const digest = crypto
    .createHash("sha256")
    .update(JSON.stringify(hashes))
    .digest("hex");
  if (digest !== provider.sourceSha256)
    throw Error(
      "Domain Pack resource manifest does not match its source digest.",
    );
  return directory;
}

function resourceDirectory(provider, environment = process.env) {
  const configured = environment[provider.directoryEnv];
  if (!configured || !path.isAbsolute(configured))
    throw Error(
      `${provider.title} needs its fixed resource checkout. Set ${provider.directoryEnv} to an absolute directory (commit ${provider.sourceCommit}).`,
    );
  const directory = fs.realpathSync(configured);
  if (!fs.statSync(directory).isDirectory())
    throw Error("Domain Pack source must be a directory.");
  return validateResources(directory, provider);
}

module.exports = { resourceDirectory, validateResources };
