const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { resourceDirectory } = require('../../../lib/resources.cjs');

function pcbRuntime(provider, environment = process.env) {
  const sourceDir = resourceDirectory(provider, environment);
  let packDir;
  for (let directory = __dirname; ; directory = path.dirname(directory)) {
    const candidate = path.join(directory, 'packs', provider.packDirectory);
    if (fs.existsSync(path.join(candidate, 'pyproject.toml'))) {
      packDir = candidate;
      break;
    }
    if (path.dirname(directory) === directory) break;
  }
  const python =
    environment[provider.pythonEnv] ||
    (packDir &&
      path.join(
        packDir,
        '.venv',
        process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
      ));
  if (
    !python ||
    !path.isAbsolute(python) ||
    !fs.statSync(python, { throwIfNoEntry: false })?.isFile()
  )
    throw Error(
      `${provider.title} needs its gateway Python environment. Run uv sync --frozen --no-dev in domain-packs/pcb or set ${provider.pythonEnv} to its absolute Python path.`,
    );
  const docker = environment.INDUSTRIAL_HARNESS_PCB_DOCKER || 'docker';
  if (
    docker !== 'docker' &&
    (!path.isAbsolute(docker) || !fs.statSync(docker, { throwIfNoEntry: false })?.isFile())
  )
    throw Error('INDUSTRIAL_HARNESS_PCB_DOCKER must be an absolute Docker executable.');
  const requirements = environment.INDUSTRIAL_HARNESS_PCB_REQUIREMENTS;
  if (
    requirements &&
    (!path.isAbsolute(requirements) ||
      !fs.statSync(requirements, { throwIfNoEntry: false })?.isFile())
  )
    throw Error(
      'INDUSTRIAL_HARNESS_PCB_REQUIREMENTS must name an absolute public requirements file.',
    );
  const requirementsPath = requirements ? fs.realpathSync(requirements) : null;
  const requirementsSha256 = requirementsPath
    ? crypto.createHash('sha256').update(fs.readFileSync(requirementsPath)).digest('hex')
    : null;
  const dockerEnvironment = Object.fromEntries(
    ['DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG']
      .filter(key => environment[key])
      .map(key => [key, environment[key]]),
  );
  const developmentImageId = environment.INDUSTRIAL_HARNESS_PCB_DEV_IMAGE_ID;
  if (developmentImageId && !/^sha256:[0-9a-f]{64}$/.test(developmentImageId))
    throw Error('INDUSTRIAL_HARNESS_PCB_DEV_IMAGE_ID must be an immutable Docker image ID.');
  return {
    python: path.normalize(python),
    sourceDir,
    docker,
    dockerEnvironment,
    imageId: developmentImageId || provider.imageId,
    requirementsPath,
    requirementsSha256,
  };
}

function pcbGatewayConfig(directory, provider, project, environment, options = {}) {
  const runtime = pcbRuntime(provider, environment);
  if (
    runtime.requirementsPath &&
    (runtime.requirementsPath === project ||
      runtime.requirementsPath.startsWith(project + path.sep))
  )
    throw Error('Public PCB requirements must be outside the writable candidate project.');
  for (const value of [project, runtime.sourceDir, directory, runtime.requirementsPath].filter(
    Boolean,
  ))
    if (value.includes(',')) throw Error('PCB Docker bind paths cannot contain commas.');
  const policyFile = path.join(directory, `mcp-${provider.id}.policy.json`);
  const policy = {
    schemaVersion: 1,
    ...provider,
    ...runtime,
    projectDir: project,
    imageInput: Boolean(options.imageInput),
    cacheDir: path.join(directory, `mcp-${provider.id}-results`),
    controller: path.join(__dirname, 'pcb-controller.py'),
  };
  fs.writeFileSync(policyFile, JSON.stringify(policy, null, 2), { mode: 0o600 });
  fs.chmodSync(policyFile, 0o600);
  return {
    command: runtime.python,
    args: [path.join(__dirname, 'pcb-gateway.py'), policyFile],
    env: { PYTHONNOUSERSITE: '1', PYTHONUNBUFFERED: '1', ...runtime.dockerEnvironment },
  };
}

module.exports = { pcbRuntime, pcbGatewayConfig };
