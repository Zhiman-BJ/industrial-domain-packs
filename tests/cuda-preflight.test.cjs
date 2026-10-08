const test = require('node:test');
const assert = require('node:assert/strict');
const { preflight, parseInventory, requireLocalGpu } = require('../packs/cuda/scripts/preflight.cjs');
const { allocate } = require('../packs/cuda/scripts/allocate.cjs');
const { imageLock } = require('../packs/cuda/runtime/protocol.cjs');
const uuid = 'GPU-11111111-2222-3333-4444-555555555555';
const row = ({ name = 'NVIDIA GeForce RTX 4090', id = uuid, driver = '595.71.05', sm = '8.9' } = {}) => `${name}, ${id}, ${driver}, ${sm}, 24564, 16, 0\n`;
const host = output => ({ platform: 'linux', architecture: 'x64', run: async () => output });

test('remote-client installation requires no local GPU and unsupported hosts do not start inspection', async () => {
  const run = async () => { throw Error('GPU inspection must not run.'); };
  assert.equal((await preflight({ mode: 'remote-client', platform: 'darwin', architecture: 'arm64', run })).status, 'not_required');
  assert.equal((await preflight({ platform: 'win32', architecture: 'x64', run })).status, 'unsupported');
});
test('local GPU inventory preserves capacity and stable selection without claiming native qualification', async () => {
  const result = await preflight(host(row()));
  assert.equal(result.status, 'ready'); assert.equal(result.selectedGpu.uuid, uuid);
  assert.equal(result.selectedGpu.memoryTotalMiB, 24564); assert.equal(result.nativeQualification, 'not_run');
  const second = 'GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  assert.equal((await preflight(host(row() + row({ id: second })))).status, 'select_gpu');
  assert.equal((await preflight({ ...host(row() + row({ id: second })), gpuUuid: second })).selectedGpu.uuid, second);
  assert.equal((await preflight({ ...host(row()), gpuUuid: second })).status, 'unsupported');
});
test('hardware detection rejects unqualified model, driver and compute capability', async () => {
  for (const fields of [{ name: 'NVIDIA GeForce RTX 5090' }, { driver: '575.57.08' }, { sm: '9.0' }]) {
    const result = await preflight(host(row(fields))); assert.equal(result.status, 'unsupported');
    assert.equal(result.gpus[0].profileCompatible, false);
  }
  assert.equal((await preflight(host(''))).reasons[0], 'no_nvidia_gpu');
  assert.equal((await preflight({ ...host(''), run: async () => { throw Error('driver unavailable'); } })).status, 'unavailable');
});
test('incomplete or conflicting GPU information cannot admit installation', async () => {
  assert.throws(() => parseInventory(row() + row()));
  assert.throws(() => parseInventory(row().replace('24564', 'N/A')));
  assert.throws(() => parseInventory(row().replace(', 0\n', ', 101\n')));
  await assert.rejects(requireLocalGpu({ gpuUuid: uuid }, host(row({ driver: '575.57.08' }))), /GPU check failed/);
});
test('allocation GPU check occurs before recipe reads, filesystem writes or native worker creation', async () => {
  let checked = false;
  await assert.rejects(allocate({ upstreamRoot: '/missing/upstream', stateDirectory: '/missing/state', imageId: imageLock.imageId, allocation: 'preflight-test', projectId: 'a'.repeat(64), gpuUuid: uuid }, {
    checkGpu: async config => { checked = config.gpuUuid === uuid; throw Error('unsupported GPU'); },
  }), /unsupported GPU/);
  assert.equal(checked, true);
});
