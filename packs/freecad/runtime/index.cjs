const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { validateRecipe, validateInputs, applyChanges, guides } = require('./recipe.cjs');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const VERSION = '1.1.4-pack.3';

function executable(environment, managed) {
  const candidates = [
    environment.INDUSTRIAL_HARNESS_FREECAD_CMD,
    ...(!managed
      ? [
          '/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd',
          path.join(os.homedir(), 'Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd'),
        ]
      : []),
  ].filter(Boolean);
  return candidates.find(file => fs.existsSync(file));
}
function inputFile(projectDir, relative) {
  if (
    path.isAbsolute(relative) ||
    relative.split(/[\\/]/).includes('..') ||
    !/\.(fcstd|step|stp)$/i.test(relative)
  )
    throw Error('CAD input must be a project-relative FCStd/STEP file.');
  const file = fs.realpathSync(path.join(projectDir, relative));
  const rel = path.relative(projectDir, file);
  if (
    !rel ||
    rel.startsWith('..' + path.sep) ||
    path.isAbsolute(rel) ||
    !fs.statSync(file).isFile() ||
    fs.statSync(file).size > 16 * 1024 * 1024
  )
    throw Error('CAD input escaped the project or exceeded 16 MiB.');
  return file;
}
function inspectInputs(projectDir) {
  const inputHashes = {};
  let size = 0;
  let entries = 0;
  function visit(directory, depth = 0) {
    if (depth > 12) throw Error('CAD project depth limit exceeded.');
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (++entries > 10000) throw Error('CAD project entry limit exceeded.');
      if (entry.name.startsWith('.') || ['node_modules', 'cad-output'].includes(entry.name))
        continue;
      const file = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) {
        if (/\.(fcstd|step|stp)$/i.test(file)) throw Error('CAD sources cannot be symlinks.');
        continue;
      }
      if (entry.isDirectory()) visit(file, depth + 1);
      else if (/\.(fcstd|step|stp)$/i.test(file)) {
        size += fs.statSync(file).size;
        if (Object.keys(inputHashes).length >= 100 || size > 64 * 1024 * 1024)
          throw Error('CAD source inventory limit exceeded.');
        inputHashes[path.relative(projectDir, file).split(path.sep).join('/')] = hash(
          fs.readFileSync(file),
        );
      }
    }
  }
  visit(projectDir);
  return { stage: null, inputHashes };
}
function recipeCompanion(source, projectDir, required) {
  const file = source.replace(/\.[^.]+$/, '.recipe.json');
  const manifestFile = source.replace(/\.[^.]+$/, '.cad-preview.json');
  if (!fs.existsSync(file) || !fs.existsSync(manifestFile)) {
    if (required)
      throw Error(
        'This model has no editable recipe. Rebuild from a project recipe first; imported arbitrary CAD is not parametrically editable.',
      );
    return null;
  }
  const read = target => {
    const real = fs.realpathSync(target),
      relative = path.relative(projectDir, real);
    if (
      relative.startsWith('..') ||
      path.isAbsolute(relative) ||
      !fs.statSync(real).isFile() ||
      fs.statSync(real).size > 256 * 1024
    )
      throw Error('CAD recipe companion escaped the project or exceeded 256 KiB.');
    return fs.readFileSync(real);
  };
  const manifest = JSON.parse(read(manifestFile));
  const bytes = read(file);
  if (
    manifest.schemaVersion !== 1 ||
    manifest.hashes?.[path.basename(source)] !== hash(fs.readFileSync(source)) ||
    manifest.hashes?.[path.basename(file)] !== hash(bytes)
  )
    throw Error('CAD editable recipe hashes changed; regenerate the model.');
  const recipe = JSON.parse(bytes);
  validateRecipe(recipe);
  return { recipe, unchanged: () => hash(read(file)) === hash(bytes) };
}
function nativeCall(command, directory, operation, request, signal) {
  fs.writeFileSync(path.join(directory, 'request.json'), JSON.stringify({ ...request, operation }));
  const script = path.join(directory, 'native.py');
  fs.copyFileSync(path.join(__dirname, 'native.py'), script);
  const home = path.join(directory, 'home');
  const data = path.join(home, 'data');
  const temporary = path.join(directory, 'temp');
  // FreeCAD's Qt paths on macOS do not follow HOME alone. These directories
  // must exist before startup or FreeCAD discards its custom-path overrides.
  for (const location of [home, data, temporary]) fs.mkdirSync(location, { recursive: true });
  const profile = path.join(directory, 'native.sb');
  // The fixed bridge can write only its new Action directory; project inputs,
  // runtime evidence and unrelated user files remain protected from native code.
  fs.writeFileSync(
    profile,
    `(version 1)\n(allow default)\n(deny network*)\n(deny appleevent-send)\n(deny file-write*)\n(allow file-write* (subpath ${JSON.stringify(directory)}) (literal "/dev/null"))\n`,
  );
  return new Promise((resolve, reject) => {
    // FreeCAD 1.1 otherwise creates the Qt global versioned cache before applying
    // FREECAD_USER_TEMP. This official option keeps that initialization under HOME.
    const child = spawn(
      '/usr/bin/sandbox-exec',
      ['-f', profile, command, '--keep-deprecated-paths', script],
      {
        cwd: directory,
        detached: true,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: {
          PATH: '/usr/bin:/bin',
          HOME: home,
          TMPDIR: temporary + '/',
          FREECAD_USER_HOME: home,
          FREECAD_USER_DATA: data,
          FREECAD_USER_TEMP: temporary,
          PYTHONNOUSERSITE: '1',
          PYTHONDONTWRITEBYTECODE: '1',
          QT_QPA_PLATFORM: 'offscreen',
        },
      },
    );
    let log = '',
      cancelled = false,
      overflow = false,
      escalation;
    const kill = signalName => {
      try {
        process.kill(-child.pid, signalName);
      } catch {}
    };
    const stop = () => {
      kill('SIGTERM');
      escalation ||= setTimeout(() => kill('SIGKILL'), 1000);
    };
    const cancel = () => {
      cancelled = true;
      stop();
    };
    const timer = setTimeout(cancel, 90000);
    signal?.addEventListener('abort', cancel, { once: true });
    if (signal?.aborted) cancel();
    const receive = bytes => {
      log += bytes;
      if (log.length > 1024 * 1024) {
        overflow = true;
        log = log.slice(0, 1024 * 1024);
        stop();
      }
    };
    child.stdout.on('data', receive);
    child.stderr.on('data', receive);
    child.on('error', error => {
      clearTimeout(timer);
      clearTimeout(escalation);
      signal?.removeEventListener('abort', cancel);
      reject(error);
    });
    child.on('close', (code, exitSignal) => {
      clearTimeout(timer);
      clearTimeout(escalation);
      signal?.removeEventListener('abort', cancel);
      kill('SIGKILL');
      const termination = `Native process exit code=${code}, signal=${exitSignal || 'none'}, cancelled=${cancelled}, overflow=${overflow}.`;
      fs.writeFileSync(path.join(directory, `${operation}.log`), log + '\n' + termination);
      resolve({
        code,
        exitSignal,
        cancelled,
        overflow,
        diagnostic: termination + ' ' + log.slice(-2000),
      });
    });
  });
}
function verify({ result, artifacts, readArtifact, action }) {
  const report = artifacts.find(a => a.kind === 'report.cad.readback');
  if (!report || !result.executionSucceeded)
    return {
      status: 'insufficient_evidence',
      reason: 'FreeCAD execution/readback did not complete; inspect the recorded logs.',
      metrics: {},
    };
  const data = JSON.parse(readArtifact(report));
  const originalArtifact = artifacts.find(a => a.kind === 'report.cad.build');
  if (!originalArtifact)
    return {
      status: 'insufficient_evidence',
      reason: 'Original shape report is missing.',
      metrics: {},
    };
  const original = JSON.parse(readArtifact(originalArtifact)).original;
  const tolerance = action.inputs.expect?.tolerance || 0.00001;
  const near = (a, b) => Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= tolerance;
  const checks = {
    nativeValid: data.native?.valid === true,
    stepValid: data.step?.valid === true,
    brepValid: data.brep?.valid === true,
    brepAgreement:
      near(data.native?.volume, data.brep?.volume) &&
      data.native?.solids === data.brep?.solids &&
      data.native?.bounds.every((v, i) => near(v, data.brep?.bounds[i])),
    positiveVolume: data.native?.volume > 0,
    solidPresent: data.native?.solids > 0,
    meshPresent: data.mesh?.facets > 0,
    sketchesConstrained: data.sketches?.every(s => s.fullyConstrained === true) === true,
    exportAgreement:
      near(data.native?.volume, data.step?.volume) &&
      near(original.volume, data.native?.volume) &&
      original.solids === data.native?.solids &&
      data.native.solids === data.step.solids &&
      data.native.bounds.every(
        (v, i) => near(v, data.step.bounds[i]) && near(v, original.bounds[i]),
      ),
    artifactsPresent: [
      'model.cad.fcstd',
      'model.cad.step',
      'model.cad.stl',
      'display.cad.brep',
    ].every(kind => artifacts.some(a => a.kind === kind)),
    inputUnchanged: result.inputUnchanged !== false,
  };
  const expected = action.inputs.expect || {};
  if (expected.volume !== undefined)
    checks.expectedVolume = near(data.native.volume, expected.volume);
  if (expected.solids !== undefined) checks.expectedSolids = data.native.solids === expected.solids;
  if (expected.bounds)
    checks.expectedBounds = expected.bounds.every((v, i) => near(data.native.bounds[i], v));
  const passed = Object.values(checks).every(Boolean);
  return {
    status: passed ? 'passed' : 'failed',
    reason: passed
      ? 'Separate FreeCAD readback confirms valid solids, constrained sketches and matching FCStd/STEP/BREP geometry. ' +
        (Object.keys(expected).length
          ? 'Supplied dimensional expectations passed. '
          : 'No design-specific dimensional expectations were supplied. ') +
        'This checks geometry, not mechanical strength or manufacturability.'
      : 'CAD geometry/readback checks failed: ' +
        Object.entries(checks)
          .filter(([, v]) => !v)
          .map(([k]) => k)
          .join(', '),
    metrics: {
      ...checks,
      volume: data.native.volume,
      solids: data.native.solids,
      boundsX: data.native.bounds[0],
      boundsY: data.native.bounds[1],
      boundsZ: data.native.bounds[2],
    },
  };
}
function createRuntimePlugin({ environment = process.env, managed = false } = {}) {
  const command = executable(environment, managed);
  const available = process.platform === 'darwin' && process.arch === 'arm64' && !!command;
  return {
    available,
    workspaceProtectedPaths: projectDir => [path.join(projectDir, 'cad-output')],
    stateProvider: ({ projectDir }) => inspectInputs(projectDir),
    protectedPaths: [path.resolve(__dirname, '..')],
    tools: ['build', 'inspect', 'export', 'edit'].map(operation => ({
      descriptor: {
        schemaVersion: '1',
        id: `cad.freecad.${operation}`,
        version: VERSION,
        risk: operation === 'inspect' ? 'read-only' : 'mutating',
        verification: ['cad.freecad.geometry'],
      },
      guide: guides[`cad.freecad.${operation}`],
      execute: async ({ projectDir, inputs, signal, action }) => {
        validateInputs(operation, inputs);
        if (!available)
          throw Error(
            managed
              ? 'FreeCAD needs preparation. Open Settings → Domains and repair CAD.'
              : 'Install FreeCAD 1.1.4 for macOS arm64 and set INDUSTRIAL_HARNESS_FREECAD_CMD to its freecadcmd executable.',
          );
        const outputRoot = path.join(projectDir, 'cad-output');
        if (
          fs.existsSync(outputRoot) &&
          (fs.lstatSync(outputRoot).isSymbolicLink() || !fs.statSync(outputRoot).isDirectory())
        )
          throw Error('CAD output root must be an ordinary project directory.');
        fs.mkdirSync(outputRoot, { recursive: true });
        const directory = path.join(outputRoot, action.id);
        fs.mkdirSync(directory, { mode: 0o700 });
        const source = operation === 'build' ? null : inputFile(projectDir, inputs.file);
        const sourceBytes = source ? fs.readFileSync(source) : null;
        const sourceHash = source ? hash(sourceBytes) : null;
        const companion = source ? recipeCompanion(source, projectDir, operation === 'edit') : null;
        const recipe =
          operation === 'build'
            ? inputs.recipe
            : operation === 'edit'
              ? applyChanges(companion.recipe, inputs.changes)
              : companion?.recipe;
        if (recipe)
          fs.writeFileSync(
            path.join(directory, 'model.recipe.json'),
            JSON.stringify(recipe, null, 2),
          );
        const stagedSource = source ? path.join(directory, 'input' + path.extname(source)) : null;
        if (source) fs.writeFileSync(stagedSource, sourceBytes);
        fs.writeFileSync(
          path.join(directory, 'inputs.json'),
          JSON.stringify({ ...inputs, sourceSha256: sourceHash }, null, 2),
        );
        const first = await nativeCall(
          command,
          directory,
          operation,
          operation === 'build' || operation === 'edit'
            ? { recipe: validateRecipe(recipe) }
            : { file: stagedSource },
          signal,
        );
        const second =
          first.code === 0 && !first.cancelled && !signal.aborted
            ? await nativeCall(command, directory, 'verify', {}, signal)
            : null;
        const succeeded =
          first.code === 0 &&
          second?.code === 0 &&
          !first.cancelled &&
          !second.cancelled &&
          !signal.aborted;
        if (succeeded) {
          const hashes = Object.fromEntries(
            [
              'model.FCStd',
              'model.step',
              'model.stl',
              'model.brep',
              'model.cad-sketches.json',
              ...(recipe ? ['model.recipe.json'] : []),
            ].map(name => [name, hash(fs.readFileSync(path.join(directory, name)))]),
          );
          fs.writeFileSync(
            path.join(directory, 'model.cad-preview.json'),
            JSON.stringify({
              schemaVersion: 1,
              hashes,
              readback: JSON.parse(fs.readFileSync(path.join(directory, 'readback.json'))),
            }),
          );
        }
        const kinds = {
          'model.FCStd': 'model.cad.fcstd',
          'model.step': 'model.cad.step',
          'model.stl': 'model.cad.stl',
          'model.brep': 'display.cad.brep',
          'model.cad-preview.json': 'display.cad.manifest',
          'model.cad-sketches.json': 'display.cad.sketches',
          'build.json': 'report.cad.build',
          'readback.json': 'report.cad.readback',
          'inputs.json': 'input.cad.recipe',
          'model.recipe.json': 'input.cad.recipe',
          'error.json': 'diagnostic.cad',
        };
        if (stagedSource) kinds[path.basename(stagedSource)] = 'input.cad.model';
        for (const name of [operation + '.log', 'verify.log']) kinds[name] = 'log.tool';
        return {
          executionSucceeded: succeeded,
          inputUnchanged:
            (!source || hash(fs.readFileSync(source)) === sourceHash) &&
            (!companion || companion.unchanged()),
          diagnostics: [
            succeeded
              ? 'FreeCAD build/export and separate readback completed.'
              : 'FreeCAD failed, timed out or was cancelled; no acceptance claim. ' +
                (second?.diagnostic || first.diagnostic),
          ],
          artifacts: Object.entries(kinds)
            .filter(([name]) => fs.existsSync(path.join(directory, name)))
            .map(([name, kind]) => ({
              file: path.relative(projectDir, path.join(directory, name)),
              kind,
            })),
        };
      },
    })),
    verifiers: { 'cad.freecad.geometry': verify },
  };
}
module.exports = { createRuntimePlugin, inspectInputs, inputFile, verify, guides };
