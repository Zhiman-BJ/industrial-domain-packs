const fs = require("node:fs");
const path = require("node:path");
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
    return manifest.provider ? [copy(manifest)] : [];
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
};
