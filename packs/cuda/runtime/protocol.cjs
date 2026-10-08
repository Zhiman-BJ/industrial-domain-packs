const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const version = '0.1.0', protocol = 'cuda-domain-mcp/1', profile = 'rtx4090-sm89';
const upstream = require('../locks/upstream.json');
const imageLock = require('../locks/rtx4090-image.json');
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const MAX_SOURCE_BYTES = 8 * 1024 * 1024;
const sourceName = name => name === 'model_new.py' ||
  /^kernels\/(?:[A-Za-z0-9][A-Za-z0-9_-]*\/)*[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:cu|cpp|cuh|h|hpp)$/.test(name);
function validateSource(files) {
  if (!files || typeof files !== 'object' || Array.isArray(files) ||
      !Object.hasOwn(files, 'model_new.py') || Object.keys(files).length > 256)
    throw Error('Supply model_new.py and at most 255 kernel source files.');
  const hashes = {}; let size = 0;
  for (const name of Object.keys(files).sort()) {
    const encoded = files[name];
    if (!sourceName(name) || typeof encoded !== 'string' || encoded.length > 12 * 1024 * 1024 ||
        !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded))
      throw Error('Only canonical base64 candidate source files are accepted.');
    const bytes = Buffer.from(encoded, 'base64');
    if (bytes.toString('base64') !== encoded || (size += bytes.length) > MAX_SOURCE_BYTES)
      throw Error('Candidate source exceeds its encoding or size limit.');
    hashes[name] = sha256(bytes);
  }
  return { hashes, sourceSha256: sha256(JSON.stringify(hashes)), sizeBytes: size };
}
function readSource(projectDir) {
  const root = fs.realpathSync(projectDir), files = {}; let visited = 0, total = 0;
  function read(name) {
    const file = path.join(root, name), info = fs.lstatSync(file);
    if (info.isSymbolicLink() || !info.isFile() || info.nlink !== 1 || (total += info.size) > MAX_SOURCE_BYTES)
      throw Error('Candidate source must contain bounded ordinary files.');
    files[name] = fs.readFileSync(file).toString('base64');
    if (Object.keys(files).length > 256) throw Error('Candidate source file limit exceeded.');
  }
  read('model_new.py');
  function visit(name, depth = 0) {
    if (++visited > 512 || depth > 16) throw Error('Candidate tree exceeds its limits.');
    const info = fs.lstatSync(path.join(root, name));
    if (info.isSymbolicLink()) throw Error('Candidate sources cannot contain symlinks.');
    if (info.isDirectory()) {
      for (const entry of fs.readdirSync(path.join(root, name)).sort()) visit(name + '/' + entry, depth + 1);
    } else { if (!sourceName(name)) throw Error('Remove generated or unsupported files from kernels/.'); read(name); }
  }
  if (fs.existsSync(path.join(root, 'kernels'))) visit('kernels');
  return { files, ...validateSource(files) };
}
function validateIdentity(identity, role) {
  if (identity?.protocol !== protocol || identity.packVersion !== version || identity.role !== role ||
      identity.profile !== profile || identity.packSourceSha256 !== require('../harness-pack.json').provider.sourceSha256 || identity.upstreamCommit !== upstream.commit ||
      identity.upstreamContentSha256 !== upstream.contentSha256 ||
      identity.imageId !== imageLock.imageId ||
      identity.platform !== 'linux-amd64' || identity.driverVersion !== '595.71.05' ||
      identity.computeCapability !== '8.9' ||
      !/^GPU-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(identity.gpuUuid || '') ||
      !/^[a-f0-9]{64}$/.test(identity.taskSha256 || '') ||
      !/^[a-f0-9]{64}$/.test(identity.verifierSha256 || ''))
    throw Error('Remote CUDA service identity differs from the pinned Pack/profile.');
  return identity;
}
const checkSchema = { type: 'object', properties: { requestId: { type: 'string', pattern: '^[a-f0-9-]{36}$' } }, additionalProperties: false };
const common = {
  requestId: { type: 'string', pattern: '^[a-f0-9-]{36}$' },
  projectId: { type: 'string', pattern: '^[a-f0-9]{64}$' },
};
const sourceSchema = { type: 'object', additionalProperties: false,
  required: ['requestId', 'projectId', 'sourceSha256', 'files'], properties: {
    ...common, sourceSha256: { type: 'string', pattern: '^[a-f0-9]{64}$' },
    files: { type: 'object', minProperties: 1, maxProperties: 256,
      additionalProperties: { type: 'string', maxLength: 12 * 1024 * 1024 } },
  } };
const ticketSchema = { type: 'object', additionalProperties: false,
  required: ['requestId', 'projectId', 'ticketId'], properties: {
    ...common, ticketId: { type: 'string', pattern: '^[a-f0-9]{64}$' },
  } };
const tools = [
  { id: 'cuda.compiler.check', name: 'cuda_compiler_check', role: 'compiler', risk: 'read-only', summary: 'Read the pinned CPU compiler identity and readiness.', inputSchema: checkSchema },
  { id: 'cuda.candidate.compile', name: 'cuda_candidate_compile', role: 'compiler', risk: 'mutating', summary: 'Compile source on the isolated CPU worker and issue a project-bound ticket.', verification: ['cuda.candidate.evidence'], inputSchema: sourceSchema },
  { id: 'cuda.evaluator.check', name: 'cuda_evaluator_check', role: 'evaluator', risk: 'read-only', summary: 'Read the pinned RTX 4090 evaluator identity and readiness.', inputSchema: checkSchema },
  { id: 'cuda.baseline.profile', name: 'cuda_baseline_profile', role: 'evaluator', risk: 'mutating', summary: 'Profile the immutable reference model on the isolated GPU before editing.', verification: ['cuda.candidate.evidence'], inputSchema: { type: 'object', additionalProperties: false, required: ['requestId', 'projectId'], properties: common } },
  { id: 'cuda.candidate.verify', name: 'cuda_candidate_verify', role: 'evaluator', risk: 'mutating', summary: 'Verify a trusted compiler ticket on the isolated GPU.', verification: ['cuda.candidate.evidence'], inputSchema: ticketSchema },
  { id: 'cuda.candidate.profile', name: 'cuda_candidate_profile', role: 'evaluator', risk: 'mutating', summary: 'Verify a trusted compiler ticket then measure against torch.compile on the GPU.', verification: ['cuda.candidate.evidence'], inputSchema: ticketSchema },
];
const descriptors = tools.map(tool => ({ schemaVersion: '1', id: tool.id, version, risk: tool.risk, verification: tool.verification || [] }));
module.exports = { version, protocol, profile, upstream, imageLock, sha256, validateSource, readSource, validateIdentity, tools, descriptors, MAX_SOURCE_BYTES };
