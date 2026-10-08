// Backend provisioning for one administrator-bound AXPBY allocation. This is
// never invoked from project inputs or an agent-exposed MCP tool.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execute } = require('../server/backend.cjs');
const { requireLocalGpu } = require('./preflight.cjs');
const { upstream, imageLock, sha256 } = require('../runtime/protocol.cjs');
function bindEvaluatorGpu(recipe, gpuUuid) {
  const evaluator = recipe.services?.evaluator;
  if (!evaluator) throw Error('Locked allocation recipe has no Evaluator.');
  evaluator.environment = { ...evaluator.environment, NVIDIA_VISIBLE_DEVICES: gpuUuid };
  evaluator.deploy ||= {}; evaluator.deploy.resources ||= {}; evaluator.deploy.resources.reservations ||= {};
  evaluator.deploy.resources.reservations.devices = [{ driver: 'nvidia', device_ids: [gpuUuid], capabilities: ['gpu'] }];
  return recipe;
}
async function allocate(config, { checkGpu = requireLocalGpu } = {}) {
  if (!path.isAbsolute(config.upstreamRoot || '') || !path.isAbsolute(config.stateDirectory || '') ||
      config.imageId !== imageLock.imageId ||
      !/^[a-z0-9][a-z0-9-]{1,62}$/.test(config.allocation || '') ||
      !/^[a-f0-9]{64}$/.test(config.projectId || '')) throw Error('Invalid administrator allocation configuration.');
  await checkGpu(config);
  const root = fs.realpathSync(config.upstreamRoot), directory = path.dirname(config.stateDirectory);
  const envPath = 'tasks/axpby/environment/';
  const composeProjectDirectory = path.join(root, envPath);
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
  const compose = ['compose', '--project-directory', composeProjectDirectory, '-p', config.allocation];
  let args = [...compose, '-f', path.join(composeProjectDirectory, 'docker-compose.yaml'), '-f', override];
  try {
    // Resolve the reviewed recipe before replacing its GPU request. Writing one
    // complete Compose file avoids appending a second device through merge rules.
    const recipe = bindEvaluatorGpu(JSON.parse(await execute(config.docker || '/usr/bin/docker', [...args, 'config', '--format', 'json'], undefined, { timeout: 10000, limit: 256 * 1024 })), config.gpuUuid);
    fs.writeFileSync(override, JSON.stringify(recipe), { mode: 0o600 });
    args = [...compose, '-f', override];
    await execute(config.docker || '/usr/bin/docker', [...args, 'up', '-d', '--wait', '--wait-timeout', '60'], undefined, { timeout: 90000 });
    const containers = {};
    for (const role of ['main', 'compiler', 'evaluator']) {
      const id = (await execute(config.docker || '/usr/bin/docker', [...args, 'ps', '-q', '--no-trunc', role], undefined, { timeout: 10000 })).trim();
      if (!/^[a-f0-9]{64}$/.test(id)) throw Error('Worker allocation did not return one immutable container ID.');
      containers[role] = id;
    }
    return { ...config, containers, composeFiles: [override], composeProjectDirectory, reviewedRecipeSha256: upstream.files[envPath + 'docker-compose.yaml'], override };
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
module.exports = { allocate, bindEvaluatorGpu };
