const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StreamableHTTPClientTransport } = require('@modelcontextprotocol/sdk/client/streamableHttp.js');
const { StdioClientTransport } = require('@modelcontextprotocol/sdk/client/stdio.js');
const { CudaService, startHttp } = require('../packs/cuda/server/mcp.cjs');
const { sha256, validateSource, readSource, imageLock, tools } = require('../packs/cuda/runtime/protocol.cjs');
const { assess } = require('../packs/cuda/runtime/verifier.cjs');
const { createRuntimePlugin, configuration } = require('../packs/cuda/runtime/index.cjs');
const baseFiles = { 'model_new.py': Buffer.from('candidate').toString('base64'), 'kernels/example.cu': Buffer.from('source').toString('base64') };
const phase = (sequence, source, passed = true) => ({ sequence,
  paper_command: ['bash utils/compile.sh', 'python -m utils.verification', 'python -m utils.profiling'][sequence - 1],
  source_manifest_sha256: source, compiled_extension_sha256: 'e'.repeat(64),
  location: sequence === 1 ? 'cpu_compiler' : 'gpu_evaluator', execution: 'executed',
  outcome: passed ? 'passed' : 'failed', exit_code: passed ? 0 : 1, output: 'native evidence', output_sha256: sha256('native evidence'),
});
class FixtureBackend {
  constructor(config) { this.config = config; this.compiles = 0; this.cancelled = false; this.recovered = false; }
  async check() { if (this.cancelled && !this.recovered) throw Error('quarantined'); return { ready: true, nativeQualification: 'fixture-only' }; }
  async compile(args) { this.compiles++; return { source_manifest_sha256: args.sourceSha256, compiler_compile_ok: true, compiler_phase: phase(1, args.sourceSha256) }; }
  async baseline() { return { success: true, task_source_sha256: this.config.taskSha256,
    output: 'baseline', output_sha256: sha256('baseline'), kernel_breakdown: [{ name: 'reference' }] }; }
  async evaluate(ticket, operation) {
    return { candidate_id: 1, source_manifest_sha256: ticket.sourceSha256,
      policy_ok: true, compile_ok: true, correctness_ok: true,
      compiled_extension_sha256: 'e'.repeat(64), submitted_extension_sha256: 'e'.repeat(64),
      phase_trace: [phase(1, ticket.sourceSha256), phase(2, ticket.sourceSha256), ...(operation === 'profiling' ? [phase(3, ticket.sourceSha256)] : [])],
      correctness_inputs: Array.from({ length: 5 }, (_, input_index) => ({ input_index, outcome: 'passed' })),
      torch_compile_us: 10, cuda_us: 8, profile_classification: 'exclusive',
      pre_profile_snapshot: this.snapshot(), post_profile_snapshot: this.snapshot(),
    };
  }
  snapshot() { return { gpu_uuid: this.config.gpuUuid, exclusive: true, compute_process_count: 0, memory_used_mib: 1 }; }
  async cancel() { this.cancelled = true; }
  async recover() { this.recovered = true; }
}
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'cuda-mcp-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const config = { projectId: 'a'.repeat(64), stateDirectory: path.join(directory, 'state'),
    imageId: imageLock.imageId, gpuUuid: 'GPU-31ebe3bf-b2e5-5e2e-2350-131b6336dd39', taskSha256: sha256('reference') };
  const backend = new FixtureBackend(config), service = new CudaService(config, backend);
  return { directory, config, backend, service };
}
async function httpFixture(t) {
  const f = fixture(t), server = await startHttp(f.service, { compilerToken: 'compiler-token', evaluatorToken: 'evaluator-token' });
  const clients = {};
  t.after(async () => { await Promise.all(Object.values(clients).map(client => client.close())); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  for (const role of ['compiler', 'evaluator']) {
    const client = new Client({ name: 'contract-test', version: '1' });
    await client.connect(new StreamableHTTPClientTransport(new URL('http://127.0.0.1:' + server.address().port + '/' + role + '/mcp'), {
      requestInit: { headers: { Authorization: 'Bearer ' + role + '-token' } },
    })); clients[role] = client;
  }
  return { ...f, server, clients };
}
const request = (f, files = baseFiles) => ({ requestId: crypto.randomUUID(), projectId: f.config.projectId, files, sourceSha256: validateSource(files).sourceSha256 });

test('paired real HTTP MCP transports keep tool roles, credentials, project scope and tickets distinct', async t => {
  const f = await httpFixture(t);
  for (const role of ['compiler', 'evaluator']) {
    const listed = await f.clients[role].listTools();
    assert.deepEqual(listed.tools.map(tool => tool.name), tools.filter(tool => tool.role === role).map(tool => tool.name));
  }
  const args = request(f);
  const compiled = await f.clients.compiler.callTool({ name: 'cuda_candidate_compile', arguments: args });
  assert.equal(compiled.isError, undefined);
  const checked = await f.clients.evaluator.callTool({ name: 'cuda_candidate_verify', arguments: {
    requestId: crypto.randomUUID(), projectId: f.config.projectId, ticketId: compiled.structuredContent.ticketId,
  } });
  const receipt = checked.structuredContent.receipt;
  assert.equal(assess(receipt, { ...receipt, operation: 'verification' }).status, 'passed');
  const profiled = await f.clients.evaluator.callTool({ name: 'cuda_candidate_profile', arguments: { requestId: crypto.randomUUID(), projectId: f.config.projectId, ticketId: compiled.structuredContent.ticketId } });
  assert.equal(assess(profiled.structuredContent.receipt, profiled.structuredContent.receipt).status, 'passed');
  assert.equal((await f.clients.compiler.callTool({ name: 'cuda_candidate_verify', arguments: {} })).isError, true);
  assert.equal((await f.clients.compiler.callTool({ name: 'cuda_candidate_compile', arguments: { ...request(f), projectId: 'b'.repeat(64) } })).isError, true);
  assert.equal((await f.clients.evaluator.callTool({ name: 'cuda_candidate_profile', arguments: { requestId: crypto.randomUUID(), projectId: f.config.projectId, ticketId: 'b'.repeat(64) } })).isError, true);
  assert.equal((await fetch('http://127.0.0.1:' + f.server.address().port + '/evaluator/mcp', { method: 'POST', headers: { Authorization: 'Bearer compiler-token' } })).status, 401);
});

test('request receipts replay across restart and reject ID reuse, cancelled and interrupted work', async t => {
  const f = fixture(t), args = request(f);
  const output = await f.service.call('compiler', 'cuda_candidate_compile', args);
  const restarted = new CudaService(f.config, f.backend);
  assert.deepEqual(await restarted.call('compiler', 'cuda_candidate_compile', args), output);
  assert.equal(f.backend.compiles, 1);
  await assert.rejects(restarted.call('compiler', 'cuda_candidate_compile', { ...args, files: { 'model_new.py': '' } }), /different inputs/);
  const interrupted = { requestId: crypto.randomUUID(), status: 'running', requestHash: 'x' };
  restarted.store.write(interrupted.requestId, interrupted);
  await restarted.recover();
  assert.equal(restarted.store.read(interrupted.requestId).status, 'interrupted');
  assert.equal(f.backend.recovered, true);
});

test('actual MCP cancellation quarantines the allocation and records confirmed cleanup', async t => {
  const f = await httpFixture(t), controller = new AbortController();
  let started;
  const running = new Promise(resolve => { started = resolve; });
  f.backend.compile = async (_args, _seq, signal) => { started(); await new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(Error('cancelled')), { once: true });
  }); };
  const args = request(f);
  const call = f.clients.compiler.callTool({ name: 'cuda_candidate_compile', arguments: args }, undefined, { signal: controller.signal });
  await running; controller.abort(); await assert.rejects(call);
  for (let i = 0; i < 100 && !f.backend.cancelled; i++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(f.backend.cancelled, true);
  assert.equal(f.service.store.read(args.requestId).status, 'cancelled');
  assert.equal(f.service.store.read(args.requestId).cleanupConfirmed, true);
});

test('canonical plugin uses both real MCP Servers, saves native artifacts and rejects changed-source tickets', async t => {
  const f = await httpFixture(t), project = path.join(f.directory, 'project');
  fs.mkdirSync(path.join(project, 'kernels'), { recursive: true });
  fs.writeFileSync(path.join(project, 'model.py'), 'reference');
  for (const [name, bytes] of Object.entries(baseFiles)) fs.writeFileSync(path.join(project, name), Buffer.from(bytes, 'base64'));
  const environment = {};
  for (const role of ['compiler', 'evaluator']) {
    const prefix = 'INDUSTRIAL_HARNESS_CUDA_' + role.toUpperCase() + '_MCP_';
    environment[prefix + 'URL'] = 'http://127.0.0.1:' + f.server.address().port + '/' + role + '/mcp';
    environment[prefix + 'TOKEN'] = role + '-token'; environment[prefix + 'IDENTITY'] = sha256(JSON.stringify(f.service.identities[role]));
  }
  const plugin = createRuntimePlugin({ environment }); t.after(() => plugin.dispose());
  async function run(id) {
    const tool = plugin.tools.find(tool => tool.descriptor.id === id), action = { id: crypto.randomUUID(), projectId: f.config.projectId, toolId: id };
    const result = await tool.execute({ projectDir: project, project: { projectId: f.config.projectId }, action, inputs: {} });
    const artifacts = result.artifacts.map(item => ({ ...item, bytes: fs.readFileSync(item.file) }));
    return plugin.verifiers['cuda.candidate.evidence']({ result, action, artifacts, readArtifact: item => item.bytes });
  }
  assert.equal((await run('cuda.candidate.compile')).status, 'passed');
  assert.equal((await run('cuda.candidate.profile')).metrics.targetMet, true);
  fs.appendFileSync(path.join(project, 'model_new.py'), '\nchanged');
  await assert.rejects(run('cuda.candidate.verify'), /exact source/);
});

test('source and verifier boundaries reject links, binaries, receipt substitution and contended measurements', async t => {
  const f = fixture(t), args = request(f);
  assert.throws(() => validateSource({ ...baseFiles, 'kernels/result.so': 'eA==' }));
  assert.throws(() => validateSource({ ...baseFiles, 'kernels/../escape.cu': 'eA==' }));
  const dir = path.join(f.directory, 'source'); fs.mkdirSync(dir); fs.symlinkSync('/etc/hosts', path.join(dir, 'model_new.py'));
  assert.throws(() => readSource(dir), /ordinary/);
  const compiled = await f.service.call('compiler', 'cuda_candidate_compile', args);
  const out = await f.service.call('evaluator', 'cuda_candidate_profile', { requestId: crypto.randomUUID(), projectId: f.config.projectId, ticketId: compiled.ticketId });
  const receipt = out.receipt, expected = { ...receipt };
  assert.equal(assess(receipt, { ...expected, requestId: crypto.randomUUID() }).status, 'insufficient_evidence');
  receipt.record.profile_classification = 'contended';
  assert.equal(assess(receipt, expected).status, 'insufficient_evidence');
  receipt.record.correctness_ok = false;
  assert.equal(assess(receipt, expected).status, 'failed');
  assert.throws(() => configuration({ INDUSTRIAL_HARNESS_CUDA_COMPILER_MCP_URL: 'http://remote.example/mcp' }, 'compiler'), /HTTPS/);
});

test('both stdio MCP Server identities perform real initialization and tool discovery', async t => {
  const entry = path.join(__dirname, 'fixtures/cuda-stdio.cjs');
  for (const role of ['compiler', 'evaluator']) {
    const client = new Client({ name: 'stdio-smoke', version: '1' });
    const transport = new StdioClientTransport({ command: process.execPath, args: [entry, role], stderr: 'pipe' });
    await client.connect(transport);
    assert.equal(client.getServerVersion().name, 'industrial-cuda-' + role);
    assert.deepEqual((await client.listTools()).tools.map(item => item.name), tools.filter(item => item.role === role).map(item => item.name));
    const check = await client.callTool({ name: 'cuda_' + role + '_check', arguments: {} });
    assert.equal(check.structuredContent.ready, true); await client.close();
  }
});

test('canonical cancellation waits for durable cleanup and saves failure evidence before disposing the MCP session', async t => {
  const f = await httpFixture(t), project = path.join(f.directory, 'cancel-project');
  fs.mkdirSync(project); fs.writeFileSync(path.join(project, 'model.py'), 'reference'); fs.writeFileSync(path.join(project, 'model_new.py'), 'candidate');
  const environment = {};
  for (const role of ['compiler', 'evaluator']) {
    const prefix = 'INDUSTRIAL_HARNESS_CUDA_' + role.toUpperCase() + '_MCP_';
    environment[prefix + 'URL'] = 'http://127.0.0.1:' + f.server.address().port + '/' + role + '/mcp';
    environment[prefix + 'TOKEN'] = role + '-token'; environment[prefix + 'IDENTITY'] = sha256(JSON.stringify(f.service.identities[role]));
  }
  let started; const running = new Promise(resolve => { started = resolve; });
  f.backend.compile = async (_args, _seq, signal) => { started(); await new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(Error('cancelled')), { once: true })); };
  f.backend.cancel = async () => { await new Promise(resolve => setTimeout(resolve, 300)); f.backend.cancelled = true; };
  const plugin = createRuntimePlugin({ environment }); t.after(() => plugin.dispose());
  const controller = new AbortController(), action = { id: crypto.randomUUID(), projectId: f.config.projectId, toolId: 'cuda.candidate.compile' };
  const call = plugin.tools.find(tool => tool.descriptor.id === action.toolId).execute({ projectDir: project, project: { projectId: f.config.projectId }, action, inputs: {}, signal: controller.signal });
  await running; controller.abort(); const result = await call;
  assert.equal(result.executionSucceeded, false); assert.equal(f.service.store.read(action.id).cleanupConfirmed, true);
  assert.equal(JSON.parse(fs.readFileSync(result.artifacts[0].file)).cleanupConfirmed, true);
  assert.equal(result.artifacts[0].kind, 'report.cuda.cancellation');
  const check = await f.clients.evaluator.callTool({ name: 'cuda_evaluator_check', arguments: { requestId: action.id } });
  assert.equal(check.isError, true);
});
