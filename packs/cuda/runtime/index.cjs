const fs = require('node:fs');
const path = require('node:path');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StreamableHTTPClientTransport } = require('@modelcontextprotocol/sdk/client/streamableHttp.js');
const { version, readSource, validateIdentity, tools, descriptors, sha256 } = require('./protocol.cjs');
const { verifier, assess, operations } = require('./verifier.cjs');
function configuration(environment, role) {
  const prefix = 'INDUSTRIAL_HARNESS_CUDA_' + role.toUpperCase() + '_MCP_';
  const endpoint = environment[prefix + 'URL'];
  if (!endpoint) return null;
  const url = new URL(endpoint);
  if (url.username || url.password || url.search || url.hash ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname))))
    throw Error('CUDA MCP needs HTTPS or an authenticated loopback tunnel.');
  const token = environment[prefix + 'TOKEN'], identitySha256 = environment[prefix + 'IDENTITY'];
  if (!token || /[\r\n]/.test(token) || !/^[a-f0-9]{64}$/.test(identitySha256 || ''))
    throw Error('Configure each trusted CUDA MCP token and exact service identity digest.');
  return { url, token, identitySha256 };
}
function evidenceDirectory(projectDir, actionId) {
  if (actionId && !/^[a-f0-9-]{36}$/.test(actionId)) throw Error('Invalid CUDA Action ID.');
  const root = fs.realpathSync(projectDir);
  let directory = root;
  for (const part of ['.cuda', ...(actionId ? ['actions', actionId] : [])]) {
    directory = path.join(directory, part);
    const info = fs.lstatSync(directory, { throwIfNoEntry: false });
    if (info && (!info.isDirectory() || info.isSymbolicLink())) throw Error('CUDA evidence directories must be ordinary directories.');
    if (!info) fs.mkdirSync(directory, { mode: 0o700 });
  }
  return directory;
}
function createRuntimePlugin({ environment = process.env } = {}) {
  const configs = Object.fromEntries(['compiler', 'evaluator'].map(role => [role, configuration(environment, role)]));
  if (Boolean(configs.compiler) !== Boolean(configs.evaluator)) throw Error('Configure both CUDA MCP Servers together.');
  const clients = {}, connecting = {};
  async function connect(role, projectDir) {
    if (!configs[role]) throw Error('Remote CUDA MCP is not configured.');
    if (!clients[role] && !connecting[role]) connecting[role] = (async () => {
      const client = new Client({ name: 'industrial-cuda-pack-' + role, version });
      const transport = new StreamableHTTPClientTransport(configs[role].url, { requestInit: { headers: { Authorization: 'Bearer ' + configs[role].token } } });
      try {
        await client.connect(transport, { timeout: 10000 });
        const response = await client.callTool({ name: 'cuda_' + role + '_check', arguments: {} });
        const identity = validateIdentity(response.structuredContent?.identity, role);
        if (response.isError || sha256(JSON.stringify(identity)) !== configs[role].identitySha256 ||
            identity.verifierSha256 !== sha256(fs.readFileSync(path.join(__dirname, 'verifier.cjs'))))
          throw Error('Remote CUDA identity/Verifier mismatch.');
        clients[role] = { client, transport, identity };
      } catch (error) { await client.close().catch(() => {}); throw error; }
    })().finally(() => { delete connecting[role]; });
    if (connecting[role]) await connecting[role];
    if (clients[role].identity.taskSha256 !== sha256(fs.readFileSync(path.join(projectDir, 'model.py'))))
      throw Error('Bound reference model differs from the remote CUDA task.');
    return clients[role];
  }
  function candidateTicket(projectDir, projectId, sourceSha256) {
    const file = path.join(evidenceDirectory(projectDir), 'ticket.json');
    const info = fs.lstatSync(file);
    if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > 256 * 1024) throw Error('Invalid local compiler receipt.');
    const receipt = JSON.parse(fs.readFileSync(file));
    const expected = { requestId: receipt.requestId, projectId, sourceSha256, operation: 'compile' };
    if (sha256(JSON.stringify(receipt.identity)) !== configs.compiler.identitySha256 || assess(receipt, expected).status !== 'passed')
      throw Error('Compile this exact source successfully before GPU evaluation.');
    return receipt.ticketId;
  }
  return {
    available: Boolean(configs.compiler),
    matchesProject: dir => fs.existsSync(path.join(dir, 'model_new.py')) && fs.existsSync(path.join(dir, 'model.py')),
    protectedPaths: [path.resolve(__dirname, '..')],
    workspaceProtectedPaths: dir => ['.cuda', 'model.py', 'binding.cpp', 'binding_registry.h', 'utils', 'CAPABILITIES.json'].map(name => path.join(dir, name)),
    stateProvider: ({ projectDir }) => {
      const source = readSource(projectDir), model = path.join(projectDir, 'model.py');
      if (fs.lstatSync(model).isSymbolicLink()) throw Error('Reference model cannot be a symlink.');
      return { stage: 'kernel', inputHashes: { ...source.hashes, 'model.py': sha256(fs.readFileSync(model)) } };
    },
    tools: configs.compiler ? descriptors.map((descriptor, index) => ({ descriptor,
      guide: { inputs: {}, description: tools[index].summary + ' Uses bound candidate files; accepts no command, endpoint or GPU overrides.' },
      execute: async ({ projectDir, project, action, inputs, signal }) => {
        if (Object.keys(inputs).length) throw Error('CUDA tools accept only an empty inputs object.');
        const tool = tools[index], { client, identity } = await connect(tool.role, projectDir);
        const source = readSource(projectDir);
        let args = {};
        if (descriptor.risk === 'mutating') {
          args = { requestId: action.id, projectId: project.projectId };
          if (operations[tool.id] === 'compile') Object.assign(args, { sourceSha256: source.sourceSha256, files: source.files });
          else if (operations[tool.id] !== 'baseline') args.ticketId = candidateTicket(projectDir, project.projectId, source.sourceSha256);
        }
        let response;
        try { response = await client.callTool({ name: tool.name, arguments: args }, undefined, { signal, timeout: 900000 }); }
        catch (error) {
          if (!signal?.aborted || descriptor.risk !== 'mutating') throw error;
          // Keep the session alive until the MCP cancellation notification is
          // delivered and the owned native allocation reports confirmed cleanup.
          let requestStatus;
          const deadline = Date.now() + 30000;
          do {
            await new Promise(resolve => setTimeout(resolve, 200));
            const status = await client.callTool({ name: 'cuda_' + tool.role + '_check', arguments: { requestId: action.id } }, undefined, { timeout: 5000 }).catch(() => null);
            if (sha256(JSON.stringify(status?.structuredContent?.identity || {})) === configs[tool.role].identitySha256) requestStatus = status.structuredContent.requestStatus;
            if (requestStatus && requestStatus.status !== 'running') break;
          } while (Date.now() < deadline);
          const cleanupConfirmed = requestStatus?.status === 'cancelled' && requestStatus.cleanupConfirmed === true;
          const file = path.join(evidenceDirectory(projectDir, action.id), 'cancellation.json');
          fs.writeFileSync(file, JSON.stringify({ identity, requestId: action.id, projectId: project.projectId, requestStatus: requestStatus || null, cleanupConfirmed }) + '\n', { mode: 0o600, flag: 'wx' });
          return { executionSucceeded: false, artifacts: [{ kind: 'report.cuda.cancellation', file, sha256: sha256(fs.readFileSync(file)) }], diagnostics: [cleanupConfirmed ? 'CUDA operation cancelled; owned native worker cleanup confirmed.' : 'CUDA cancellation cleanup could not be confirmed; operator inspection and recovery required.'] };
        }
        if (response.isError) throw Error(response.content?.find(item => item.type === 'text')?.text || 'CUDA MCP rejected the operation.');
        if (descriptor.risk === 'read-only') {
          if (sha256(JSON.stringify(response.structuredContent?.identity)) !== configs[tool.role].identitySha256) throw Error('CUDA worker identity changed.');
          return { executionSucceeded: true, artifacts: [], diagnostics: [JSON.stringify(response.structuredContent)] };
        }
        const receipt = response.structuredContent?.receipt;
        if (!receipt || sha256(JSON.stringify(receipt.identity)) !== configs[tool.role].identitySha256) throw Error('CUDA receipt identity changed.');
        const output = evidenceDirectory(projectDir, action.id);
        const file = path.join(output, 'receipt.json');
        fs.writeFileSync(file, JSON.stringify(receipt) + '\n', { mode: 0o600, flag: 'wx' });
        const sourceSha256 = operations[tool.id] === 'baseline' ? identity.taskSha256 : source.sourceSha256;
        if (tool.id === 'cuda.candidate.compile' && assess(receipt, { requestId: action.id, projectId: project.projectId, sourceSha256, operation: 'compile' }).status === 'passed') {
          const target = path.join(projectDir, '.cuda', 'ticket.json');
          if (fs.lstatSync(target, { throwIfNoEntry: false })?.isSymbolicLink()) throw Error('Local compiler ticket cannot be a symlink.');
          const tmp = target + '.' + action.id;
          fs.writeFileSync(tmp, JSON.stringify(receipt), { mode: 0o600, flag: 'wx' }); fs.renameSync(tmp, target);
        }
        return { executionSucceeded: true, sourceSha256,
          artifacts: [{ kind: 'report.cuda.candidate', file, sha256: sha256(fs.readFileSync(file)) }], diagnostics: [] };
      },
    })) : [],
    verifiers: { 'cuda.candidate.evidence': verifier },
    capabilities: configs.compiler ? require('../harness-pack.json').capabilities : [],
    dispose: async () => { await Promise.all(Object.values(connecting).map(promise => promise.catch(() => {})));
      await Promise.all(Object.values(clients).map(async ({ client, transport }) => {
        await Promise.race([transport.terminateSession().catch(() => {}), new Promise(resolve => { const timer = setTimeout(resolve, 2000); timer.unref(); })]);
        await client.close();
      })); },
  };
}
module.exports = { createRuntimePlugin, configuration };
