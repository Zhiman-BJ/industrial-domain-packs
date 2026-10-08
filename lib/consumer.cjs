const fs = require("node:fs");
const path = require("node:path");
const metadata = require("./consumer.json");
const names = ["chip", "godot", "pcb", "freecad", "cad"];
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
module.exports = {
  sourceDirectory,
  hostPacks,
  consumerMetadata,
  skillResource,
};
