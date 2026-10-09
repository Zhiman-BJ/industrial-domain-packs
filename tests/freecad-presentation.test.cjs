const test = require('node:test');
const assert = require('node:assert/strict');
const { presentation } = require('../packs/freecad/runtime/presentation.cjs');
const names = ['model.FCStd', 'model.step', 'model.stl', 'model.brep', 'model.cad-preview.json', 'model.cad-sketches.json', 'model.recipe.json', 'readback.json', 'build.json', 'source'];
const artifacts = names.map(localId => ({ localId }));
test('producer declares model, version-bound companions and independent diagnostic report', () => {
  const result = presentation({ operation: 'build', artifacts, succeeded: true });
  assert.equal(result.groups[0].primary, 'model.FCStd');
  assert.equal(result.groups[0].preview, 'model.FCStd');
  assert.deepEqual(result.groups[0].companions, ['model.brep', 'model.cad-preview.json', 'model.cad-sketches.json', 'model.recipe.json']);
  assert.ok(result.groups[0].attachments.includes('model.step'));
  assert.equal(result.groups[1].primary, 'readback.json');
  assert.equal(result.groups[1].preview, undefined);
  assert.equal(result.groups[0].supersedesInput, undefined);
});
test('only successful edits replace the exact input; inspect references that input without replacing it', () => {
  const input = { relativePath: 'cad-output/previous/model.FCStd', sha256: 'a'.repeat(64) };
  const options = { artifacts, sourcePath: input.relativePath, sourceHash: input.sha256, succeeded: true };
  assert.deepEqual(presentation({ ...options, operation: 'edit' }).groups[0].supersedesInput, input);
  const inspection = presentation({ ...options, operation: 'inspect' });
  assert.equal(inspection.groups.length, 1);
  assert.deepEqual(inspection.checks[0].input, input);
  assert.deepEqual(inspection.inputs, [{ output: 'source', relativePath: input.relativePath }]);
  const failure = presentation({ ...options, operation: 'edit', succeeded: false });
  assert.equal(failure.groups[0].supersedesInput, undefined);
  assert.equal(failure.groups[0].preview, undefined);
});
test('failure/cancellation declares only actual outputs and never synthesizes a model', () => {
  const result = presentation({ operation: 'build', succeeded: false, artifacts: [{ localId: 'error.json' }, { localId: 'build.log' }] });
  assert.deepEqual(result.groups.map(group => group.primary), ['error.json']);
  assert.deepEqual(result.groups[0].attachments, ['build.log']);
  assert.deepEqual(presentation({ operation: 'build', artifacts: [], succeeded: false }).groups, []);
});
