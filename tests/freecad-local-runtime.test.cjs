const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');

// Contract fixtures only: execute/result/verify are production code. The native
// process boundary emits reports and placeholder files, not real CAD acceptance.
const runtimeFile = path.resolve(__dirname, '../packs/freecad/runtime/index.cjs');
const runtimeRequire = createRequire(runtimeFile);
const shape = { valid: true, volume: 100, solids: 1, bounds: [10, 5, 2] };
const recipe = {
  parameters: { Height: 2 },
  features: [{ id: 'Box', op: 'box', length: 10, width: 5, height: 'Height' }],
  result: 'Box',
};
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function fixtureRuntime(onNativeStart = () => {}) {
  const calls = [];
  function spawn(command, args, options) {
    const request = JSON.parse(fs.readFileSync(path.join(options.cwd, 'request.json')));
    calls.push(request.operation);
    onNativeStart(request);
    const write = (name, data) => fs.writeFileSync(
      path.join(options.cwd, name), typeof data === 'string' ? data : JSON.stringify(data),
    );
    if (request.operation === 'verify') {
      write('readback.json', {
        native: shape, step: shape, brep: shape,
        mesh: { facets: 12 }, sketches: [{ fullyConstrained: true }],
      });
    } else {
      for (const name of ['model.FCStd', 'model.step', 'model.stl', 'model.brep'])
        write(name, 'CONTRACT FIXTURE ONLY');
      write('model.cad-sketches.json', []);
      write('build.json', { original: shape });
    }
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.pid = 2147483647;
    setImmediate(() => child.emit('close', 0, null));
    return child;
  }
  const module = { exports: {} };
  const context = {
    require: name => name === 'node:child_process' ? { spawn } : runtimeRequire(name),
    module, exports: module.exports, __dirname: path.dirname(runtimeFile),
    // Select the supported local adapter on every CI host; no native process runs.
    process: { platform: 'darwin', arch: 'arm64', env: {}, kill() {} },
    structuredClone, setTimeout, clearTimeout,
  };
  vm.runInNewContext(fs.readFileSync(runtimeFile, 'utf8'), context, { filename: runtimeFile });
  return { runtime: module.exports, calls };
}

async function executeFixture(t, operation, expect, onNativeStart) {
  const projectDir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'freecad-contract-')));
  t.after(() => fs.rmSync(projectDir, { recursive: true, force: true }));
  const source = path.join(projectDir, 'source.step');
  fs.writeFileSync(source, 'CONTRACT INPUT FIXTURE');
  fs.writeFileSync(path.join(projectDir, 'source.recipe.json'), JSON.stringify(recipe));
  fs.writeFileSync(path.join(projectDir, 'source.cad-preview.json'), JSON.stringify({
    schemaVersion: 1,
    hashes: {
      'source.step': digest(fs.readFileSync(source)),
      'source.recipe.json': digest(fs.readFileSync(path.join(projectDir, 'source.recipe.json'))),
    },
  }));
  const inputs = operation === 'build'
    ? { recipe: structuredClone(recipe) }
    : { file: 'source.step', ...(operation === 'edit' ? { changes: { parameters: { Height: 2 } } } : {}) };
  if (expect !== undefined) inputs.expect = expect;
  const { runtime, calls } = fixtureRuntime(request => onNativeStart?.(inputs, request));
  const plugin = runtime.createRuntimePlugin({
    environment: { INDUSTRIAL_HARNESS_FREECAD_CMD: process.execPath },
  });
  assert.equal(plugin.available, true);
  const tool = plugin.tools.find(tool => tool.descriptor.id === 'cad.freecad.' + operation);
  const action = { id: 'contract', inputs };
  const result = await tool.execute({ projectDir, inputs, action, signal: new AbortController().signal });
  assert.equal(result.executionSucceeded, true);
  assert.deepEqual(calls, [operation, 'verify']);
  const verification = plugin.verifiers['cad.freecad.geometry']({
    result, artifacts: result.artifacts, action,
    readArtifact: artifact => fs.readFileSync(path.join(projectDir, artifact.file), 'utf8'),
  });
  return { result, verification };
}

for (const operation of ['build', 'inspect', 'export', 'edit']) {
  for (const [label, expect, status, failedCheck] of [
    ['matching dimensions', { volume: 100, solids: 1, bounds: [10, 5, 2] }, 'passed'],
    ['wrong volume', { volume: 999 }, 'failed', 'expectedVolume'],
    ['wrong solid count', { solids: 2 }, 'failed', 'expectedSolids'],
    ['wrong bounds', { bounds: [99, 5, 2] }, 'failed', 'expectedBounds'],
    ['custom tolerance', { volume: 100.05, bounds: [10.05, 5, 2], tolerance: 0.1 }, 'passed'],
    ['default tolerance', { volume: 100.05 }, 'failed', 'expectedVolume'],
    ['no expectations', undefined, 'passed'],
  ]) {
    test(operation + ' execute → verify: ' + label, async t => {
      const { result, verification } = await executeFixture(t, operation, expect);
      assert.equal(verification.status, status);
      assert.deepEqual(result.expect, expect);
      if (failedCheck) assert.equal(verification.metrics[failedCheck], false);
      if (expect?.bounds) assert.equal(verification.metrics.expectedBounds, status === 'passed');
      if (expect?.solids) assert.equal(verification.metrics.expectedSolids, expect.solids === shape.solids);
      if (expect === undefined) {
        assert.equal(Object.hasOwn(verification.metrics, 'expectedVolume'), false);
        assert.match(verification.reason, /No design-specific dimensional expectations/);
      }
    });
  }
  test(operation + ' snapshots validated expectations before native execution', async t => {
    const expect = { volume: 999, solids: 2, bounds: [99, 99, 99], tolerance: 0.001 };
    const original = structuredClone(expect);
    const { result, verification } = await executeFixture(t, operation, expect, inputs => {
      inputs.expect.volume = 100;
      inputs.expect.solids = 1;
      inputs.expect.bounds[0] = 10;
      inputs.expect.bounds[1] = 5;
      inputs.expect.bounds[2] = 2;
      inputs.expect.tolerance = 1000;
    });
    assert.deepEqual(result.expect, original);
    assert.notEqual(result.expect, expect);
    assert.notEqual(result.expect.bounds, expect.bounds);
    assert.equal(verification.status, 'failed');
    for (const check of ['expectedVolume', 'expectedSolids', 'expectedBounds'])
      assert.equal(verification.metrics[check], false);
  });
}
