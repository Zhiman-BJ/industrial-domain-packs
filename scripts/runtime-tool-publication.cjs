// Publisher-only AGS control plane. No workload lifecycle or deletion operations.
const { createHash } = require("node:crypto");
const canonical = (value) =>
  Array.isArray(value)
    ? value.map(canonical)
    : value && typeof value === "object"
      ? Object.fromEntries(
          Object.keys(value)
            .filter(
              (key) =>
                value[key] != null &&
                !(Array.isArray(value[key]) && value[key].length === 0),
            )
            .sort()
            .map((key) => [key, canonical(value[key])]),
        )
      : value;
const digest = (value) =>
  "sha256:" +
  createHash("sha256")
    .update(JSON.stringify(canonical(value)))
    .digest("hex");
const fields = [
  "ToolType",
  "NetworkConfiguration",
  "RoleArn",
  "Persistent",
  "CustomConfiguration",
  "StorageMounts",
  "LogConfiguration",
  "ComputerConfiguration",
];
function planTool({
  environment,
  region,
  packId,
  profile,
  image,
  configuration,
}) {
  if (
    !["test", "prod"].includes(environment) ||
    !/^[a-z]+-[a-z]+(?:-[a-z]+)?\d?$/.test(region) ||
    !/^[a-z0-9-]+$/.test(packId) ||
    !/^[a-z0-9-]+$/.test(profile)
  )
    throw Error("Invalid publication selection");
  if (!/^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$/.test(image))
    throw Error("Immutable image required");
  const config = structuredClone(configuration);
  if (
    !config ||
    Object.keys(config).some(
      (k) => ![...fields, "DefaultTimeout"].includes(k),
    ) ||
    config.ToolType !== "custom" ||
    !config.NetworkConfiguration ||
    config.Persistent !== false ||
    !/^\d+s$/.test(config.DefaultTimeout) ||
    +config.DefaultTimeout.slice(0, -1) < 1 ||
    +config.DefaultTimeout.slice(0, -1) > 86400 ||
    !config.CustomConfiguration ||
    !["personal", "enterprise"].includes(
      config.CustomConfiguration.ImageRegistryType,
    ) ||
    !config.RoleArn
  )
    throw Error("Invalid trusted Tool configuration");
  if (
    config.CustomConfiguration.Image ||
    config.StorageMounts?.length ||
    config.ComputerConfiguration
  )
    throw Error(
      "Publisher does not allow image overrides, mounts or computer configuration",
    );
  config.CustomConfiguration.Image = image;
  const identity = digest({
    environment,
    region,
    packId,
    profile,
    configuration: config,
  });
  // Resource sizing is mutable; all other configuration remains versioned.
  const stableConfig = structuredClone(config);
  if (stableConfig.CustomConfiguration.Resources) {
    delete stableConfig.CustomConfiguration.Resources.CPU;
    delete stableConfig.CustomConfiguration.Resources.Memory;
    delete stableConfig.CustomConfiguration.Resources.Storage;
  }
  const stableIdentity = digest({
    environment,
    region,
    packId,
    profile,
    configuration: stableConfig,
  });
  // The full hash is the API idempotency key; the name remains within AGS's 50-char limit.
  const request = {
    ...config,
    ToolName: `dp-${environment}-${stableIdentity.slice(7, 47)}`,
    ClientToken: identity.slice(7),
    Description: `domain-pack:${stableIdentity}`,
  };
  return { environment, region, packId, profile, identity, request };
}
function configurationDigest(request) {
  const config = {};
  for (const key of fields)
    if (request[key] != null) config[key] = request[key];
  config.DefaultTimeoutSeconds = Number(request.DefaultTimeout.slice(0, -1));
  return digest(config);
}
function readback(plan, tool) {
  if (!/^sdt-[a-z0-9]+$/.test(tool.ToolId || ""))
    throw Error("Invalid Tool ID");
  if (
    tool.ToolName !== plan.request.ToolName ||
    tool.Description !== plan.request.Description
  )
    throw Error("Tool publication identity mismatch");
  const expected = {},
    actual = {};
  for (const key of fields) {
    if (plan.request[key] != null) expected[key] = plan.request[key];
    if (tool[key] != null && !(Array.isArray(tool[key]) && !tool[key].length))
      actual[key] = structuredClone(tool[key]);
  }
  // AGS may expose the resolved digest alongside the immutable reference.
  if (actual.CustomConfiguration?.ImageDigest) {
    if (
      actual.CustomConfiguration.ImageDigest !==
      plan.request.CustomConfiguration.Image.split("@")[1]
    )
      throw Error("Tool image digest mismatch");
    delete actual.CustomConfiguration.ImageDigest;
  }
  expected.DefaultTimeoutSeconds = Number(
    plan.request.DefaultTimeout.slice(0, -1),
  );
  actual.DefaultTimeoutSeconds = tool.DefaultTimeoutSeconds;
  if (digest(expected) !== digest(actual))
    throw Error("Tool configuration differs from publication request");
  return {
    providerToolId: tool.ToolId,
    toolConfigurationDigest: digest(actual),
  };
}
async function findTool(plan, client) {
  const matches = [];
  let offset = 0;
  for (let page = 0; page < 100; page++) {
    const response = await client.DescribeSandboxToolList({
      Offset: offset,
      Limit: 100,
    });
    const rows = response.SandboxToolSet;
    if (
      !Array.isArray(rows) ||
      !Number.isSafeInteger(response.TotalCount) ||
      response.TotalCount < 0
    )
      throw Error("Invalid Tool list response");
    matches.push(
      ...rows.filter((tool) => tool.ToolName === plan.request.ToolName),
    );
    offset += rows.length;
    if (offset >= response.TotalCount) {
      if (matches.length > 1) throw Error("Ambiguous Tool identity");
      return matches[0];
    }
    if (!rows.length) throw Error("Incomplete Tool pagination");
  }
  throw Error("Tool pagination limit exceeded");
}
async function ensureTool(
  plan,
  client,
  {
    persist,
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    polls = 60,
  } = {},
) {
  let tool = await findTool(plan, client);
  let submitted = false;
  let updated = false;
  if (!tool) {
    // This callback must upload the intent durably, not merely write a runner file.
    if (typeof persist !== "function")
      throw Error("Durable publication intent required");
    await persist(plan);
    try {
      submitted = true;
      await client.CreateSandboxTool(plan.request);
    } catch {
      /* Outcome unknown: query, never blind replay. */
    }
  }
  for (let i = 0; i < polls; i++) {
    if (!tool) tool = await findTool(plan, client);
    if (tool) {
      if (tool.Status === "ACTIVE") {
        try {
          return {
            ...readback(plan, tool),
            toolOperation: updated
              ? "updated"
              : submitted
                ? "created"
                : "reused",
          };
        } catch (error) {
          // Prove that only CPU/memory/storage differ before updating anything.
          const normalized = structuredClone(tool);
          for (const key of ["CPU", "Memory", "Storage"]) {
            const value = plan.request.CustomConfiguration.Resources?.[key];
            if (value === undefined)
              delete normalized.CustomConfiguration.Resources?.[key];
            else {
              normalized.CustomConfiguration.Resources ??= {};
              normalized.CustomConfiguration.Resources[key] = value;
            }
          }
          readback(plan, normalized);
          if (!updated) {
            if (typeof persist !== "function")
              throw Error("Durable publication intent required");
            await persist(plan);
            updated = true;
            const custom = structuredClone(tool.CustomConfiguration);
            delete custom.ImageDigest;
            custom.Resources = structuredClone(
              plan.request.CustomConfiguration.Resources,
            );
            try {
              await client.UpdateSandboxTool({
                ToolId: tool.ToolId,
                CustomConfiguration: custom,
              });
            } catch {
              /* Query the result; never blindly replay an uncertain update. */
            }
          }
        }
      }
      if (!["CREATING", "ACTIVE"].includes(tool.Status))
        throw Error("Tool is not usable: " + tool.Status);
      if (
        tool.ToolName !== plan.request.ToolName ||
        tool.Description !== plan.request.Description
      )
        throw Error("Tool publication identity mismatch");
    }
    if (i + 1 < polls) {
      await sleep(5000);
      tool = undefined;
    }
  }
  throw Error(
    "Tool creation outcome unknown or still pending; reconcile the same publication identity",
  );
}
module.exports = {
  planTool,
  ensureTool,
  readback,
  digest,
  configurationDigest,
};
