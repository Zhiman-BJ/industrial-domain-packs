const test = require("node:test");
const assert = require("node:assert/strict");
const {
  planTool,
  ensureTool,
} = require("../scripts/runtime-tool-publication.cjs");
const image = "ccr.ccs.tencentyun.com/example/rtl@sha256:" + "a".repeat(64);
const base = {
  environment: "test",
  region: "ap-guangzhou",
  packId: "chip-pack",
  profile: "rtl-cpu",
  image,
  configuration: {
    ToolType: "custom",
    NetworkConfiguration: { NetworkMode: "PUBLIC" },
    RoleArn: "qcs::cam::uin/123:roleName/pull",
    DefaultTimeout: "300s",
    Persistent: false,
    CustomConfiguration: { ImageRegistryType: "personal" },
  },
};
const plan = () => planTool(base);
function cloud(p, options = {}) {
  let row = options.existing;
  const calls = [];
  return {
    calls,
    async DescribeSandboxToolList() {
      return { TotalCount: row ? 1 : 0, SandboxToolSet: row ? [row] : [] };
    },
    async CreateSandboxTool(request) {
      calls.push(request);
      row = {
        ...structuredClone(request),
        ToolId: "sdt-test1234",
        Status: "ACTIVE",
        DefaultTimeoutSeconds: 300,
      };
      delete row.ClientToken;
      delete row.DefaultTimeout;
      if (options.drift)
        row.CustomConfiguration.Image = image.slice(0, -64) + "b".repeat(64);
      if (options.timeout) throw Error("network timeout");
      return { ToolId: row.ToolId };
    },
    row: () => row,
  };
}
test("stable publication identity is isolated by environment and immutable image", () => {
  assert.deepEqual(plan(), plan());
  assert.notEqual(
    plan().request.ClientToken,
    planTool({ ...base, environment: "prod" }).request.ClientToken,
  );
  assert.notEqual(
    plan().request.ToolName,
    planTool({ ...base, image: image.slice(0, -64) + "b".repeat(64) }).request
      .ToolName,
  );
  assert.throws(
    () => planTool({ ...base, image: "example:latest" }),
    /immutable/i,
  );
});
test("persist intent before create; return only active matching readback", async () => {
  const p = plan(),
    c = cloud(p),
    events = [];
  const out = await ensureTool(p, c, {
    persist: async () => events.push(c.calls.length),
    sleep: async () => {},
    polls: 2,
  });
  assert.deepEqual(events, [0]);
  assert.equal(out.providerToolId, "sdt-test1234");
  assert.match(out.toolConfigurationDigest, /^sha256:[a-f0-9]{64}$/);
});
test("persistence failure prevents creation", async () => {
  const p = plan(),
    c = cloud(p);
  await assert.rejects(
    ensureTool(p, c, {
      persist: async () => {
        throw Error("storage unavailable");
      },
    }),
    /storage/,
  );
  assert.equal(c.calls.length, 0);
});
test("create timeout reconciles without a second create", async () => {
  const p = plan(),
    c = cloud(p, { timeout: true });
  assert.equal(
    (
      await ensureTool(p, c, {
        persist: async () => {},
        sleep: async () => {},
        polls: 2,
      })
    ).providerToolId,
    "sdt-test1234",
  );
  assert.equal(c.calls.length, 1);
});
test("unresolved create is unknown and never replayed in the invocation", async () => {
  const p = plan();
  let creates = 0;
  const c = {
    DescribeSandboxToolList: async () => ({
      TotalCount: 0,
      SandboxToolSet: [],
    }),
    CreateSandboxTool: async () => {
      creates++;
      throw Error("timeout");
    },
  };
  await assert.rejects(
    ensureTool(p, c, {
      persist: async () => {},
      sleep: async () => {},
      polls: 2,
    }),
    /unknown/i,
  );
  assert.equal(creates, 1);
});
test("existing matching tool is reused; changed readback is rejected", async () => {
  const p = plan(),
    c = cloud(p);
  await ensureTool(p, c, {
    persist: async () => {},
    sleep: async () => {},
    polls: 2,
  });
  const existing = cloud(p, { existing: c.row() });
  await ensureTool(p, existing, {
    persist: async () => {},
    sleep: async () => {},
    polls: 2,
  });
  assert.equal(existing.calls.length, 0);
  existing.row().CustomConfiguration.Image = "wrong";
  await assert.rejects(
    ensureTool(p, existing, { persist: async () => {} }),
    /configuration/i,
  );
});
test("pagination checks all tools and query errors never permit create", async () => {
  const p = plan();
  let creates = 0;
  const c = {
    DescribeSandboxToolList: async () => {
      throw Error("unauthorized");
    },
    CreateSandboxTool: async () => {
      creates++;
    },
  };
  await assert.rejects(
    ensureTool(p, c, { persist: async () => {} }),
    /unauthorized/,
  );
  assert.equal(creates, 0);
});
test("a matching tool on a later page prevents creation", async () => {
  const p = plan(),
    c = cloud(p);
  await ensureTool(p, c, {
    persist: async () => {},
    sleep: async () => {},
    polls: 2,
  });
  let creates = 0;
  const paged = {
    DescribeSandboxToolList: async ({ Offset }) => ({
      TotalCount: 2,
      SandboxToolSet: Offset ? [c.row()] : [{ ToolName: "unrelated" }],
    }),
    CreateSandboxTool: async () => {
      creates++;
    },
  };
  assert.equal((await ensureTool(p, paged, {})).providerToolId, "sdt-test1234");
  assert.equal(creates, 0);
});
test("failed tools and mismatched active configuration never publish", async () => {
  const p = plan(),
    c = cloud(p);
  await ensureTool(p, c, {
    persist: async () => {},
    sleep: async () => {},
    polls: 2,
  });
  c.row().Status = "FAILED";
  await assert.rejects(ensureTool(p, c, {}), /not usable/);
  c.row().Status = "ACTIVE";
  c.row().CustomConfiguration.Env = [{ Name: "INJECTED", Value: "1" }];
  await assert.rejects(ensureTool(p, c, {}), /configuration/);
});
test("nullable cloud detail fields normalize without masking nonempty overrides", async () => {
  const p = plan(),
    c = cloud(p);
  await ensureTool(p, c, {
    persist: async () => {},
    sleep: async () => {},
    polls: 2,
  });
  Object.assign(c.row().CustomConfiguration, {
    Command: null,
    Args: [],
    Env: [],
    Probe: null,
  });
  assert.equal((await ensureTool(p, c, {})).providerToolId, "sdt-test1234");
});
test("CPU/memory changes preserve Tool name and update existing Tool once with verified readback", async () => {
  const before = structuredClone(base);
  before.configuration.CustomConfiguration.Resources = {
    CPU: "1",
    Memory: "1Gi",
    Storage: "1Gi",
  };
  const after = structuredClone(before);
  after.configuration.CustomConfiguration.Resources = {
    CPU: "2",
    Memory: "2Gi",
    Storage: "1Gi",
  };
  const p = planTool(before),
    q = planTool(after);
  assert.equal(p.request.ToolName, q.request.ToolName);
  assert.notEqual(p.identity, q.identity);
  const c = cloud(p);
  await c.CreateSandboxTool(p.request);
  let updates = 0,
    persisted = false;
  c.UpdateSandboxTool = async (req) => {
    assert.equal(persisted, true);
    updates++;
    c.row().CustomConfiguration = structuredClone(req.CustomConfiguration);
    throw Error("response lost");
  };
  const out = await ensureTool(q, c, {
    persist: async () => {
      persisted = true;
    },
    sleep: async () => {},
    polls: 3,
  });
  assert.equal(out.toolOperation, "updated");
  assert.equal(updates, 1);
  assert.equal(c.calls.length, 1);
  assert.equal(
    (await ensureTool(q, c, { sleep: async () => {}, polls: 1 })).toolOperation,
    "reused",
  );
  const storage = structuredClone(after);
  storage.configuration.CustomConfiguration.Resources.Storage = "5Gi";
  assert.equal(planTool(storage).request.ToolName, q.request.ToolName);
  const disk = await ensureTool(planTool(storage), c, {
    persist: async () => {},
    sleep: async () => {},
    polls: 3,
  });
  assert.equal(disk.toolOperation, "updated");
  assert.equal(updates, 2);
});
