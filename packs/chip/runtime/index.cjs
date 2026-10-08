const { descriptor, verifier } = require('./verifier.cjs');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

function runtimeEnvironment(environment) {
  // Engineering subprocesses do not need model/session credentials. Future
  // tool-specific secrets must be declared explicitly rather than inherited.
  return Object.fromEntries(
    Object.entries(environment).filter(
      ([name]) => !/credential|api.?key|token|secret|password|authorization/i.test(name),
    ),
  );
}

function createRuntimePlugin({ environment = process.env } = {}) {
  const python =
    environment.INDUSTRIAL_HARNESS_EDA_PYTHON ||
    path.join(__dirname, '../eda-harness/.venv/bin/python');
  const source = path.join(__dirname, '../eda-harness/src');
  function call(operation, projectDir, signal) {
    return new Promise((resolve, reject) => {
      const child = spawn(python, [path.join(__dirname, 'bridge.py')], {
        cwd: projectDir,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: {
          ...runtimeEnvironment(environment),
          PYTHONPATH: source,
          PYTHONNOUSERSITE: '1',
          PYTHONUNBUFFERED: '1',
          PYTHONDONTWRITEBYTECODE: '1',
        },
      });
      let stdout = '',
        stderr = '';
      child.stdout.on('data', value => {
        stdout += value;
        if (stdout.length > 4 * 1024 * 1024) child.kill('SIGTERM');
      });
      child.stderr.on('data', value => {
        stderr = (stderr + value).slice(-65536);
      });
      child.on('error', reject);
      let closed = false,
        cancelTimer;
      const cancel = () => {
        if (closed) return;
        // Cancellation may arrive while the native runtime is still creating
        // its Run. Retry serially until its own polling/cleanup path observes it.
        void call('cancel', projectDir)
          .catch(error => {
            stderr += `\nCancellation: ${error.message}`;
          })
          .finally(() => {
            if (!closed) cancelTimer = setTimeout(cancel, 250);
          });
      };
      signal?.addEventListener('abort', cancel, { once: true });
      child.on('close', code => {
        closed = true;
        clearTimeout(cancelTimer);
        signal?.removeEventListener('abort', cancel);
        if (code !== 0) return reject(Error(`EDA runtime failed (${code}): ${stderr}`));
        try {
          resolve(JSON.parse(stdout));
        } catch (error) {
          reject(Error(`Invalid EDA runtime response: ${error.message}`));
        }
      });
      child.stdin.end(JSON.stringify({ operation, projectDir }));
    });
  }
  return {
    matchesProject: projectDir => fs.existsSync(path.join(projectDir, 'eda.yaml')),
    workspaceProtectedPaths: projectDir => [path.join(projectDir, '.eda')],
    stateProvider: ({ projectDir }) => call('inspect', projectDir),
    tools: [
      {
        descriptor,
        execute: ({ projectDir, inputs, signal }) => {
          if (Object.keys(inputs).length)
            throw Error(
              'RTL verification uses the declared eda.yaml inputs; this tool accepts no command or script overrides.',
            );
          return call('execute', projectDir, signal);
        },
      },
      {
        descriptor: {
          schemaVersion: '1',
          id: 'chip.environment.check',
          version: '0.6.1-core.2',
          risk: 'read-only',
          verification: [],
        },
        execute: ({ projectDir, inputs }) => {
          if (Object.keys(inputs).length)
            throw Error(
              'Host environment checks use the bound eda.yaml; no command or context overrides are accepted.',
            );
          return call('environment', projectDir);
        },
      },
    ],
    verifiers: {
      'chip.rtl.assertions': verifier,
    },
    capabilities: [
      {
        id: 'chip.rtl.core.verify',
        title: 'Persistent RTL assertion verification',
        domain: 'chip',
        stages: ['rtl'],
        priority: 100,
        keywords: ['simulate', 'simulation', 'verification', 'assertion', 'rtl verify'],
        skills: [],
        tools: [
          {
            id: 'chip.rtl.verify',
            summary: 'Run declared RTL simulation through the durable runtime.',
          },
          {
            id: 'chip.environment.check',
            summary:
              'Check the declared RTL tools and Docker on the host Runtime; sandbox Shell/MCP Docker access is intentionally denied. Read environment readiness from diagnostics, not engineering verification.',
          },
        ],
        verification: ['chip.rtl.assertions'],
      },
      {
        id: 'chip.environment.core.check',
        title: 'Host RTL environment preflight',
        domain: 'chip',
        stages: ['rtl'],
        priority: 110,
        keywords: ['environment', 'docker', 'preflight', '环境检查', '工具检查'],
        skills: [],
        tools: [
          {
            id: 'chip.environment.check',
            summary:
              'Read the host Runtime environment for declared RTL inputs; call with inputs: {}. This does not initialize a project or establish engineering acceptance.',
          },
        ],
        verification: [],
      },
    ],
    protectedPaths: [path.resolve(__dirname, '..')],
    available: fs.existsSync(python),
  };
}
module.exports = { createRuntimePlugin, runtimeEnvironment };
