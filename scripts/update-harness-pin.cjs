const fs = require('node:fs');
const path = require('node:path');

const name = '@zhiman-bj/industrial-domain-packs';
const immutable = /^https:\/\/codeload\.github\.com\/Zhiman-BJ\/industrial-domain-packs\/tar\.gz\/[a-f0-9]{40}$/;
const versionPattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

function updateHarnessPin(directory, { oldUrl, newUrl, version }) {
  if (!immutable.test(oldUrl) || !immutable.test(newUrl)) {
    throw new Error('Both pins must be immutable Domain Packs commit URLs');
  }
  if (!versionPattern.test(version || '')) throw new Error('An exact target package version is required');
  const manifests = ['package.json', 'apps/cli/package.json', 'apps/desktop/package.json'];
  const updates = manifests.map(relative => {
    const file = path.join(directory, relative);
    const pkg = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (pkg.dependencies?.[name] !== oldUrl) throw new Error(`Unexpected pin in ${relative}`);
    pkg.dependencies[name] = newUrl;
    return [file, pkg];
  });
  const peerFile = path.join(directory, 'packages/domain-skills/package.json');
  const peer = JSON.parse(fs.readFileSync(peerFile, 'utf8'));
  if (!versionPattern.test(peer.peerDependencies?.[name] || '')) {
    throw new Error('Expected an exact Domain Packs peer version in domain-skills');
  }
  peer.peerDependencies[name] = version;
  updates.push([peerFile, peer]);
  // Validate every consumer before writing any manifest.
  for (const [file, pkg] of updates) fs.writeFileSync(file, JSON.stringify(pkg, null, 2) + '\n');
  return updates.map(([file]) => path.relative(directory, file));
}

if (require.main === module) {
  const directory = process.argv[2];
  if (!directory) throw new Error('Usage: node update-harness-pin.cjs <harness-directory>');
  const changed = updateHarnessPin(directory, {
    oldUrl: process.env.OLD_URL,
    newUrl: process.env.NEW_URL,
    version: process.env.TARGET_VERSION,
  });
  console.log(changed.join('\n'));
}
module.exports = { updateHarnessPin };
