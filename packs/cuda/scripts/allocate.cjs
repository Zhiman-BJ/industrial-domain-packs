// Backend provisioning for one administrator-bound AXPBY allocation. This is
// never invoked from project inputs or an agent-exposed MCP tool.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execute } = require('../server/backend.cjs');
const { requireLocalGpu } = require('./preflight.cjs');
const { upstream, imageLock, sha256 } = require('../runtime/protocol.cjs');
async function allocate(config, { checkGpu = requireLocalGpu } = {}) {
  if (!path.isAbsolute(config.upstreamRoot || '') || !path.isAbsolute(config.stateDirectory || '') ||
      config.imageId !== imageLock.imageId ||
      !/^[a-z0-9][a-z0-9-]{1,62}$/.test(config.allocation || '') ||
      !/^[a-f0-9]{64}$/.test(config.projectId || '')) throw Error('Invalid administrator allocation configuration.');
  await checkGpu(config);
  const root = fs.realpathSync(config.upstreamRoot), directory = path.dirname(config.stateDirectory);
  const envPath = 'tasks/axpby/environment/';
  for (const file of ['docker-compose.yaml', 'candidate-worker-seccomp.json', 'evaluator-worker-seccomp.json']) {
    const target = path.join(root, envPath, file);
    if (fs.lstatSync(target).isSymbolicLink() || sha256(fs.readFileSync(target)) !== upstream.files[envPath + file]) throw Error('Upstream worker recipe identity mismatch.');
  }
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const override = path.join(directory, 'allocation-' + crypto.randomUUID() + '.json');
  const worker = { image: config.imageId, network_mode: 'none' };
  fs.writeFileSync(override, JSON.stringify({ services: {
    main: { ...worker, entrypoint: [], command: ['/usr/bin/sleep', 'infinity'], user: '10001:10001', cpus: '1.0', mem_limit: '1g', pids_limit: 128 },
    compiler: worker, evaluator: worker,
  } }), { mode: 0o600, flag: 'wx' });
  const args = ['compose', '-p', config.allocation, '-f', path.join(root, envPath, 'docker-compose.yaml'), '-f', override];
  try {
    await execute(config.docker || '/usr/bin/docker', [...args, 'up', '-d', '--wait', '--wait-timeout', '60'], undefined, { timeout: 90000 });
    const containers = {};
    for (const role of ['main', 'compiler', 'evaluator']) {
      const id = (await execute(config.docker || '/usr/bin/docker', [...args, 'ps', '-q', '--no-trunc', role], undefined, { timeout: 10000 })).trim();
      if (!/^[a-f0-9]{64}$/.test(id)) throw Error('Worker allocation did not return one immutable container ID.');
      containers[role] = id;
    }
    return { ...config, containers, composeFiles: [path.join(root, envPath, 'docker-compose.yaml'), override], override };
  } catch (error) {
    await execute(config.docker || '/usr/bin/docker', [...args, 'down', '--volumes'], undefined, { timeout: 30000 }).catch(() => {});
    throw error;
  }
}
if (require.main === module) (async () => {
  const input = process.argv[2], output = process.argv[3];
  if (!path.isAbsolute(input || '') || !path.isAbsolute(output || '')) throw Error('Use absolute configuration input/output files.');
  const allocated = await allocate(JSON.parse(fs.readFileSync(input)));
  fs.writeFileSync(output, JSON.stringify(allocated, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
})().catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
module.exports = { allocate };
