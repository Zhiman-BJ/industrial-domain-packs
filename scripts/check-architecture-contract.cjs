#!/usr/bin/env node
// Maintained in industrial-agent-harness; satellite copies are pinned by policy.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const revision = 'IH-ARCH-001';
const roles = new Set(['harness', 'domain-packs', 'remote-service']);
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const skipped = new Set([
  '.git',
  'node_modules',
  'dist',
  'outputs',
  '.venv',
  '.venv-kimi',
  '__pycache__',
  'tests',
  'fixtures',
  'examples',
  'docs',
  'doc',
  'licenses',
]);
const lifecycle = [
  ['agent-session', /\bnew\s+(?:KimiSession|Session)\s*\(/g],
  ['broker-resolution', /\bresolveProjectTask\s*\(/g],
  ['chat-store', /\bnew\s+ChatStore\s*\(/g],
  ['resource-manager', /\bnew\s+SessionResourceManager\s*\(/g],
  ['begin-turn', /\bchats\.beginTurn\s*\(/g],
  ['finish-turn', /\bchats\.finish\s*\(/g],
];

function walk(root, relative = '') {
  const directory = path.join(root, relative);
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const name = path.posix.join(relative, entry.name);
    if (entry.isSymbolicLink()) {
      if (!skipped.has(entry.name)) throw Error(`Source symlink is not allowed: ${name}`);
      return [];
    }
    if (entry.isDirectory()) return skipped.has(entry.name) ? [] : walk(root, name);
    return /\.(?:cjs|mjs|js|ts|tsx|py)$/.test(entry.name) &&
      !/(?:\.test\.|selftest)/.test(entry.name)
      ? [name]
      : [];
  });
}

function imports(source) {
  return [
    ...source.matchAll(/(?:\b(?:require|import)\s*\(\s*|\bfrom\s+|\bimport\s+)['"]([^'"]+)['"]/g),
  ].map(match => match[1]);
}

function packageManifests(root, relative = '') {
  return fs.readdirSync(path.join(root, relative), { withFileTypes: true }).flatMap(entry => {
    const file = path.posix.join(relative, entry.name);
    if (entry.isDirectory()) return skipped.has(entry.name) ? [] : packageManifests(root, file);
    return entry.isFile() && entry.name === 'package.json' ? [file] : [];
  });
}

function observations(root) {
  const atoms = {},
    calls = {},
    imported = {};
  for (const file of walk(root)) {
    if (file === 'scripts/check-architecture-contract.cjs') continue;
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    imported[file] = imports(source);
    if (/^(?:apps\/|packages\/|lib\/)/.test(file) && !file.startsWith('packages/viewer-builtin/')) {
      for (const match of source.matchAll(
        /['"`]((?:chip|pcb|cad|godot|eda|freecad|yosys|verilator|openroad|kicad)(?:\.[A-Za-z][\w.-]*)?)['"`]/g,
      )) {
        const key = `${file}#${match[1]}`;
        atoms[key] = (atoms[key] || 0) + 1;
      }
    }
    if (file.startsWith('apps/cli/') || file.startsWith('apps/desktop/')) {
      for (const [name, expression] of lifecycle) {
        const count = [...source.matchAll(expression)].length;
        if (count) calls[`${file}#${name}`] = count;
      }
    }
  }
  return { atoms, calls, imported };
}

function inspect(root, policy, trusted = policy) {
  const failures = [];
  const fail = message => failures.push(message);
  if (trusted.revision !== revision || !roles.has(trusted.role))
    fail('Unknown architecture contract or repository role.');
  if (
    policy.revision !== trusted.revision ||
    policy.role !== trusted.role ||
    policy.canonical !== trusted.canonical
  )
    fail('Contract identity cannot change inside an ordinary feature PR.');
  if (JSON.stringify(policy.governanceHashes) !== JSON.stringify(trusted.governanceHashes))
    fail('Protected architecture machinery cannot be rebaselined in a feature PR.');
  if (JSON.stringify(policy.frozenPrefixes) !== JSON.stringify(trusted.frozenPrefixes))
    fail('Frozen migration areas cannot be removed or changed in a feature PR.');
  const safeFile = file => {
    if (
      typeof file !== 'string' ||
      path.isAbsolute(file) ||
      file.includes('\\') ||
      file.split('/').some(part => !part || part === '.' || part === '..')
    )
      throw Error(`Unsafe policy path: ${file}`);
    let current = root;
    for (const part of file.split('/')) {
      current = path.join(current, part);
      if (fs.lstatSync(current, { throwIfNoEntry: false })?.isSymbolicLink())
        throw Error(`Policy path cannot be a symlink: ${file}`);
    }
    return current;
  };
  for (const [file, digest] of Object.entries(trusted.governanceHashes || {})) {
    const target = safeFile(file);
    if (!fs.existsSync(target) || hash(fs.readFileSync(target)) !== digest)
      fail(
        `Protected architecture machinery changed: ${file}. Use a separately approved architecture revision.`,
      );
  }
  for (const [file, digest] of Object.entries(policy.frozen || {})) {
    if (trusted.frozen?.[file] !== digest) fail(`Legacy exception added or rebaselined: ${file}`);
  }
  for (const [file, digest] of Object.entries(trusted.frozen || {})) {
    const target = safeFile(file);
    if (fs.existsSync(target) && hash(fs.readFileSync(target)) !== digest)
      fail(
        `Frozen compatibility source changed: ${file}. Move the maintained change to its owning repository.`,
      );
    if (fs.existsSync(target) && policy.frozen?.[file] !== digest)
      fail(`An existing compatibility exception cannot be erased from policy: ${file}`);
  }
  const { atoms, calls, imported } = observations(root);
  for (const [name, actual, allowed] of [
    ['domainAtoms', atoms, trusted.domainAtoms || {}],
    ['adapterCalls', calls, trusted.adapterCalls || {}],
  ]) {
    for (const [key, count] of Object.entries(policy[name] || {}))
      if (count > (allowed[key] || 0)) fail(`Architecture exception expanded: ${name}/${key}`);
    for (const [key, count] of Object.entries(actual)) {
      if (
        count > Math.min(allowed[key] || 0, policy[name]?.[key] || 0) &&
        trusted.role !== 'domain-packs'
      )
        fail(`New ${name} outside its owner: ${key}`);
    }
  }
  const frozenPrefixes = trusted.frozenPrefixes || [];
  for (const prefix of frozenPrefixes) {
    const directory = safeFile(prefix);
    if (!fs.existsSync(directory)) continue;
    const visit = relative => {
      for (const entry of fs.readdirSync(path.join(root, relative), { withFileTypes: true })) {
        if (skipped.has(entry.name)) continue;
        const file = path.posix.join(relative, entry.name);
        if (entry.isSymbolicLink()) fail(`Compatibility source cannot be a symlink: ${file}`);
        else if (entry.isDirectory()) visit(file);
        else if (!Object.hasOwn(trusted.frozen || {}, file))
          fail(`New file in frozen compatibility area: ${file}`);
      }
    };
    visit(prefix);
  }
  for (const [file, names] of Object.entries(imported)) {
    if (Object.hasOwn(trusted.frozen || {}, file)) continue;
    const core =
      /^packages\/(?:harness-core|contracts|domain-runtime|capability-broker|viewer-core)\//.test(
        file,
      );
    for (const name of names) {
      const resolved = name.startsWith('.')
        ? path
            .relative(root, path.resolve(root, path.dirname(file), name))
            .split(path.sep)
            .join('/')
        : name;
      if (core && /(?:agent-kimi|@moonshot-ai\/|electron|(?:^|\/)apps\/)/.test(resolved))
        fail(`Core imports an adapter or agent kernel: ${file} -> ${name}`);
      if (
        file.startsWith('apps/cli/') &&
        /(?:apps\/desktop|electron|viewer-builtin)/.test(resolved)
      )
        fail(`CLI imports Desktop or Viewer UI: ${file} -> ${name}`);
      if (file.startsWith('apps/desktop/') && resolved.includes('apps/cli/'))
        fail(`Desktop imports CLI implementation: ${file} -> ${name}`);
      if (trusted.role === 'harness' && resolved.includes('domain-packs/'))
        fail(
          `Harness source directly imports a maintained domain implementation: ${file} -> ${name}`,
        );
      if (
        trusted.role !== 'harness' &&
        /(?:@moonshot-ai\/|agent-kimi|electron|viewer-builtin|(?:^|\/)apps\/(?:cli|desktop)\/)/.test(
          resolved,
        )
      )
        fail(`Domain/backend imports an application or agent kernel: ${file} -> ${name}`);
    }
    if (/^apps\/(?:cli|desktop)\//.test(file)) {
      const source = fs.readFileSync(path.join(root, file), 'utf8');
      if (
        /\b(?:spawn|spawnSync|execFile|execFileSync|execSync)\s*\(\s*['"`](?:yosys|verilator|iverilog|vvp|openroad|kicad(?:-cli)?|freecad(?:cmd)?|godot|ngspice|xyce)\b/i.test(
          source,
        )
      )
        fail(`Application directly executes an industrial tool: ${file}`);
    }
    if (
      trusted.role === 'domain-packs' &&
      /^(?:apps\/|lib\/(?:contracts|agent|harness-core)(?:\/|\.))/.test(file)
    )
      fail(`Domain Packs defines an application or canonical core: ${file}`);
    if (
      trusted.role === 'remote-service' &&
      /(?:^|\/)(?:verifier|native-profile|recipe)\.(?:cjs|js|py)$/.test(file)
    )
      fail(`Remote service defines domain execution/verification: ${file}`);
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  for (const file of packageManifests(root)) {
    if (Object.hasOwn(trusted.frozen || {}, file)) continue;
    const item = JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
    const dependencies = Object.keys({
      ...item.dependencies,
      ...item.devDependencies,
      ...item.peerDependencies,
      ...item.optionalDependencies,
    });
    const core =
      /^packages\/(?:harness-core|contracts|domain-runtime|capability-broker|viewer-core)\//.test(
        file,
      );
    for (const name of dependencies) {
      if (
        (core || trusted.role !== 'harness') &&
        /(?:@moonshot-ai\/|agent-kimi|electron|viewer-builtin)/.test(name)
      )
        fail(`Forbidden dependency in ${file}: ${name}`);
      if (
        file.startsWith('apps/cli/') &&
        /(?:electron|viewer-builtin|@industrial-agent-harness\/desktop)/.test(name)
      )
        fail(`CLI package depends on Desktop or Viewer UI: ${name}`);
      if (file.startsWith('apps/desktop/') && name === '@industrial-agent-harness/cli')
        fail('Desktop package depends on the CLI adapter.');
    }
    if (
      trusted.role !== 'harness' &&
      /^@industrial-agent-harness\/(?:contracts|harness-core|agent-kimi)$/.test(item.name || '')
    )
      fail(`Canonical/agent package cannot be defined outside Harness: ${file}`);
  }
  if (trusted.role !== 'harness') {
    for (const name of Object.keys({ ...manifest.dependencies, ...manifest.devDependencies }))
      if (/(?:@moonshot-ai\/|agent-kimi|electron|viewer-builtin)/.test(name))
        fail(`Forbidden application dependency: ${name}`);
  }
  if (trusted.role === 'remote-service') {
    const name = '@zhiman-bj/industrial-domain-packs';
    const dependency = manifest.dependencies?.[name];
    if (
      !/^https:\/\/codeload\.github\.com\/Zhiman-BJ\/industrial-domain-packs\/tar\.gz\/[a-f0-9]{40}$/.test(
        dependency || '',
      )
    )
      fail('Remote Domain Pack dependency must pin a full reviewed Git commit.');
    const lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json'), 'utf8'));
    if (
      lock.packages?.['']?.dependencies?.[name] !== dependency ||
      lock.packages?.[`node_modules/${name}`]?.resolved !== dependency ||
      !lock.packages?.[`node_modules/${name}`]?.integrity
    )
      fail('Remote Domain Pack lock identity or integrity is missing/mismatched.');
  }
  return {
    revision,
    role: trusted.role,
    ok: failures.length === 0,
    failures,
    frozenFiles: Object.keys(trusted.frozen || {}).length,
  };
}

function main(argv) {
  let root = process.cwd(),
    policyFile;
  for (let i = 0; i < argv.length; i++) {
    if (!['--root', '--policy'].includes(argv[i]) || !argv[i + 1])
      throw Error('Usage: check-architecture-contract [--root DIR] [--policy TRUSTED_JSON]');
    if (argv[i] === '--root') root = path.resolve(argv[++i]);
    else policyFile = path.resolve(argv[++i]);
  }
  const policy = JSON.parse(fs.readFileSync(path.join(root, 'architecture/policy.json'), 'utf8'));
  const trusted = policyFile ? JSON.parse(fs.readFileSync(policyFile, 'utf8')) : policy;
  const report = inspect(root, policy, trusted);
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  process.exitCode = report.ok ? 0 : 1;
}
module.exports = { hash, imports, observations, inspect, revision };
if (require.main === module) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    console.error(String(error));
    process.exitCode = 1;
  }
}
