// Runs the paired native MCP flow through the Pack's canonical plugin. Credentials
// come exclusively from trusted environment settings. Receipts stay in the Project.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createRuntimePlugin } = require('../runtime/index.cjs');
const { sha256 } = require('../runtime/protocol.cjs');
async function qualify({ projectDir, projectId, environment = process.env, onStep = () => {} }) {
  if (!path.isAbsolute(projectDir) || !/^[a-f0-9]{64}$/.test(projectId)) throw Error('Supply an absolute bound Project and its trusted ID.');
  const plugin = createRuntimePlugin({ environment }), steps = [];
  if (!plugin.available) throw Error('Configure both authenticated CUDA MCP Servers.');
  try {
    for (const id of ['cuda.compiler.check', 'cuda.evaluator.check', 'cuda.baseline.profile', 'cuda.candidate.compile', 'cuda.candidate.verify', 'cuda.candidate.profile']) {
      const tool = plugin.tools.find(item => item.descriptor.id === id);
      const action = { id: crypto.randomUUID(), projectId, toolId: id };
      const result = await tool.execute({ projectDir, project: { projectId }, action, inputs: {} });
      const artifacts = result.artifacts.map(item => ({ ...item, bytes: fs.readFileSync(item.file) }));
      const verdict = tool.descriptor.risk === 'read-only' ? { status: 'passed' } : plugin.verifiers['cuda.candidate.evidence']({ result, action, artifacts, readArtifact: item => item.bytes });
      const step = { toolId: id, actionId: action.id, ...verdict, artifacts: result.artifacts.map(item => ({ kind: item.kind, sha256: sha256(item.bytes || fs.readFileSync(item.file)) })) };
      steps.push(step); onStep(step);
      if (verdict.status !== 'passed') throw Error(id + ': ' + verdict.reason);
    }
    return { profile: 'rtx4090-sm89', passed: true, steps };
  } finally { await plugin.dispose(); }
}
module.exports = { qualify };
if (require.main === module) qualify({ projectDir: process.argv[2], projectId: process.argv[3], onStep: step => process.stdout.write(JSON.stringify(step) + '\n') }).catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
