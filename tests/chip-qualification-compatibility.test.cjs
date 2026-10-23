const test = require('node:test');
const assert = require('node:assert/strict');
// Frozen A verifier from Pack ba772952; never broaden it to accept candidate B.
const {verifier: a} = require('./fixtures/qualification/chip-verifier-a.cjs');
const {verifier: b} = require('../packs/chip/runtime/verifier.cjs');
function evidence(field, log = '') {
  return {
    result: {[field]: {status: 'PASS'}},
    artifacts: [
      {kind: 'log.tool', text: log},
      {kind: 'report.simulation', text: 'EDA_SIMULATION_FINISHED'},
      {kind: 'waveform.vcd', text: '$var wire 1 ! clk $end\n$enddefinitions $end\n#0\n0!\n'},
    ],
    readArtifact: item => Buffer.from(item.text),
  };
}
for (const [label, verifier, field, expected] of [
  ['A accepts A', a, 'nativeVerification', 'passed'],
  ['B accepts B', b, 'qualificationNativeVerification', 'passed'],
  ['B cannot verify A', b, 'nativeVerification', 'insufficient_evidence'],
  ['A cannot verify B', a, 'qualificationNativeVerification', 'insufficient_evidence'],
]) test(label, () => assert.equal(verifier(evidence(field)).status, expected));
test('native assertion failure stays a business failure under either verifier', () => {
  for (const verifier of [a,b])
    for (const field of ['nativeVerification','qualificationNativeVerification'])
      assert.equal(verifier(evidence(field,'%Error: Assertion failed')).status,'failed');
});
