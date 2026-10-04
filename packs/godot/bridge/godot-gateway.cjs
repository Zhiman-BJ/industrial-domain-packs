const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const Ajv = require('ajv');
const { Server } = require('@modelcontextprotocol/sdk/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} = require('@modelcontextprotocol/sdk/types.js');
const { validateResources } = require('../../../lib/resources.cjs');
const { ActionJournal } = require('@industrial-agent-harness/domain-runtime');

const MAX_TEXT = 16 * 1024,
  MAX_CACHE = 4 * 1024 * 1024,
  MAX_TOTAL_CACHE = 8 * 1024 * 1024;
const string = { type: 'string' };
const tool = (name, description, properties, required = [], mutating = false) => ({
  name,
  description,
  inputSchema: { type: 'object', properties, required, additionalProperties: false },
  annotations: { readOnlyHint: !mutating, destructiveHint: mutating, openWorldHint: false },
});
const gatewayTools = [
  tool('domain_tool_list', 'List Godot tool IDs allowed by the current Broker scope.', {
    offset: { type: 'integer', minimum: 0, maximum: 100 },
    limit: { type: 'integer', minimum: 1, maximum: 20 },
  }),
  tool(
    'domain_tool_describe',
    'Describe one allowed Godot tool and its pinned argument schema.',
    { toolId: string },
    ['toolId'],
  ),
  tool(
    'domain_tool_call',
    'Call one allowed Godot tool. Scene instantiation, import and run may execute project scripts or write files.',
    { toolId: string, arguments: { type: 'object' } },
    ['toolId', 'arguments'],
    true,
  ),
  tool(
    'domain_tool_result_read',
    'Read a page of a large cached Godot tool result.',
    {
      responseId: string,
      offset: { type: 'integer', minimum: 0, maximum: MAX_CACHE },
      limit: { type: 'integer', minimum: 1, maximum: 8000 },
    },
    ['responseId'],
  ),
];
const schemas = {
  'godot.game.project_status': { type: 'object', properties: {}, additionalProperties: false },
  'godot.game.inspect_scene_source': {
    type: 'object',
    properties: { scenePath: string },
    required: ['scenePath'],
    additionalProperties: false,
  },
  'godot.game.inspect_scene_runtime': {
    type: 'object',
    properties: { scenePath: string },
    required: ['scenePath'],
    additionalProperties: false,
  },
  'godot.game.check_project': { type: 'object', properties: {}, additionalProperties: false },
  'godot.game.run_scene': {
    type: 'object',
    properties: { scenePath: string, frames: { type: 'integer', minimum: 1, maximum: 180 } },
    required: ['scenePath', 'frames'],
    additionalProperties: false,
  },
};

function createGodotGateway(policy) {
  if (
    policy.schemaVersion !== 1 ||
    policy.providerId !== 'godot.local' ||
    !path.isAbsolute(policy.projectDir) ||
    !path.isAbsolute(policy.packDir) ||
    !path.isAbsolute(policy.binary) ||
    !Array.isArray(policy.allowedToolIds)
  )
    throw Error('Invalid Godot policy.');
  const allowed = new Map(
    policy.tools
      .filter(item => policy.allowedToolIds.includes(item.id))
      .map(item => [item.id, item]),
  );
  if (allowed.size !== policy.allowedToolIds.length || [...allowed.keys()].some(id => !schemas[id]))
    throw Error('Invalid Godot tool allowlist.');
  const project = fs.realpathSync(policy.projectDir);
  validateResources(policy.packDir, {
    title: 'Godot local game tools',
    sourceFiles: policy.sourceFiles,
    resourceRoots: policy.resourceRoots,
    sourceSha256: policy.sourceSha256,
  });
  const runtime = require(path.join(policy.packDir, 'src', 'runtime.cjs'));
  const ajv = new Ajv({ allErrors: true });
  const gatewayValidators = new Map(
    gatewayTools.map(item => [item.name, ajv.compile(item.inputSchema)]),
  );
  const toolValidators = new Map([...allowed.keys()].map(id => [id, ajv.compile(schemas[id])]));
  const cache = new Map();
  let actionJournal;
  let cacheBytes = 0;
  function checkBoundaries() {
    if (fs.realpathSync(policy.projectDir) !== project)
      throw Error('Bound project directory changed. Reconnect Godot tools.');
    runtime.boundProject(project);
    validateResources(policy.packDir, {
      title: 'Godot local game tools',
      sourceFiles: policy.sourceFiles,
      resourceRoots: policy.resourceRoots,
      sourceSha256: policy.sourceSha256,
    });
    if (!fs.statSync(policy.binary).isFile()) throw Error('Bound Godot executable is unavailable.');
  }
  function bounded(value) {
    const output = JSON.stringify(value);
    if (Buffer.byteLength(output) <= MAX_TEXT) return output;
    const bytes = Buffer.byteLength(output);
    if (bytes > MAX_CACHE)
      throw Error('Godot result exceeds 4 MiB. Request a narrower observation.');
    fs.mkdirSync(policy.cacheDir, { recursive: true, mode: 0o700 });
    while (cacheBytes + bytes > MAX_TOTAL_CACHE && cache.size) {
      const oldest = cache.keys().next().value,
        entry = cache.get(oldest);
      cache.delete(oldest);
      fs.rmSync(entry.file);
      cacheBytes -= entry.bytes;
    }
    const responseId = crypto.randomUUID();
    const file = path.join(policy.cacheDir, responseId + '.json');
    fs.writeFileSync(file, output, { flag: 'wx', mode: 0o600 });
    cache.set(responseId, {
      file,
      bytes,
      sha256: crypto.createHash('sha256').update(output).digest('hex'),
      characters: output.length,
    });
    cacheBytes += bytes;
    return JSON.stringify({
      paged: true,
      responseId,
      characters: output.length,
      sha256: cache.get(responseId).sha256,
      nextStep: 'Call domain_tool_result_read.',
    });
  }
  const server = new Server(
    { name: 'Industrial Godot MCP Gateway', version: policy.version },
    {
      capabilities: { tools: {} },
      instructions:
        'List the scoped Godot IDs, describe a tool, then call it. Godot import or scene execution is a native smoke check, not gameplay acceptance.',
    },
  );
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: gatewayTools }));
  server.setRequestHandler(CallToolRequestSchema, async request => {
    try {
      checkBoundaries();
      const args = request.params.arguments || {};
      const validate = gatewayValidators.get(request.params.name);
      if (!validate || !validate(args) || Buffer.byteLength(JSON.stringify(args)) > 65536)
        throw Error('Invalid Godot gateway arguments.');
      let result;
      switch (request.params.name) {
        case 'domain_tool_list': {
          const rows = [...allowed.values()].map(({ id, name, risk, summary }) => ({
            toolId: id,
            name,
            risk,
            summary,
          }));
          const offset = args.offset || 0,
            limit = args.limit || 20;
          result = {
            tools: rows.slice(offset, offset + limit),
            total: rows.length,
            nextOffset: offset + limit < rows.length ? offset + limit : null,
          };
          break;
        }
        case 'domain_tool_describe': {
          const descriptor = allowed.get(args.toolId);
          if (!descriptor) throw Error('Godot tool is outside the current Broker scope.');
          result = { ...descriptor, inputSchema: schemas[args.toolId], projectDir: project };
          break;
        }
        case 'domain_tool_call': {
          const descriptor = allowed.get(args.toolId);
          if (!descriptor) throw Error('Godot tool is outside the current Broker scope.');
          if (!toolValidators.get(args.toolId)(args.arguments))
            throw Error('Arguments do not match the pinned Godot tool schema.');
          const inputs = args.arguments;
          const script = path.join(policy.packDir, 'src', 'inspect_scene.gd');
          const calls = {
            'godot.game.project_status': () => runtime.projectStatus(project, policy.binaryVersion),
            'godot.game.inspect_scene_source': () =>
              runtime.inspectSceneSource(project, inputs.scenePath),
            'godot.game.inspect_scene_runtime': () =>
              runtime.inspectSceneRuntime(
                project,
                policy.binary,
                script,
                inputs.scenePath,
                policy.receiptDir,
              ),
            'godot.game.check_project': () =>
              runtime.checkProject(project, policy.binary, policy.receiptDir),
            'godot.game.run_scene': () =>
              runtime.runScene(
                project,
                policy.binary,
                inputs.scenePath,
                inputs.frames,
                policy.receiptDir,
              ),
          };
          if (descriptor.risk === 'mutating') {
            actionJournal ||= new ActionJournal(project, policy.domain);
            const native = await actionJournal.execute(
              args.toolId,
              inputs,
              calls[args.toolId],
              value => value?.importStatus !== 'FAIL' && value?.executionStatus !== 'failed',
            );
            result = {
              providerId: policy.providerId,
              toolId: args.toolId,
              projectDir: project,
              result: native,
            };
          } else
            result = {
              providerId: policy.providerId,
              toolId: args.toolId,
              projectDir: project,
              result: await calls[args.toolId](),
            };
          break;
        }
        case 'domain_tool_result_read': {
          const entry = cache.get(args.responseId);
          if (!entry) throw Error('Invalid or expired Godot response ID.');
          const raw = fs.readFileSync(entry.file, 'utf8');
          if (crypto.createHash('sha256').update(raw).digest('hex') !== entry.sha256)
            throw Error('Cached Godot response changed.');
          const offset = args.offset || 0,
            chunk = raw.slice(offset, offset + (args.limit || 8000));
          result = {
            responseId: args.responseId,
            offset,
            text: chunk,
            nextOffset: offset + chunk.length < raw.length ? offset + chunk.length : null,
            sha256: entry.sha256,
          };
          break;
        }
      }
      return { content: [{ type: 'text', text: bounded(result) }] };
    } catch (error) {
      return {
        content: [{ type: 'text', text: String(error.message).slice(0, 2048) }],
        isError: true,
      };
    }
  });
  return { server };
}

if (require.main === module) {
  (async () => {
    const file = process.argv[2];
    if (!file || (process.platform !== 'win32' && fs.statSync(file).mode & 0o077))
      throw Error('Godot policy must be private.');
    const { server } = createGodotGateway(JSON.parse(fs.readFileSync(file, 'utf8')));
    await server.connect(new StdioServerTransport());
  })().catch(() => {
    process.stderr.write('Godot MCP gateway failed to start.\n');
    process.exitCode = 1;
  });
}

module.exports = { createGodotGateway, gatewayTools, schemas };
