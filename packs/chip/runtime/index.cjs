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
    stateProvider: ({ projectDir }) => call('inspect', projectDir),
    tools: [
      {
        descriptor: {
          schemaVersion: '1',
          id: 'chip.rtl.verify',
          version: '0.6.1-core.1',
          risk: 'mutating',
          verification: ['chip.rtl.assertions'],
        },
        execute: ({ projectDir, inputs, signal }) => {
          if (Object.keys(inputs).length)
            throw Error(
              'RTL verification uses the declared eda.yaml inputs; this tool accepts no command or script overrides.',
            );
          return call('execute', projectDir, signal);
        },
      },
    ],
    verifiers: {
      'chip.rtl.assertions': ({ result, artifacts, readArtifact }) => {
        const logs = artifacts
          .filter(item => item.kind === 'log.tool')
          .map(item => readArtifact(item).toString('utf8'))
          .join('\n');
        const report = artifacts.find(item => item.kind === 'report.simulation');
        const wave = artifacts.find(item => item.kind === 'waveform.vcd');
        const assertionFailure = /%(?:Error|Fatal).*Assertion failed/i.test(logs);
        if (assertionFailure)
          return {
            status: 'failed',
            reason: 'Real Verilator assertion failed; inspect collected tool logs.',
            metrics: { assertionFailure: true },
          };
        if (!report || !wave || !result.nativeVerification)
          return {
            status: 'insufficient_evidence',
            reason: 'Simulation completion report, VCD or native verifier evidence is missing.',
            metrics: {},
          };
        const complete = readArtifact(report).toString('utf8').includes('EDA_SIMULATION_FINISHED');
        const vcd = readArtifact(wave).toString('utf8');
        const genuineWave =
          /\$enddefinitions\s+\$end/.test(vcd) && /#\d+/.test(vcd) && /\$var/.test(vcd);
        const passed =
          complete &&
          genuineWave &&
          result.nativeVerification.status === 'PASS' &&
          !/%Error/.test(logs);
        return {
          status: passed ? 'passed' : 'failed',
          reason: passed
            ? 'Assertions enabled; testbench reached $finish and produced a real VCD. Functional coverage remains testbench-defined.'
            : 'Simulation evidence failed the assertion/completion/VCD checks.',
          metrics: { completed: complete, waveformPresent: genuineWave, assertionFailure: false },
        };
      },
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
        ],
        verification: ['chip.rtl.assertions'],
      },
    ],
    protectedPaths: [path.resolve(__dirname, '..')],
    available: fs.existsSync(python),
  };
}
module.exports = { createRuntimePlugin, runtimeEnvironment };
