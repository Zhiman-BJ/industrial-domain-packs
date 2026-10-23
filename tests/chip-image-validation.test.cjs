const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
test('image validation invokes the native entry rather than the long-running worker', t => {
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'chip-image-validation-'));
  t.after(()=>fs.rmSync(temp,{recursive:true,force:true}));
  const docker=path.join(temp,'docker');
  fs.writeFileSync(docker,`#!${process.execPath}
const fs=require('node:fs'),path=require('node:path'),args=process.argv.slice(2);
if(args[0]==='rm') process.exit(0);
if(args[args.indexOf('--entrypoint')+1]!=='python3' || !args.includes('/opt/domain-packs/packs/chip/runtime/native-profile.py')) process.exit(42);
const mount=args.find(a=>a.endsWith(',target=/work'));
const out=mount.slice('type=bind,source='.length,-',target=/work'.length);
fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({profileId:'rtl-cpu',completionMarker:'EDA_SIMULATION_FINISHED',qualificationNativeVerification:{status:'PASS'}}));
fs.writeFileSync(path.join(out,'wave.vcd'),'$var wire 1 a clock $end\\n$enddefinitions $end\\n#1\\n1a');
fs.writeFileSync(path.join(out,'tool.log'),'finished');
`,{mode:0o755});
  const root=path.join(__dirname,'..');
  const result=spawnSync(process.execPath,['packs/chip/scripts/validate-image.cjs','packs/chip/examples/rtl','--output',path.join(temp,'out'),'--image','fixture-image'],{cwd:root,encoding:'utf8',env:{...process.env,PATH:temp+path.delimiter+process.env.PATH}});
  assert.equal(result.status,0,result.stdout+result.stderr);
});
