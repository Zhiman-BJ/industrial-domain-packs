const fs = require('node:fs');
const { sha256, validateIdentity } = require('./protocol.cjs');
const digest = /^[a-f0-9]{64}$/;
const operations = { 'cuda.candidate.compile': 'compile', 'cuda.baseline.profile': 'baseline',
  'cuda.candidate.verify': 'verification', 'cuda.candidate.profile': 'profiling' };
function assess(receipt, expected) {
  const insufficient = reason => ({ status: 'insufficient_evidence', reason, metrics: {} });
  const role = receipt?.operation === 'compile' ? 'compiler' : 'evaluator';
  try { validateIdentity(receipt?.identity, role); } catch (error) { return insufficient(error.message); }
  const record = receipt.record;
  if (receipt.schemaVersion !== 1 || receipt.protocol !== 'cuda-domain-mcp/1' ||
      receipt.operation !== expected.operation || receipt.requestId !== expected.requestId ||
      receipt.projectId !== expected.projectId || receipt.sourceSha256 !== expected.sourceSha256 ||
      !digest.test(receipt.sourceSha256 || '') || !record)
    return insufficient('CUDA receipt is missing or bound to different inputs/action.');
  const metrics = {};
  if (receipt.operation === 'baseline') {
    if (record.success !== true || record.task_source_sha256 !== receipt.identity.taskSha256 ||
        receipt.sourceSha256 !== receipt.identity.taskSha256 ||
        typeof record.output !== 'string' || sha256(record.output) !== record.output_sha256 ||
        !Array.isArray(record.kernel_breakdown) || !record.kernel_breakdown.length)
      return insufficient('Immutable reference profiling evidence is missing or inconsistent.');
    return { status: 'passed', reason: 'Immutable reference model profiling completed; this does not verify a candidate.', metrics: { kernelCount: record.kernel_breakdown.length } };
  }
  if (record.source_manifest_sha256 !== receipt.sourceSha256 || !digest.test(receipt.ticketId || ''))
    return insufficient('Compiler ticket and candidate source identities differ.');
  const trace = record.phase_trace;
  if (!Array.isArray(trace) || trace.length < 1 || trace.length > 3)
    return insufficient('Independent compiler/evaluator phase evidence is missing.');
  for (const [index, phase] of trace.entries()) {
    const commands = ['bash utils/compile.sh', 'python -m utils.verification', 'python -m utils.profiling'];
    if (phase.sequence !== index + 1 || phase.paper_command !== commands[index] ||
        phase.source_manifest_sha256 !== receipt.sourceSha256 ||
        phase.location !== (index ? 'gpu_evaluator' : 'cpu_compiler') ||
        typeof phase.output !== 'string' || sha256(phase.output) !== phase.output_sha256 ||
        !['passed', 'failed'].includes(phase.outcome) ||
        !['executed', 'not_run'].includes(phase.execution) ||
        (phase.outcome === 'passed' && (phase.execution !== 'executed' || phase.exit_code !== 0)))
      return insufficient('Native phase evidence is inconsistent with the candidate.');
  }
  if (receipt.operation === 'compile') {
    if (typeof record.compiler_compile_ok !== 'boolean' || trace.length !== 1 ||
        trace[0].outcome !== (record.compiler_compile_ok ? 'passed' : 'failed'))
      return insufficient('Compiler result and native evidence disagree.');
    const passed = record.compiler_compile_ok && digest.test(trace[0].compiled_extension_sha256 || '');
    return { status: passed ? 'passed' : 'failed', reason: passed ? 'CPU compilation and policy checks passed; GPU correctness has not run.' : 'CPU compilation or policy checks failed.', metrics: { compiled: passed } };
  }
  if (!Number.isSafeInteger(record.candidate_id) || record.candidate_id < 1 ||
      !['policy_ok', 'compile_ok', 'correctness_ok'].every(key => typeof record[key] === 'boolean'))
    return insufficient('Native candidate verdict is missing.');
  Object.assign(metrics, { candidateId: record.candidate_id, correctness: record.correctness_ok,
    compiled: record.compile_ok, measurementClassification: record.profile_classification || 'unclassified' });
  if (!record.policy_ok || !record.compile_ok || !record.correctness_ok)
    return { status: 'failed', reason: 'Native policy, compilation or correctness checks failed.', metrics };
  if (trace.length < 2 || trace.slice(0, 2).some(phase => phase.outcome !== 'passed') ||
      !digest.test(record.compiled_extension_sha256 || '') ||
      record.compiled_extension_sha256 !== record.submitted_extension_sha256 ||
      trace.some(phase => phase.compiled_extension_sha256 !== record.compiled_extension_sha256) ||
      !Array.isArray(record.correctness_inputs) || record.correctness_inputs.length !== 5 ||
      !record.correctness_inputs.every((input, index) => input.input_index === index && input.outcome === 'passed'))
    return insufficient('Successful correctness requires five input audits and the compiled extension identity.');
  if (receipt.operation === 'profiling') {
    if (trace.length !== 3 || trace[2].outcome !== 'passed' ||
        !['torch_compile_us', 'cuda_us'].every(key => Number.isFinite(record[key]) && record[key] > 0))
      return insufficient('Valid performance phase and timings are required.');
    if (record.profile_classification !== 'exclusive')
      return insufficient('A contended or unclassified measurement cannot establish performance acceptance.');
    for (const snapshot of [record.pre_profile_snapshot, record.post_profile_snapshot])
      if (snapshot?.gpu_uuid !== receipt.identity.gpuUuid || snapshot.exclusive !== true ||
          snapshot.compute_process_count !== 0 || !Number.isFinite(snapshot.memory_used_mib) ||
          snapshot.memory_used_mib < 0 || snapshot.memory_used_mib > 512)
        return insufficient('Exclusive measurements require matching GPU snapshots before and after profiling.');
    metrics.speedupVsCompile = record.torch_compile_us / record.cuda_us;
    metrics.targetMet = metrics.speedupVsCompile >= 1.05;
  }
  return { status: 'passed', reason: receipt.operation === 'profiling'
    ? 'Correctness and exclusive GPU measurement passed; targetMet records the separate 5% optimization target.'
    : 'CPU compilation and five independent GPU correctness checks passed.', metrics };
}
function verifier({ result, artifacts, action, readArtifact }) {
  const reports = artifacts.filter(item => item.kind === 'report.cuda.candidate');
  if (reports.length !== 1) return { status: 'insufficient_evidence', reason: 'Exactly one CUDA receipt is required.', metrics: {} };
  try {
    const receipt = JSON.parse(readArtifact(reports[0]).toString('utf8'));
    if (receipt.identity.verifierSha256 !== sha256(fs.readFileSync(__filename))) throw Error('Remote and local CUDA Verifiers differ.');
    return assess(receipt, { requestId: action.id, projectId: action.projectId,
      operation: operations[action.toolId], sourceSha256: result.sourceSha256 });
  } catch (error) { return { status: 'insufficient_evidence', reason: error.message, metrics: {} }; }
}
module.exports = { assess, verifier, operations };
