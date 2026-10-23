const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
function fixture(t) {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'prod-writeback-'));
  t.after(() => fs.rmSync(cwd, {recursive:true, force:true}));
  const git = (...args) => execFileSync('git', args, {cwd, encoding:'utf8', stdio:['ignore','pipe','pipe']}).trim();
  git('init','-b','main'); git('config','user.name','github-actions[bot]');
  git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com');
  fs.writeFileSync(path.join(cwd,'package.json'), JSON.stringify({files:[]}));
  git('add','.'); git('commit','-m','source');
  const source = git('rev-parse','HEAD');
  git('checkout','-b','automation/runtime-prod/'+source);
  const file = 'packs/chip/releases/rtl-cpu/prod.lock.json';
  fs.mkdirSync(path.dirname(path.join(cwd,file)), {recursive:true});
  fs.writeFileSync(path.join(cwd,file), JSON.stringify({buildSourceCommit:source}));
  git('add','.'); git('commit','-m','chore: publish chip, freecad, pcb runtimes');
  return {cwd,git,source};
}
test('prod child and exact merge are recognized without re-publishing', t => {
  const {cwd,git,source} = fixture(t);
  const api = require('../scripts/runtime-publication-trigger.cjs');
  assert.equal(typeof api.inspectWriteback, 'function');
  assert.deepEqual(api.inspectWriteback(cwd), {environment:'prod', source});
  const branch = git('branch','--show-current');
  git('checkout','main'); git('merge','--no-ff',branch,'-m','Merge prod locks');
  assert.deepEqual(api.inspectWriteback(cwd), {environment:'prod', source});
});
test('merge with intervening source changes is not a completed publication', t => {
  const {cwd,git} = fixture(t);
  const api = require('../scripts/runtime-publication-trigger.cjs');
  assert.equal(typeof api.inspectWriteback, 'function');
  const branch = git('branch','--show-current');
  git('checkout','main'); fs.writeFileSync(path.join(cwd,'new-code.cjs'),'changed');
  git('add','.'); git('commit','-m','new source');
  git('merge','--no-ff',branch,'-m','Merge stale prod locks');
  assert.equal(api.inspectWriteback(cwd), null);
});
test('prod branch push leaves main untouched and rejects stale main', t => {
  const {cwd,git,source} = fixture(t);
  const remote = path.join(cwd,'remote.git');
  git('init','--bare',remote); git('remote','add','origin',remote); git('push','origin','main');
  const api = require('../scripts/runtime-publication-writeback.cjs');
  assert.equal(typeof api.pushProductionBranch,'function');
  const branch = api.pushProductionBranch({cwd,source});
  assert.equal(branch,'automation/runtime-prod/'+source);
  assert.equal(git('ls-remote','origin','refs/heads/main').split(/\s/)[0],source);
  assert.equal(api.pushProductionBranch({cwd,source}),branch);
  git('push','origin','HEAD:main');
  assert.throws(() => api.pushProductionBranch({cwd,source}), /Source branch moved/);
});
test('production PR reuses the exact existing head and enables guarded merge', async () => {
  const api = require('../scripts/runtime-publication-writeback.cjs');
  assert.equal(typeof api.openProductionPR,'function');
  const calls=[];
  const pr={number:9,node_id:'PR_9',html_url:'https://example/pr/9',head:{sha:'b'.repeat(40)}};
  const github={rest:{pulls:{list:async()=>({data:[pr]}),create:async()=>{throw Error('duplicate PR');}}},graphql:async(query,args)=>calls.push({query,args})};
  assert.equal(await api.openProductionPR({github,owner:'org',repo:'repo',source:'a'.repeat(40),head:'b'.repeat(40)}),pr.html_url);
  assert.match(calls[0].query,/enablePullRequestAutoMerge/);
  assert.match(calls[0].query,/MERGE/);
  await assert.rejects(api.openProductionPR({github,owner:'org',repo:'repo',source:'a'.repeat(40),head:'c'.repeat(40)}),/head differs/);
});
test('generated locks only skip publication in their intended environment and branch', () => {
  const {selectWriteback} = require('../scripts/runtime-publication-trigger.cjs');
  assert.equal(typeof selectWriteback,'function');
  const source='a'.repeat(40), prod={environment:'prod',source}, testLock={environment:'test',source};
  assert.equal(selectWriteback('refs/heads/feature',prod),null);
  assert.equal(selectWriteback('refs/heads/main',testLock),null);
  assert.equal(selectWriteback('refs/heads/main',prod),prod);
  assert.equal(selectWriteback('refs/heads/feature',testLock),testLock);
  assert.equal(selectWriteback('refs/heads/automation/runtime-prod/'+source,prod),prod);
  assert.throws(()=>selectWriteback('refs/heads/automation/runtime-prod/'+source,testLock),/Invalid production/);
  assert.throws(()=>selectWriteback('refs/heads/automation/runtime-prod/'+'b'.repeat(40),prod),/Invalid production/);
});
