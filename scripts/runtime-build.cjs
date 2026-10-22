// Publisher-only helpers. Build from prepareContext's output, not the checkout:
// that binds the bytes fingerprinted here to the bytes Docker can actually copy.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const hash = (bytes) =>
  "sha256:" + crypto.createHash("sha256").update(bytes).digest("hex");
const immutable = (value) =>
  /^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$/.test(value);
function safe(name) {
  if (
    typeof name !== "string" ||
    !/^[a-zA-Z0-9_./-]+$/.test(name) ||
    name.startsWith("/") ||
    name.split("/").some((p) => !p || p === "." || p === "..")
  )
    throw Error("Unsafe build source path");
  return name;
}
function file(root, name) {
  let target = path.resolve(root);
  for (const part of safe(name).split("/")) {
    target = path.join(target, part);
    if (fs.lstatSync(target).isSymbolicLink())
      throw Error("Build source contains symlink");
  }
  const stat = fs.statSync(target);
  if (!stat.isFile())
    throw Error("Unsupported broad COPY: enumerate runtime files explicitly");
  return { bytes: fs.readFileSync(target), mode: stat.mode & 0o777 };
}
function collect({ root, recipe, platform, buildArgs = {} }) {
  if (!/^linux\/(amd64|arm64)(\/v[0-9]+)?$/.test(platform))
    throw Error("Unsupported build platform");
  if (!buildArgs || typeof buildArgs !== "object" || Array.isArray(buildArgs))
    throw Error("Invalid build args");
  const recipeFile = file(root, recipe);
  const text = recipeFile.bytes.toString("utf8");
  const files = new Map([[recipe, recipeFile]]),
    args = {},
    stages = new Set(),
    images = new Set();
  function image(value) {
    if (!immutable(value)) throw Error("Immutable image digest required");
    images.add(value);
  }
  const syntax = text.match(/^#\s*syntax=(\S+)\s*$/m);
  if (syntax) image(syntax[1]);
  for (const line of text.replace(/\\\r?\n/g, " ").split(/\r?\n/)) {
    if (!line.trim() || line.trim().startsWith("#")) continue;
    const match = line.trim().match(/^(\w+)\s+(.+)$/);
    if (!match) throw Error("Unsupported Dockerfile instruction");
    const instruction = match[1].toUpperCase(),
      rest = match[2].trim();
    if (instruction === "RUN") {
      for (const mount of rest.matchAll(/--mount=(\S+)/g)) {
        const parts = mount[1].split(",");
        if (
          !parts.includes("type=cache") ||
          parts.some(
            (part) =>
              !/^(type=cache|target=\/[^,]+|sharing=(shared|private|locked))$/.test(
                part,
              ),
          )
        )
          throw Error("Unsupported mounted build input");
      }
    }
    if (instruction === "FROM") {
      const from = rest.match(/^(\S+)(?:\s+AS\s+([a-zA-Z0-9_-]+))?$/i);
      if (!from) throw Error("Unsupported FROM");
      if (!stages.has(from[1])) image(from[1]);
      if (from[2]) stages.add(from[2]);
    } else if (instruction === "ARG") {
      const arg = rest.match(/^([A-Z_][A-Z0-9_]*)=([^\s$'"\\]*)$/);
      if (!arg || /^(BUILD|TARGET)(PLATFORM|OS|ARCH|VARIANT)$/.test(arg[1]) || Object.hasOwn(args, arg[1]))
        throw Error("Unsupported automatic, expanded or scoped ARG");
      args[arg[1]] = arg[2];
    } else if (instruction === "COPY") {
      const tokens = rest.split(/\s+/);
      let from;
      while (tokens[0]?.startsWith("--")) {
        const flag = tokens.shift();
        if (flag.startsWith("--from=")) from = flag.slice(7);
        else if (!/^--chmod=[0-7]{3,4}$/.test(flag))
          throw Error("Unsupported COPY flag");
      }
      if (tokens.length < 2) throw Error("Unsupported COPY");
      if (from) {
        if (!stages.has(from)) image(from);
        continue;
      }
      for (const name of tokens.slice(0, -1)) {
        if (name === "content-lock.json" || name.includes("/releases/"))
          throw Error("Publication metadata cannot be a runtime build input");
        if (name === "." || ["packs", "lib"].includes(name))
          throw Error("Unsupported broad COPY");
        if (path.basename(name).endsWith(".dockerignore"))
          throw Error("Unsupported Docker ignore file in prepared context");
        safe(name);
        files.set(name, file(root, name));
      }
    } else if (
      instruction === "ADD" ||
      (instruction === "RUN" && /--mount=(?!type=cache(?:,|\s))/.test(rest))
    ) {
      throw Error("Unsupported external or mounted build input");
    } else if (
      ![
        "RUN",
        "ENV",
        "WORKDIR",
        "USER",
        "ENTRYPOINT",
        "CMD",
        "EXPOSE",
        "LABEL",
        "SHELL",
        "STOPSIGNAL",
        "HEALTHCHECK",
      ].includes(instruction)
    ) {
      throw Error("Unsupported Dockerfile instruction: " + instruction);
    }
  }
  for (const [key, value] of Object.entries(buildArgs)) {
    if (
      !Object.hasOwn(args, key) ||
      typeof value !== "string" ||
      /[\x00\r\n]/.test(value)
    )
      throw Error("Unknown or invalid build argument");
    args[key] = value;
  }
  const sorted = (values) =>
    Object.fromEntries(
      Object.entries(values).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
    );
  const descriptor = {
    recipe,
    platform,
    buildArgs: sorted(args),
    images: [...images].sort(),
    files: sorted(
      Object.fromEntries(
        [...files].map(([name, value]) => [
          name,
          { sha256: hash(value.bytes), mode: value.mode },
        ]),
      ),
    ),
  };
  return {
    result: {
      ...descriptor,
      buildInputDigest: hash(JSON.stringify(descriptor)),
    },
    files,
  };
}
function inputs(options) {
  return collect(options).result;
}
function prepareContext(options, destination) {
  const { result, files } = collect(options);
  // A fresh directory avoids stale or ignored files changing COPY semantics.
  fs.mkdirSync(destination);
  for (const [name, { bytes, mode }] of files) {
    const target = path.join(destination, name);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, bytes, { flag: "wx", mode });
    fs.chmodSync(target, mode);
  }
  return result;
}
module.exports = { inputs, prepareContext };
if (require.main === module) {
  try {
    const [recipe, platform, destination, args = "{}"] = process.argv.slice(2);
    if (!destination)
      throw Error(
        "Usage: node scripts/runtime-build.cjs RECIPE PLATFORM NEW_CONTEXT_DIRECTORY [BUILD_ARGS_JSON]",
      );
    console.log(
      JSON.stringify(
        prepareContext(
          {
            root: process.cwd(),
            recipe,
            platform,
            buildArgs: JSON.parse(args),
          },
          destination,
        ),
        null,
        2,
      ),
    );
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
