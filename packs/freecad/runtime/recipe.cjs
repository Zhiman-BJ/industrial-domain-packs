const NAME = /^[A-Za-z][A-Za-z0-9_]{0,39}$/;
const OPS = {
  box: ['length', 'width', 'height'],
  cylinder: ['radius', 'height'],
  sketch_pad: ['profile', 'length', 'width', 'radius', 'height'],
  cut: ['base', 'tool'],
  fuse: ['base', 'tool'],
  common: ['base', 'tool'],
  hole: ['base', 'radius', 'height'],
};
function keys(value, allowed) {
  if (
    !value ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    Object.keys(value).some(k => !allowed.includes(k))
  )
    throw Error('Unexpected or missing CAD fields.');
}
function validateRecipe(recipe) {
  keys(recipe, ['parameters', 'features', 'result']);
  keys(recipe.parameters || {}, Object.keys(recipe.parameters || {}));
  const parameters = recipe.parameters || {};
  if (Object.keys(parameters).length > 100) throw Error('CAD parameter limit exceeded.');
  for (const [name, value] of Object.entries(parameters))
    if (!NAME.test(name) || !Number.isFinite(value) || Math.abs(value) > 10000)
      throw Error('CAD parameters must be named finite dimensions in millimetres.');
  const dimension = value => {
    const resolved = typeof value === 'string' ? parameters[value] : value;
    if (!Number.isFinite(resolved) || resolved <= 0 || resolved > 10000)
      throw Error(
        'CAD dimensions must be positive, at most 10000 mm; parameter references must exist.',
      );
    return resolved;
  };
  if (!Array.isArray(recipe.features) || !recipe.features.length || recipe.features.length > 50)
    throw Error('CAD recipe needs 1–50 features.');
  const names = new Set();
  const features = recipe.features.map(feature => {
    if (!Object.hasOwn(OPS, feature?.op)) throw Error('Unsupported CAD operation.');
    keys(feature, ['id', 'op', 'origin', ...OPS[feature.op]]);
    if (
      !NAME.test(feature.id) ||
      ['Parameters', 'HarnessResult'].includes(feature.id) ||
      names.has(feature.id)
    )
      throw Error('CAD feature IDs must be unique safe names.');
    const result = { ...feature };
    if (
      feature.origin !== undefined &&
      (!Array.isArray(feature.origin) ||
        feature.origin.length !== 3 ||
        feature.origin.some(v => !Number.isFinite(v) || Math.abs(v) > 10000))
    )
      throw Error('CAD origins need three bounded numeric coordinates.');
    for (const key of ['base', 'tool'])
      if (OPS[feature.op].includes(key) && !names.has(feature[key]))
        throw Error('CAD operands must reference earlier features.');
    const dimensions =
      feature.op === 'sketch_pad'
        ? feature.profile === 'rectangle'
          ? ['length', 'width', 'height']
          : feature.profile === 'circle'
            ? ['radius', 'height']
            : null
        : OPS[feature.op].filter(k => !['base', 'tool'].includes(k));
    if (!dimensions) throw Error('Sketch profile must be rectangle or circle.');
    for (const key of dimensions) result[key] = dimension(feature[key]);
    names.add(feature.id);
    return result;
  });
  if (!names.has(recipe.result)) throw Error('CAD result must name a feature.');
  return { parameters, features, result: recipe.result };
}
function validateInputs(operation, inputs) {
  keys(inputs, operation === 'build' ? ['recipe', 'expect'] : ['file', 'expect']);
  if (operation === 'build') validateRecipe(inputs.recipe);
  else if (typeof inputs.file !== 'string' || !inputs.file || inputs.file.length > 1024)
    throw Error('CAD file must be a project-relative FCStd/STEP path.');
  if (inputs.expect) {
    keys(inputs.expect, ['volume', 'bounds', 'solids', 'tolerance']);
    for (const key of ['volume', 'tolerance'])
      if (
        inputs.expect[key] !== undefined &&
        (!Number.isFinite(inputs.expect[key]) || inputs.expect[key] <= 0)
      )
        throw Error('CAD expectations need positive finite numbers.');
    if (
      inputs.expect.solids !== undefined &&
      (!Number.isInteger(inputs.expect.solids) ||
        inputs.expect.solids < 1 ||
        inputs.expect.solids > 50)
    )
      throw Error('Invalid expected solid count.');
    if (
      inputs.expect.bounds &&
      (!Array.isArray(inputs.expect.bounds) ||
        inputs.expect.bounds.length !== 3 ||
        inputs.expect.bounds.some(v => !Number.isFinite(v) || v <= 0))
    )
      throw Error('Expected bounds need three positive dimensions.');
  }
}
const guides = Object.fromEntries(
  ['build', 'inspect', 'export'].map(operation => [
    `cad.freecad.${operation}`,
    {
      description:
        operation === 'build'
          ? 'Build a FreeCAD feature tree from a bounded parametric recipe; save FCStd, STEP and STL, then reopen in a separate process.'
          : 'Read a project FCStd/STEP, report geometry and export FCStd/STEP/STL through the persistent runtime. FCStd accepts only the supported native feature types, with no Python proxies or expressions.',
      inputs:
        operation === 'build'
          ? {
              recipe: {
                parameters: 'named mm dimensions, optional',
                features:
                  '1–50 ordered features, unique id; op=sketch_pad (rectangle: length,width,height or circle: radius,height), box(length,width,height), cylinder(radius,height), hole(base,radius,height), cut/fuse/common(base,tool); origin=[x,y,z] optional; dimensions may reference a parameter name',
                result: 'final feature id',
              },
              expect:
                'optional {volume: mm³, bounds: [xSize,ySize,zSize] mm, solids: integer, tolerance: absolute tolerance, default 0.00001}',
            }
          : {
              file: 'project-relative .FCStd/.step/.stp',
              expect: 'optional {volume,bounds,solids,tolerance}',
            },
      example:
        operation === 'build'
          ? {
              recipe: {
                parameters: { W: 20 },
                features: [
                  {
                    id: 'Plate',
                    op: 'sketch_pad',
                    profile: 'rectangle',
                    length: 40,
                    width: 'W',
                    height: 5,
                  },
                  {
                    id: 'Drilled',
                    op: 'hole',
                    base: 'Plate',
                    radius: 2,
                    height: 5,
                    origin: [10, 10, 0],
                  },
                ],
                result: 'Drilled',
              },
              expect: { solids: 1, bounds: [40, 20, 5] },
            }
          : { file: 'part.step', expect: { solids: 1 } },
    },
  ]),
);
module.exports = { validateRecipe, validateInputs, guides };
