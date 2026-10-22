// Maintainer-authored CJS data only; cloud identities and credentials are not inputs.
const Ajv = require("ajv");
const profiles = {
  chip: "rtl-cpu",
  freecad: "freecad-linux-cpu",
  pcb: "pcb-verify-cpu",
};
const object = (properties) => ({
  type: "object",
  properties,
  required: Object.keys(properties),
  additionalProperties: false,
});
const string = { type: "string", minLength: 1 };
const strings = { type: "array", items: string };
const positive = { type: "integer", minimum: 1 };
const port = { ...positive, maximum: 65535 };
const domain = (profile) =>
  object({
    profile: { const: profile, type: "string" },
    networkMode: { enum: ["PUBLIC", "SANDBOX"], type: "string" },
    command: { ...strings, minItems: 1 },
    args: strings,
    ports: {
      type: "array",
      minItems: 1,
      items: object({
        name: string,
        port,
        protocol: { enum: ["TCP", "UDP"], type: "string" },
      }),
    },
    resources: object({
      cpu: { type: "string", pattern: "^(?:[0-9]+(?:\\.[0-9]+)?|[0-9]+m)$" },
      memory: { type: "string", pattern: "^[1-9][0-9]*(Mi|Gi)$" },
      storage: { type: "string", enum: ["", "1Gi", "5Gi", "10Gi", "20Gi"] },
    }),
    probe: object({
      httpGet: object({
        path: { type: "string", pattern: "^/" },
        port,
        scheme: { type: "string", enum: ["HTTP", "HTTPS"] },
      }),
      readyTimeoutMs: positive,
      probeTimeoutMs: positive,
      probePeriodMs: positive,
      successThreshold: positive,
      failureThreshold: positive,
    }),
  });
const ajv = new Ajv({ allErrors: true });
const validate = ajv.compile(
  object({
    schemaVersion: { type: "integer", const: 1 },
    region: { type: "string", pattern: "^[a-z]+-[a-z]+(?:-[a-z]+)?[0-9]?$" },
    domains: object(
      Object.fromEntries(
        Object.entries(profiles).map(([name, profile]) => [
          name,
          domain(profile),
        ]),
      ),
    ),
  }),
);
function buildPublicationConfig(input, selection) {
  let config;
  try {
    config = structuredClone(input);
  } catch {
    throw Error("Invalid publication configuration: export plain data only");
  }
  if (!validate(config))
    throw Error(
      "Invalid publication configuration: " + ajv.errorsText(validate.errors),
    );
  if (
    profiles[selection.directory] !== selection.profile ||
    !Object.hasOwn(profiles, selection.directory)
  )
    throw Error("Invalid publication domain/profile selection");
  const c = config.domains[selection.directory];
  return {
    schemaVersion: 1,
    region: config.region,
    imagePrefix: "ccr.ccs.tencentyun.com/industrial-mcp-remote",
    toolConfiguration: {
      ToolType: "custom",
      Persistent: false,
      DefaultTimeout: selection.directory === "chip" ? "1800s" : "900s",
      NetworkConfiguration: { NetworkMode: c.networkMode },
      CustomConfiguration: {
        ImageRegistryType: "personal",
        Command: c.command,
        Args: c.args,
        Ports: c.ports.map((p) => ({
          Name: p.name,
          Port: p.port,
          Protocol: p.protocol,
        })),
        Resources: {
          CPU: c.resources.cpu,
          Memory: c.resources.memory,
          Storage: c.resources.storage,
        },
        Probe: {
          HttpGet: {
            Path: c.probe.httpGet.path,
            Port: c.probe.httpGet.port,
            Scheme: c.probe.httpGet.scheme,
          },
          ReadyTimeoutMs: c.probe.readyTimeoutMs,
          ProbeTimeoutMs: c.probe.probeTimeoutMs,
          ProbePeriodMs: c.probe.probePeriodMs,
          SuccessThreshold: c.probe.successThreshold,
          FailureThreshold: c.probe.failureThreshold,
        },
      },
    },
  };
}
module.exports = { buildPublicationConfig };
