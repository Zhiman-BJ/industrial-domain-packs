// Read-only GPU admission before local native workers are provisioned. Remote
// clients deliberately skip local GPU inspection. This is not a native benchmark.
const { execute } = require('../server/backend.cjs');
const { profile } = require('../runtime/protocol.cjs');
const gpuId = /^GPU-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const query = ['--query-gpu=name,uuid,driver_version,compute_cap,memory.total,memory.used,utilization.gpu', '--format=csv,noheader,nounits'];
function parseInventory(output) {
  const lines = output.trim() ? output.trim().split(/\r?\n/) : [];
  if (lines.length > 256) throw Error('GPU inventory exceeds its limit.');
  const ids = new Set();
  return lines.map(line => {
    const [name, uuid, driverVersion, computeCapability, total, used, utilization, ...extra] = line.split(',').map(value => value.trim());
    if (extra.length || !name || !gpuId.test(uuid || '') || ids.has(uuid) ||
        !/^\d+(?:\.\d+)+$/.test(driverVersion || '') || !/^\d+\.\d+$/.test(computeCapability || '') ||
        ![total, used, utilization].every(value => /^\d+(?:\.\d+)?$/.test(value || '')))
      throw Error('GPU information is incomplete or ambiguous.');
    ids.add(uuid);
    const memoryTotalMiB = Number(total), memoryUsedMiB = Number(used), utilizationPercent = Number(utilization);
    if (![memoryTotalMiB, memoryUsedMiB, utilizationPercent].every(Number.isFinite) || memoryTotalMiB <= 0 || memoryUsedMiB > memoryTotalMiB || utilizationPercent > 100)
      throw Error('GPU capacity or utilization is invalid.');
    const reasons = [];
    if (!/^(?:NVIDIA )?(?:GeForce )?RTX 4090$/.test(name)) reasons.push('gpu_model_not_qualified');
    if (computeCapability !== '8.9') reasons.push('compute_capability_mismatch');
    if (driverVersion !== '595.71.05') reasons.push('driver_not_qualified');
    return { name, uuid, driverVersion, computeCapability, memoryTotalMiB, memoryUsedMiB,
      utilizationPercent, profileCompatible: reasons.length === 0, reasons };
  });
}
async function preflight({ mode = 'local-server', gpuUuid, platform = process.platform,
  architecture = process.arch, command = 'nvidia-smi', run = execute } = {}) {
  const report = { schemaVersion: 1, mode, profile, status: 'unsupported', reasons: [], gpus: [],
    selectedGpu: null, nativeQualification: 'not_run' };
  if (mode === 'remote-client') return { ...report, status: 'not_required', profile: null };
  if (mode !== 'local-server') throw Error('Choose local-server or remote-client installation.');
  if (gpuUuid && !gpuId.test(gpuUuid)) throw Error('Select a GPU using its stable UUID.');
  if (platform !== 'linux' || architecture !== 'x64') {
    report.reasons.push('local_host_platform_not_qualified'); return report;
  }
  try { report.gpus = parseInventory(await run(command, query, undefined, { timeout: 10000, limit: 128 * 1024 })); }
  catch { return { ...report, status: 'unavailable', reasons: ['gpu_detection_failed'] }; }
  if (!report.gpus.length) { report.reasons.push('no_nvidia_gpu'); return report; }
  const compatible = report.gpus.filter(gpu => gpu.profileCompatible);
  if (gpuUuid) {
    const selected = report.gpus.find(gpu => gpu.uuid === gpuUuid);
    if (!selected) report.reasons.push('selected_gpu_not_found');
    else if (!selected.profileCompatible) report.reasons = selected.reasons;
    else { report.status = 'ready'; report.selectedGpu = selected; }
  } else if (!compatible.length) report.reasons.push('no_qualified_gpu_profile');
  else if (compatible.length > 1) { report.status = 'select_gpu'; report.reasons.push('gpu_selection_required'); }
  else { report.status = 'ready'; report.selectedGpu = compatible[0]; }
  return report;
}
async function requireLocalGpu(config, options) {
  if (!gpuId.test(config.gpuUuid || '')) throw Error('Bind local CUDA installation to an explicit GPU UUID.');
  const report = await preflight({ ...options, mode: 'local-server', gpuUuid: config.gpuUuid });
  if (report.status !== 'ready') throw Error('CUDA installation GPU check failed: ' + report.reasons.join(', ') + '. Connect to a reviewed remote MCP service or repair the local environment.');
  return report;
}
module.exports = { preflight, parseInventory, requireLocalGpu };
if (require.main === module) {
  const args = process.argv.slice(2);
  if ((args.includes('--gpu') && !gpuId.test(args[args.indexOf('--gpu') + 1] || '')) || (args.includes('--gpu') && args.includes('--remote-client'))) throw Error('Choose remote-client or supply one explicit GPU UUID.');
  if (args.some(arg => !['--remote-client', '--gpu'].includes(arg) && !gpuId.test(arg))) throw Error('Use --remote-client or --gpu GPU-UUID.');
  preflight({ mode: args.includes('--remote-client') ? 'remote-client' : 'local-server', gpuUuid: args.includes('--gpu') ? args[args.indexOf('--gpu') + 1] : undefined })
    .then(report => { process.stdout.write(JSON.stringify(report, null, 2) + '\n'); if (!['ready', 'not_required', 'select_gpu'].includes(report.status)) process.exitCode = 2; })
    .catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
}
