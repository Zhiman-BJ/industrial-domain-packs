const test = require("node:test"),
  assert = require("node:assert/strict");
const {
  buildPublicationConfig,
} = require("../scripts/runtime-publication-config.cjs");
const read = () => structuredClone(require("../publication/test.cjs"));
const selection = { directory: "chip", profile: "rtl-cpu" };
test("environment CJS converts domain inputs without mutating or sharing cached configuration", () => {
  const raw = read();
  raw.domains.chip.resources.memory = "4Gi";
  const got = buildPublicationConfig(raw, selection);
  assert.equal(got.region, "ap-guangzhou");
  assert.equal(got.imagePrefix, "ccr.ccs.tencentyun.com/industrial-mcp-remote");
  assert.equal(
    got.toolConfiguration.CustomConfiguration.Resources.Memory,
    "4Gi",
  );
  assert.equal(got.toolConfiguration.DefaultTimeout, "1800s");
  assert.equal(got.toolConfiguration.Persistent, false);
  assert.deepEqual(
    got.toolConfiguration.CustomConfiguration.Command,
    raw.domains.chip.command,
  );
  assert.equal(
    got.toolConfiguration.CustomConfiguration.Probe.HttpGet.Port,
    raw.domains.chip.probe.httpGet.port,
  );
  assert.equal(
    got.toolConfiguration.CustomConfiguration.Resources.Storage,
    raw.domains.chip.resources.storage,
  );
  assert.equal(Object.hasOwn(got.toolConfiguration, "RoleArn"), false);
  got.toolConfiguration.CustomConfiguration.Command.push("mutation");
  assert.notDeepEqual(
    got.toolConfiguration.CustomConfiguration.Command,
    raw.domains.chip.command,
  );
  assert.deepEqual(
    buildPublicationConfig(raw, selection).toolConfiguration.CustomConfiguration
      .Command,
    raw.domains.chip.command,
  );
});
test("rejects misspelled fields, wrong types, missing domains and profiles", () => {
  for (const mutate of [
    (x) => (x.domains.chip.resources.memroy = "4Gi"),
    (x) => (x.domains.chip.resources.cpu = 2),
    (x) => delete x.domains.pcb,
    (x) => (x.domains.chip.profile = "wrong"),
    (x) => (x.domains.chip.defaultTimeout = "30s"),
    (x) => (x.imageNamespace = "other"),
    (x) => (x.domains.chip.ports[0].port = 70000),
    (x) => (x.domains.chip.probe.httpGet.scheme = "broken"),
    (x) => (x.domains.pcb.command = null),
  ]) {
    const raw = read();
    mutate(raw);
    assert.throws(
      () => buildPublicationConfig(raw, selection),
      /publication|configuration/i,
    );
  }
});
test("rejects non-data exports and unsupported domain selections", () => {
  for (const raw of [null, [], () => ({}), { ...read(), extra: () => {} }])
    assert.throws(
      () => buildPublicationConfig(raw, selection),
      /publication|configuration/i,
    );
  assert.throws(
    () =>
      buildPublicationConfig(read(), {
        directory: "unknown",
        profile: "rtl-cpu",
      }),
    /selection/,
  );
});
