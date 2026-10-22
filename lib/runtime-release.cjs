const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const identifier = /^[a-z0-9][a-z0-9._-]*$/;
const sha256 = /^sha256:[a-f0-9]{64}$/;
const hex256 = /^[a-f0-9]{64}$/;
const imageReference =
  /^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?\/[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[a-f0-9]{64}$/;
const selectionKeys = [
  "packId",
  "domain",
  "runtimeProfile",
  "environment",
  "region",
  "buildInputDigest",
  "runtimeDigest",
  "workerProtocolVersion",
];

function invalid(detail) {
  // Never echo rejected values: callers may accidentally pass credentials.
  throw new Error("Invalid runtime release: " + detail);
}
function object(value, keys, label) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== keys.length ||
    keys.some((key) => !Object.hasOwn(value, key))
  )
    invalid(label);
}
function matches(value, pattern, label) {
  if (typeof value !== "string" || value.length > 1024 || !pattern.test(value))
    invalid(label);
}
function list(value, label) {
  if (
    !Array.isArray(value) ||
    !value.length ||
    value.length > 100 ||
    value.some(
      (item) =>
        typeof item !== "string" ||
        !item.length ||
        item.length > 1024 ||
        /[\x00-\x20\x7f]/.test(item),
    ) ||
    new Set(value).size !== value.length
  )
    invalid(label);
}
function freeze(value) {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

// This validates published metadata, not cloud existence or engineering qualification.
// expected is the trusted consumer selection, including independently computed inputs.
function validateRuntimeRelease(record, expected) {
  object(
    record,
    [
      "schemaVersion",
      "releaseId",
      ...selectionKeys,
      "buildSourceCommit",
      "buildSourceContentSha256",
      "image",
      "provider",
      "providerToolId",
      "toolConfigurationDigest",
      "qualification",
    ],
    "record fields",
  );
  if (record.schemaVersion !== 1) invalid("schema version");
  for (const key of [
    "releaseId",
    "packId",
    "domain",
    "runtimeProfile",
    "region",
  ])
    matches(record[key], identifier, key);
  if (!["test", "prod"].includes(record.environment)) invalid("environment");
  matches(record.buildSourceCommit, /^[a-f0-9]{40}$/, "build source commit");
  matches(
    record.buildSourceContentSha256,
    hex256,
    "build source content identity",
  );
  matches(record.buildInputDigest, sha256, "build input identity");
  // Runtime identity is an existing opaque contract string, not a new digest algorithm.
  matches(record.runtimeDigest, /^[^\s\x00-\x1f\x7f]+$/, "runtime identity");
  if (
    !Number.isSafeInteger(record.workerProtocolVersion) ||
    record.workerProtocolVersion < 1
  )
    invalid("worker protocol");
  if (record.provider !== "tencent-ags") invalid("provider");
  matches(record.providerToolId, /^sdt-[a-z0-9]+$/, "provider Tool identity");
  matches(
    record.toolConfigurationDigest,
    sha256,
    "Tool configuration identity",
  );
  object(record.image, ["reference", "platform"], "image fields");
  matches(record.image.reference, imageReference, "immutable image reference");
  matches(
    record.image.platform,
    /^linux\/(?:amd64|arm64)(?:\/v[0-9]+)?$/,
    "image platform",
  );
  object(
    record.qualification,
    ["status", "gates", "evidence"],
    "qualification fields",
  );
  if (record.qualification.status !== "passed") invalid("qualification status");
  list(record.qualification.gates, "qualification gates");
  list(record.qualification.evidence, "qualification evidence");
  object(expected, selectionKeys, "trusted selection");
  for (const key of selectionKeys)
    if (record[key] !== expected[key]) invalid("selection mismatch: " + key);
  return freeze(structuredClone(record));
}

// root/contentLock are trusted package inputs, never client-supplied host paths.
// The package facade verifies the full content lock before calling this reader.
function readRuntimeRelease({ root, relativePath, contentLock, expected }) {
  const parts =
    typeof relativePath === "string" &&
    /^packs\/[a-z0-9][a-z0-9_-]*\/releases\/[a-z0-9][a-z0-9._-]*\/(test|prod)\.lock\.json$/.test(
      relativePath,
    )
      ? relativePath.split("/")
      : null;
  if (
    !parts ||
    parts.some((part) => part === "." || part === "..") ||
    parts[3] !== expected?.runtimeProfile ||
    parts[4] !== expected?.environment + ".lock.json"
  )
    invalid("release path");
  const hash = contentLock?.files?.[relativePath];
  if (typeof hash !== "string" || !hex256.test(hash))
    invalid("release missing from content lock");
  let filename = root;
  for (const part of parts) {
    filename = path.join(filename, part);
    if (fs.lstatSync(filename).isSymbolicLink())
      invalid("release path contains symlink");
  }
  const stat = fs.statSync(filename);
  if (!stat.isFile() || stat.size > 65536) invalid("release file");
  const bytes = fs.readFileSync(filename);
  if (crypto.createHash("sha256").update(bytes).digest("hex") !== hash)
    invalid("release differs from content lock");
  let record;
  try {
    record = JSON.parse(bytes.toString("utf8"));
  } catch {
    invalid("release JSON");
  }
  return validateRuntimeRelease(record, expected);
}

module.exports = { validateRuntimeRelease, readRuntimeRelease };
