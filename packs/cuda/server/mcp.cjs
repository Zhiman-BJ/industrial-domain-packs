const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const Ajv = require('ajv');
const { Server } = require('@modelcontextprotocol/sdk/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { StreamableHTTPServerTransport } = require('@modelcontextprotocol/sdk/server/streamableHttp.js');
const { ListToolsRequestSchema, CallToolRequestSchema } = require('@modelcontextprotocol/sdk/types.js');
const { tools, version, protocol, profile, upstream, sha256, validateSource, validateIdentity } = require('../runtime/protocol.cjs');
const { RequestStore } = require('./store.cjs');
const { DockerBackend } = require('./backend.cjs');
function identity(config, role) {
  return validateIdentity({ protocol, packVersion: version, role, profile,
    packSourceSha256: require('../harness-pack.json').provider.sourceSha256,
    upstreamCommit: upstream.commit, upstreamContentSha256: upstream.contentSha256,
    imageId: config.imageId, platform: 'linux-amd64', driverVersion: '595.71.05',
    computeCapability: '8.9', gpuUuid: config.gpuUuid, taskSha256: config.taskSha256,
    verifierSha256: sha256(fs.readFileSync(path.join(__dirname, '../runtime/verifier.cjs'))) }, role);
}
class CudaService {
  constructor(config, backend = new DockerBackend(config)) {
    if (!/^[a-f0-9]{64}$/.test(config.projectId || '')) throw Error('Bind the CUDA allocation to one trusted Project ID.');
    this.config = config; this.backend = backend; this.store = new RequestStore(config.stateDirectory);
    this.identities = Object.fromEntries(['compiler', 'evaluator'].map(role => [role, identity(config, role)]));
    this.active = null;
    this.lock = path.join(this.store.directory, 'worker.lock');
  }
  async call(role, name, args, signal) {
    const tool = tools.find(item => item.role === role && item.name === name);
    if (!tool) throw Error('Tool is outside this CUDA MCP Server.');
    if (!new Ajv().validate(tool.inputSchema, args)) throw Error('Invalid CUDA tool arguments.');
    if (tool.risk === 'read-only') {
      if (args.requestId) {
        const entry = this.store.read(args.requestId);
        if (!entry || entry.role !== role || entry.projectId !== this.config.projectId) throw Error('CUDA request is unavailable in this MCP role.');
        return { identity: this.identities[role], requestStatus: { requestId: entry.requestId, status: entry.status, cleanupConfirmed: entry.cleanupConfirmed === true } };
      }
      return { identity: this.identities[role], ...await this.backend.check() };
    }
    if (args.projectId !== this.config.projectId) throw Error('CUDA request is bound to another Project.');
    const requestHash = sha256(JSON.stringify({ role, name, args })), prior = this.store.read(args.requestId);
    if (prior) {
      if (prior.requestHash !== requestHash) throw Error('CUDA request ID was already used with different inputs.');
      if (prior.status !== 'completed') throw Error('CUDA request is ' + prior.status + '; inspect evidence and use a new Action.');
      return prior.output;
    }
    if (this.active) throw Error('CUDA worker is busy; finish or cancel the current Action first.');
    const sequence = this.store.entries().length + 1;
    if (sequence > 4096) throw Error('CUDA session request limit reached.');
    const fd = fs.openSync(this.lock, 'wx', 0o600);
    fs.writeFileSync(fd, JSON.stringify({ pid: process.pid, requestId: args.requestId })); fs.closeSync(fd);
    const controller = new AbortController();
    const abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) controller.abort();
    this.active = controller;
    const entry = { requestId: args.requestId, requestHash, projectId: args.projectId,
      sequence, status: 'running', role, name };
    try {
      this.store.write(args.requestId, entry);
      await this.backend.check();
      if (controller.signal.aborted) throw Error('CUDA operation cancelled.');
      const operation = name === 'cuda_candidate_compile' ? 'compile' : name === 'cuda_baseline_profile' ? 'baseline' : name === 'cuda_candidate_verify' ? 'verification' : 'profiling';
      let record, sourceSha256, ticketId;
      if (operation === 'compile') {
        const source = validateSource(args.files);
        if (source.sourceSha256 !== args.sourceSha256) throw Error('CUDA source digest mismatch.');
        const submission = await this.backend.compile(args, sequence, controller.signal);
        if (submission.source_manifest_sha256 !== args.sourceSha256) throw Error('Compiler changed candidate source identity.');
        sourceSha256 = source.sourceSha256;
        ticketId = sha256(JSON.stringify({ projectId: args.projectId, requestId: args.requestId, sourceSha256, imageId: this.config.imageId }));
        entry.ticket = { ticketId, projectId: args.projectId, sourceSha256, submission, files: args.files, compileRequestId: args.requestId };
        record = { source_manifest_sha256: sourceSha256, compiler_compile_ok: submission.compiler_compile_ok,
          phase_trace: [submission.compiler_phase] };
      } else if (operation === 'baseline') {
        record = await this.backend.baseline(sequence, controller.signal);
        sourceSha256 = this.config.taskSha256;
      } else {
        const ticket = this.store.ticket(args.ticketId);
        if (ticket.projectId !== args.projectId) throw Error('Compiler ticket belongs to another Project.');
        sourceSha256 = ticket.sourceSha256; ticketId = ticket.ticketId;
        record = await this.backend.evaluate(ticket, operation, sequence, controller.signal);
      }
      const receipt = { schemaVersion: 1, protocol, identity: this.identities[role],
        requestId: args.requestId, projectId: args.projectId, operation, sourceSha256,
        ...(ticketId ? { ticketId } : {}), record };
      entry.output = { ...(ticketId ? { ticketId } : {}), receipt };
      entry.status = 'completed'; this.store.write(args.requestId, entry);
      return entry.output;
    } catch (error) {
      if (controller.signal.aborted || /timed out|output limit/i.test(error.message)) {
        try { await this.backend.cancel(); entry.cleanupConfirmed = true; }
        catch (cleanup) { entry.cleanupConfirmed = false; entry.cleanupError = cleanup.message; }
        entry.status = 'cancelled';
      } else entry.status = 'failed';
      entry.error = error.message; this.store.write(args.requestId, entry); throw error;
    } finally {
      signal?.removeEventListener('abort', abort); this.active = null;
      fs.unlinkSync(this.lock);
    }
  }
  async recover() {
    if (this.active) throw Error('Cannot recover an active CUDA operation.');
    if (fs.existsSync(this.lock)) {
      const { pid } = JSON.parse(fs.readFileSync(this.lock));
      try { process.kill(pid, 0); throw Error('Another CUDA service still owns the worker allocation.'); }
      catch (error) { if (error.code !== 'ESRCH') throw error; }
    }
    await this.backend.recover();
    for (const entry of this.store.entries()) if (entry.status === 'running') {
      entry.status = 'interrupted'; entry.cleanupConfirmed = true; this.store.write(entry.requestId, entry);
    }
    fs.rmSync(this.lock, { force: true });
  }
}
function createMcpServer(role, service) {
  if (!['compiler', 'evaluator'].includes(role)) throw Error('Unknown CUDA MCP Server role.');
  const server = new Server({ name: 'industrial-cuda-' + role, version }, { capabilities: { tools: {} } });
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: tools.filter(tool => tool.role === role).map(tool => ({
    name: tool.name, description: tool.summary, inputSchema: tool.inputSchema,
    annotations: { readOnlyHint: tool.risk === 'read-only', destructiveHint: false, openWorldHint: false },
  })) }));
  server.setRequestHandler(CallToolRequestSchema, async (request, extra) => {
    try {
      const output = await service.call(role, request.params.name, request.params.arguments || {}, extra.signal);
      return { content: [{ type: 'text', text: JSON.stringify(output.receipt ? {
        operation: output.receipt.operation, sourceSha256: output.receipt.sourceSha256,
        ticketId: output.ticketId, candidateId: output.receipt.record?.candidate_id,
      } : output) }], structuredContent: output };
    } catch (error) { return { isError: true, content: [{ type: 'text', text: String(error.message).slice(0, 2048) }] }; }
  });
  return server;
}
function startHttp(service, { compilerToken, evaluatorToken, port = 0, host = '127.0.0.1' }) {
  if (!['127.0.0.1', '::1'].includes(host) || !compilerToken || !evaluatorToken || compilerToken === evaluatorToken)
    throw Error('CUDA MCP needs loopback binding and separate nonempty role tokens.');
  const tokens = { compiler: compilerToken, evaluator: evaluatorToken };
  const sessions = new Map();
  const server = http.createServer(async (req, res) => {
    const match = /^\/(compiler|evaluator)\/mcp$/.exec(req.url || '');
    if (!match) { res.writeHead(404).end(); return; }
    const role = match[1], supplied = Buffer.from(req.headers.authorization || ''), expected = Buffer.from('Bearer ' + tokens[role]);
    if (req.headers.origin || supplied.length !== expected.length || !crypto.timingSafeEqual(supplied, expected)) { res.writeHead(401).end(); return; }
    if (!['POST', 'GET', 'DELETE'].includes(req.method)) { res.writeHead(405).end(); return; }
    let size = 0, body = [];
    try {
      for await (const chunk of req) { size += chunk.length; if (size > 14 * 1024 * 1024) { res.writeHead(413).end(); return; } body.push(chunk); }
      const parsed = size ? JSON.parse(Buffer.concat(body)) : undefined;
      const sessionId = req.headers['mcp-session-id'];
      let entry = sessionId && sessions.get(role + ':' + sessionId);
      if (!entry) {
        if (sessionId || req.method !== 'POST' || parsed?.method !== 'initialize') { res.writeHead(404).end(); return; }
        if (sessions.size >= 16) { res.writeHead(429).end(); return; }
        const mcp = createMcpServer(role, service);
        const transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => crypto.randomUUID(), enableJsonResponse: true,
          onsessioninitialized: id => sessions.set(role + ':' + id, { mcp, transport }),
          onsessionclosed: id => { sessions.delete(role + ':' + id); void mcp.close(); },
        });
        await mcp.connect(transport); entry = { mcp, transport };
      }
      await entry.transport.handleRequest(req, res, parsed);
    } catch { if (!res.headersSent) res.writeHead(400); res.end(); }
  });
  server.requestTimeout = 0;
  server.on('close', () => { for (const { mcp } of sessions.values()) void mcp.close(); sessions.clear(); });
  return new Promise(resolve => server.listen(port, host, () => resolve(server)));
}
async function main() {
  const args = process.argv.slice(2), file = args[0];
  if (!file || !path.isAbsolute(file)) throw Error('Supply an absolute administrator-owned CUDA configuration file.');
  const info = fs.lstatSync(file);
  if (!info.isFile() || info.isSymbolicLink() || (info.mode & 0o022)) throw Error('CUDA configuration cannot be writable by other users.');
  const config = JSON.parse(fs.readFileSync(file)), service = new CudaService(config);
  if (args.includes('--recover')) await service.recover();
  if (fs.existsSync(service.lock) || service.store.entries().some(entry => entry.status === 'running'))
    throw Error('Interrupted CUDA allocation requires explicit --recover before serving.');
  await service.backend.check();
  if (args.includes('--identity')) { process.stdout.write(JSON.stringify(service.identities) + '\n'); return; }
  let listener;
  for (const name of ['SIGTERM', 'SIGINT']) process.once(name, async () => {
    service.active?.abort(); listener?.close();
    while (service.active) await new Promise(resolve => setTimeout(resolve, 25));
    process.exit(0);
  });
  if (args.includes('--stdio')) {
    const role = args[args.indexOf('--stdio') + 1];
    await createMcpServer(role, service).connect(new StdioServerTransport());
  } else {
    listener = await startHttp(service, { port: config.port || 0,
      compilerToken: process.env.CUDA_COMPILER_MCP_TOKEN, evaluatorToken: process.env.CUDA_EVALUATOR_MCP_TOKEN });
    process.stderr.write('CUDA MCP listening on loopback port ' + listener.address().port + '\n');
  }
}
module.exports = { CudaService, createMcpServer, startHttp, identity };
if (require.main === module) main().catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
