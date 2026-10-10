const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const metadata = require("./consumer.json");
const names = ["chip", "godot", "pcb", "freecad", "cad", "cuda"];
const manifests = names.map((name) => ({
  name,
  catalog: require(`../packs/${name}/pack.json`),
}));
const copy = (value) => JSON.parse(JSON.stringify(value));
function sourceDirectory(packId) {
  const entry = manifests.find((item) => item.catalog.id === packId);
  if (!entry) throw Error("Unknown Domain Pack: " + packId);
  return path.resolve(__dirname, "../packs", entry.name);
}
function hostPacks() {
  return manifests.flatMap(({ name }) => {
    const manifest = require(`../packs/${name}/harness-pack.json`);
    return manifest.provider
      ? [
          {
            ...copy(manifest),
            agents: copy(
              (metadata.agents || []).filter(
                (agent) => agent.packId === manifest.id,
              ),
            ),
          },
        ]
      : [];
  });
}
function consumerMetadata() {
  return copy(metadata);
}
function skillResource(id) {
  const skill = metadata.skills.find((item) => item.id === id);
  if (!skill) throw Error("Unknown Domain Pack Skill: " + id);
  const directory = path.join(
    sourceDirectory(skill.packId),
    skill.resourcePath,
  );
  if (!fs.statSync(path.join(directory, "SKILL.md")).isFile())
    throw Error("Missing Domain Pack Skill: " + id);
  return { ...copy(skill), directory };
}
function agentResource(id) {
  const agent = (metadata.agents || []).find((item) => item.id === id);
  if (!agent) throw Error("Unknown Domain Pack Agent: " + id);
  if (
    typeof agent.resourcePath !== "string" ||
    !/^agents\/[a-zA-Z0-9._/-]+\.md$/.test(agent.resourcePath) ||
    agent.resourcePath.split("/").some((part) => !part || part === "." || part === "..")
  )
    throw Error("Unsafe Domain Pack Agent resource path.");
  let file = sourceDirectory(agent.packId);
  if (fs.lstatSync(file).isSymbolicLink())
    throw Error("Domain Pack Agent resources cannot contain symlinks.");
  for (const part of agent.resourcePath.split("/")) {
    file = path.join(file, part);
    if (fs.lstatSync(file).isSymbolicLink())
      throw Error("Domain Pack Agent resources cannot contain symlinks.");
  }
  const info = fs.statSync(file);
  if (!info.isFile() || info.size > 128 * 1024)
    throw Error("Domain Pack Agent instructions must be an ordinary file of at most 128 KiB.");
  const bytes = fs.readFileSync(file);
  const instructions = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  if (!instructions.trim())
    throw Error("Domain Pack Agent instructions must not be empty.");
  const relative = path.relative(path.resolve(__dirname, ".."), file).split(path.sep).join("/");
  const expected = require("../content-lock.json").files[relative];
  if (!expected || crypto.createHash("sha256").update(bytes).digest("hex") !== expected)
    throw Error("Domain Pack Agent instructions differ from their pinned release.");
  return { ...copy(agent), file, instructions };
}
function gatewayAdapter(provider) {
  const entry = manifests.find(
    (item) =>
      item.catalog.id === provider.packId ||
      (!provider.packId && item.name === provider.packDirectory),
  );
  if (!entry) throw Error("Unknown gateway Pack identity.");
  const adapters = {
    chip: () => require("../packs/chip/bridge/runtime.cjs"),
    pcb: () => {
      const api = require("../packs/pcb/bridge/pcb-runtime.cjs");
      return {
        providerRuntime: api.pcbRuntime,
        gatewayConfig: api.pcbGatewayConfig,
      };
    },
    godot: () => {
      const api = require("../packs/godot/bridge/godot-runtime.cjs");
      return {
        providerRuntime: api.godotRuntime,
        gatewayConfig: api.godotGatewayConfig,
      };
    },
  };
  if (!adapters[entry.name]) throw Error("Pack has no gateway transport.");
  return adapters[entry.name]();
}
module.exports = {
  gatewayAdapter,
  sourceDirectory,
  hostPacks,
  consumerMetadata,
  skillResource,
  agentResource,
};
