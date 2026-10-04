const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');

const MAX_SCENE_BYTES = 2 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 64 * 1024;
const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');

function boundProject(projectDir) {
  const project = fs.realpathSync(projectDir);
  const config = path.join(project, 'project.godot');
  if (!fs.statSync(project).isDirectory() || !fs.statSync(config, {throwIfNoEntry: false})?.isFile() || fs.realpathSync(config) !== config) throw Error('Bound Godot project is unavailable.');
  return project;
}

function sceneFile(project, scenePath) {
  if (typeof scenePath !== 'string' || !/^[a-zA-Z0-9_ .+()/-]+\.tscn$/.test(scenePath) || scenePath.startsWith('/') || scenePath.split('/').some(part => part === '.' || part === '..')) throw Error('Use a project-relative .tscn path.');
  const file = fs.realpathSync(path.join(project, scenePath));
  if (!file.startsWith(project + path.sep) || !fs.statSync(file).isFile() || fs.statSync(file).size > MAX_SCENE_BYTES) throw Error('Scene is outside the bound project or exceeds 2 MiB.');
  return file;
}

function projectStatus(projectDir, binaryVersion) {
  const project = boundProject(projectDir);
  const file = path.join(project, 'project.godot');
  if (fs.statSync(file).size > 256 * 1024) throw Error('project.godot exceeds the inspection limit.');
  const raw = fs.readFileSync(file);
  const text = raw.toString('utf8');
  const config = text.match(/^config\/name\s*=\s*(.+)$/m)?.[1] || null;
  const mainScene = text.match(/^run\/main_scene\s*=\s*(.+)$/m)?.[1] || null;
  return {projectDir: project, godotVersion: binaryVersion, projectSha256: sha256(raw), configName: config, mainScene, observation: 'project.godot source only', verificationStatus: 'not_run'};
}

function inspectSceneSource(projectDir, scenePath) {
  const project = boundProject(projectDir);
  const file = sceneFile(project, scenePath);
  const raw = fs.readFileSync(file);
  const sections = [];
  let current;
  let truncated = false;
  for (const [index, line] of raw.toString('utf8').split(/\r?\n/).entries()) {
    if (line.startsWith('[')) {
      if (sections.length >= 300) {truncated = true; break;}
      current = {line: index + 1, header: line.slice(0, 400), properties: {}};
      sections.push(current);
    } else if (current && line.includes(' = ')) {
      if (Object.keys(current.properties).length >= 60) {truncated = true; continue;}
      const split = line.indexOf(' = ');
      const key = line.slice(0, split);
      if (key.length <= 100) current.properties[key] = line.slice(split + 3, split + 403);
    }
  }
  return {scenePath, sha256: sha256(raw), sections, truncated, observation: 'serialized source; inherited/runtime values are not resolved', verificationStatus: 'not_run'};
}

function runProcess(binary, project, args, timeoutMs) {
  return new Promise(resolve => {
    const startedAt = new Date().toISOString();
    const env = Object.fromEntries(['PATH', 'HOME', 'TMPDIR', 'SYSTEMROOT', 'WINDIR'].filter(key => process.env[key]).map(key => [key, process.env[key]]));
    const child = spawn(binary, args, {cwd: project, env, stdio: ['ignore', 'pipe', 'pipe'], detached: process.platform !== 'win32'});
    let stdout = Buffer.alloc(0), stderr = Buffer.alloc(0), truncated = false, timedOut = false, spawnError = null;
    const append = (target, chunk) => {
      if (target.length >= MAX_OUTPUT_BYTES) {truncated = true; return target;}
      if (target.length + chunk.length > MAX_OUTPUT_BYTES) truncated = true;
      return Buffer.concat([target, chunk.subarray(0, MAX_OUTPUT_BYTES - target.length)]);
    };
    child.stdout.on('data', chunk => {stdout = append(stdout, chunk);});
    child.stderr.on('data', chunk => {stderr = append(stderr, chunk);});
    child.on('error', error => {spawnError = error.message;});
    const timer = setTimeout(() => {
      timedOut = true;
      try {if (process.platform === 'win32') child.kill('SIGKILL'); else process.kill(-child.pid, 'SIGKILL');} catch {child.kill('SIGKILL');}
    }, timeoutMs);
    child.on('close', (code, signal) => {
      clearTimeout(timer);
      resolve({startedAt, endedAt: new Date().toISOString(), exitCode: code, signal, timedOut, spawnError,
        stdout: stdout.toString('utf8'), stderr: stderr.toString('utf8'), outputTruncated: truncated});
    });
  });
}

function receipt(receiptDir, kind, project, inputs, processResult, detail = {}) {
  fs.mkdirSync(receiptDir, {recursive: true, mode: 0o700});
  fs.chmodSync(receiptDir, 0o700);
  const receiptId = crypto.randomUUID();
  const result = {receiptId, kind, projectDir: project, inputs, ...processResult, ...detail,
    outputSha256: sha256(processResult.stdout + '\n' + processResult.stderr), artifactSet: null,
    artifactCoverage: 'not_enumerated', sideEffects: 'Project scripts or Godot import may write files; artifact coverage is not established.'};
  fs.writeFileSync(path.join(receiptDir, receiptId + '.json'), JSON.stringify(result, null, 2), {flag: 'wx', mode: 0o600});
  return result;
}

async function inspectSceneRuntime(projectDir, binary, inspectorScript, scenePath, receiptDir) {
  const project = boundProject(projectDir);
  sceneFile(project, scenePath);
  const run = await runProcess(binary, project, ['--headless', '--path', project, '--script', inspectorScript, '--', scenePath], 30000);
  const line = run.stdout.split(/\r?\n/).find(value => value.startsWith('HARNESS_SCENE_JSON:'));
  let observation = null;
  try {if (line) observation = JSON.parse(line.slice('HARNESS_SCENE_JSON:'.length));} catch {}
  const diagnostics = errorDiagnostics(run);
  return receipt(receiptDir, 'inspect_scene_runtime', project, {scenePath}, run, {observation, diagnostics,
    verificationStatus: 'not_run', executionStatus: !run.timedOut && run.exitCode === 0 && observation?.ok && diagnostics.length === 0 ? 'completed' : 'failed'});
}

function errorDiagnostics(run) {
  return (run.stdout + '\n' + run.stderr).split(/\r?\n/).filter(line => /(?:SCRIPT ERROR|ERROR:|Parse Error)/i.test(line)).slice(0, 30);
}

async function checkProject(projectDir, binary, receiptDir) {
  const project = boundProject(projectDir);
  const run = await runProcess(binary, project, ['--headless', '--path', project, '--import', '--quit'], 60000);
  const diagnostics = errorDiagnostics(run);
  return receipt(receiptDir, 'check_project', project, {}, run, {diagnostics,
    importStatus: !run.timedOut && run.exitCode === 0 && diagnostics.length === 0 ? 'PASS' : 'FAIL',
    engineeringAcceptance: 'not_run'});
}

async function runScene(projectDir, binary, scenePath, frames, receiptDir) {
  const project = boundProject(projectDir);
  sceneFile(project, scenePath);
  if (!Number.isInteger(frames) || frames < 1 || frames > 180) throw Error('frames must be an integer from 1 to 180.');
  const run = await runProcess(binary, project, ['--headless', '--path', project, '--quit-after', String(frames), 'res://' + scenePath], 30000);
  const diagnostics = errorDiagnostics(run);
  return receipt(receiptDir, 'run_scene', project, {scenePath, frames}, run, {diagnostics,
    executionStatus: !run.timedOut && run.exitCode === 0 && diagnostics.length === 0 ? 'completed' : 'failed', engineeringAcceptance: 'not_run'});
}

module.exports = {projectStatus, inspectSceneSource, inspectSceneRuntime, checkProject, runScene, boundProject, sceneFile};
