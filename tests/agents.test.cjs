const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const packs = require("../lib/index.cjs");
const lock = require("../content-lock.json");
const packageFiles = require("../package.json").files;

function fixture(t, change) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "pack-agents-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const files = [
    "lib/consumer.cjs",
    "lib/consumer.json",
    "content-lock.json",
    ...packs.catalog.map((pack) => `packs/${path.basename(packs.sourceDirectory(pack.id))}/pack.json`),
    ...packs.consumerMetadata().agents.map((agent) =>
      path.relative(packs.root, packs.agentResource(agent.id).file),
    ),
  ];
  for (const file of files) {
    const target = path.join(directory, file);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(path.join(packs.root, file), target);
  }
  const metadataFile = path.join(directory, "lib/consumer.json");
  const metadata = JSON.parse(fs.readFileSync(metadataFile, "utf8"));
  change?.(directory, metadata);
  fs.writeFileSync(metadataFile, JSON.stringify(metadata));
  return { directory, consumer: require(path.join(directory, "lib/consumer.cjs")) };
}

test("Pack Agents have unique identities and resolve packaged, locked domain instructions", () => {
  const { agents, skills } = packs.consumerMetadata();
  assert.equal(agents.length, packs.catalog.length);
  assert.equal(new Set(agents.map((agent) => agent.id)).size, agents.length);
  for (const agent of agents) {
    assert.match(agent.id, /^[a-z][a-z0-9.-]*$/);
    assert.ok(agent.title.trim());
    assert.ok(agent.description.trim());
    const pack = packs.getPack(agent.packId);
    assert.equal(agent.domain, pack.domain);
    assert.deepEqual(pack.agents, [agent.resourcePath]);
    for (const skillId of agent.skills) {
      const skill = skills.find((item) => item.id === skillId);
      assert.ok(skill, `${agent.id} references unknown Skill ${skillId}`);
      assert.equal(skill.domain, agent.domain);
    }
    const resource = packs.agentResource(agent.id);
    assert.equal(resource.instructions, fs.readFileSync(resource.file, "utf8"));
    assert.equal(path.dirname(resource.file), path.join(packs.sourceDirectory(agent.packId), "agents"));
    assert.match(resource.instructions, /^# /);
    const relative = path.relative(packs.root, resource.file).split(path.sep).join("/");
    assert.ok(lock.files[relative]);
    assert.ok(packageFiles.includes(relative));
  }
  for (const host of packs.hostPacks())
    assert.deepEqual(host.agents, agents.filter((agent) => agent.packId === host.id));
  assert.ok(agents.some((agent) => agent.packId === "cad-pack"));
});

test("Agent declarations and resource metadata are isolated from caller mutation", () => {
  const initial = packs.consumerMetadata().agents[0];
  const resource = packs.agentResource(initial.id);
  resource.skills.length = 0;
  resource.title = "changed";
  const metadata = packs.consumerMetadata();
  metadata.agents[0].skills.length = 0;
  metadata.agents.length = 0;
  const host = packs.hostPacks().find((pack) => pack.id === initial.packId);
  host.agents[0].title = "changed";
  host.agents[0].skills.length = 0;
  assert.deepEqual(packs.consumerMetadata().agents[0], initial);
  assert.equal(packs.agentResource(initial.id).title, initial.title);
  assert.deepEqual(packs.agentResource(initial.id).skills, initial.skills);
  assert.throws(() => packs.agentResource("unknown-agent"), /Unknown Domain Pack Agent/);
});

test("Agent resources reject traversal, absolute paths and non-Markdown targets", async (t) => {
  for (const resourcePath of ["../outside.md", "/tmp/agent.md", "agents/../outside.md", "agents//role.md", "agents/role.json"]) {
    await t.test(resourcePath, (t) => {
      const { consumer } = fixture(t, (_, metadata) => {
        metadata.agents[0].resourcePath = resourcePath;
      });
      assert.throws(() => consumer.agentResource("chip.engineer"), /Unsafe Domain Pack Agent/);
    });
  }
});

test("Agent resources reject symlink files and directories", async (t) => {
  for (const linkDirectory of [false, true]) {
    await t.test(linkDirectory ? "directory" : "file", (t) => {
      const { consumer } = fixture(t, (directory) => {
        const target = path.join(directory, "packs/chip/agents", linkDirectory ? "" : "chip.engineer.md");
        const original = target + ".original";
        fs.renameSync(target, original);
        fs.symlinkSync(original, target);
      });
      assert.throws(() => consumer.agentResource("chip.engineer"), /cannot contain symlinks/);
    });
  }
});

test("Agent resources reject oversized, empty, invalid UTF-8 and modified instruction bytes", async (t) => {
  for (const [name, bytes, pattern] of [
    ["oversized", Buffer.alloc(128 * 1024 + 1, "x"), /at most 128 KiB/],
    ["empty", " \n", /must not be empty/],
    ["invalid UTF-8", Buffer.from([0xff]), /encoded data was not valid/],
    ["changed", "# Different instructions\n", /differ from their pinned release/],
  ]) {
    await t.test(name, (t) => {
      const { consumer } = fixture(t, (directory) => {
        fs.writeFileSync(path.join(directory, "packs/chip/agents/chip.engineer.md"), bytes);
      });
      assert.throws(() => consumer.agentResource("chip.engineer"), pattern);
    });
  }
});
