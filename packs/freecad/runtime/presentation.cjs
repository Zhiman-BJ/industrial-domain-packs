// These roles are declared by the producer; consumers must never infer them
// from names, extensions, output order or modification time.
function presentation({ operation, artifacts, sourcePath, sourceHash, succeeded }) {
  const names = new Set(artifacts.map(output => output.localId));
  const existing = values => values.filter(name => names.has(name));
  const source = sourcePath && sourceHash ? { relativePath: sourcePath, sha256: sourceHash } : null;
  const groups = [];
  if (operation !== 'inspect' && names.has('model.FCStd')) groups.push({
    key: 'model', title: operation === 'edit' ? 'Modified model' : 'CAD model',
    primary: 'model.FCStd',
    // Preview needs the producer's version manifest and all its companions.
    ...(succeeded && names.has('model.cad-preview.json') ? { preview: 'model.FCStd' } : {}),
    attachments: existing(['model.step', 'model.stl', 'build.json', 'readback.json', 'error.json', operation + '.log', 'verify.log']),
    companions: existing(['model.brep', 'model.cad-preview.json', 'model.cad-sketches.json', 'model.recipe.json']),
    ...(operation === 'edit' && succeeded && source ? { supersedesInput: source } : {}),
  });
  // Diagnostics remain independently selectable without becoming the model's
  // primary entry or receiving a fabricated success status.
  const report = names.has('readback.json') ? 'readback.json' : names.has('error.json') ? 'error.json' : null;
  if (report) groups.push({
    key: 'report', title: operation === 'inspect' ? 'Geometry inspection' : 'CAD diagnostics',
    primary: report, attachments: existing(['build.json', operation + '.log', 'verify.log']), companions: [],
  });
  return {
    schemaVersion: '1', groups,
    inputs: source && names.has('source') ? [{ output: 'source', relativePath: sourcePath }] : [],
    checks: operation === 'inspect' && source ? [{ input: source, outputs: existing(['readback.json', 'error.json', 'inspect.log', 'verify.log']) }] : [],
  };
}
module.exports = { presentation };
