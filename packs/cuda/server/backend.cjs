const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { upstream, imageLock } = require('../runtime/protocol.cjs');
const nativeScript = fs.readFileSync(path.join(__dirname, 'native.py'), 'utf8');
const installed = {
  '/opt/cuda-agent/candidates.py': 'harbor_cuda_agent/candidates.py',
  '/opt/cuda-agent/candidate_client.py': 'harbor_cuda_agent/candidate_client.py',
  '/opt/cuda-agent/candidate_compiler.py': 'harbor_cuda_agent/candidate_compiler.py',
  '/opt/cuda-agent/candidate_server.py': 'harbor_cuda_agent/candidate_server.py',
  '/opt/cuda-agent/verification_audit.py': 'harbor_cuda_agent/verification_audit.py',
  '/opt/cuda-agent/verify_task.py': 'tasks/axpby/tests/verify_task.py',
  '/opt/cuda-agent/capabilities.json': 'tasks/axpby/tests/capabilities.json',
  '/app/utils/compile.py': 'paper-runtime/utils/compile.py',
  '/app/utils/verification.py': 'paper-runtime/utils/verification.py',
  '/app/utils/native_profiling.py': 'paper-runtime/utils/native_profiling.py',
  '/app/utils/profiling.py': 'paper-runtime/utils/profiling.py',
  '/app/utils/_dispatch_guard.py': 'paper-runtime/utils/_dispatch_guard.py',
  '/opt/cuda-agent-lock/protected.sha256': 'tasks/axpby/tests/protected.sha256',
};
function execute(command, args, input, { signal, timeout = 900000, limit = 32 * 1024 * 1024 } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(Error('CUDA operation cancelled.'));
    const child = spawn(command, args, { stdio: ['pipe', 'pipe', 'pipe'], env: {
      PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C.UTF-8',
    } });
    let stdout = [], bytes = 0, stderr = '', failure;
    const cancel = () => { failure = Error('CUDA operation cancelled.'); child.kill('SIGKILL'); };
    const timer = setTimeout(() => { failure = Error('CUDA native operation timed out.'); child.kill('SIGKILL'); }, timeout);
    signal?.addEventListener('abort', cancel, { once: true });
    child.stdout.on('data', chunk => { bytes += chunk.length; if (bytes > limit) { failure = Error('CUDA output limit exceeded.'); child.kill('SIGKILL'); } else stdout.push(chunk); });
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-4096); });
    child.stdin.on('error', () => {});
    child.on('error', error => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); reject(error); });
    child.on('close', code => { clearTimeout(timer); signal?.removeEventListener('abort', cancel);
      if (failure) reject(failure); else if (code !== 0) reject(Error('CUDA native adapter failed: ' + stderr));
      else resolve(Buffer.concat(stdout).toString('utf8'));
    });
    child.stdin.end(input === undefined ? undefined : JSON.stringify(input));
  });
}
class DockerBackend {
  constructor(config) {
    this.config = config;
    if (!/^sha256:[a-f0-9]{64}$/.test(config.imageId || '') ||
        !['main', 'compiler', 'evaluator'].every(role => /^[a-f0-9]{64}$/.test(config.containers?.[role] || '')) ||
        new Set(Object.values(config.containers)).size !== 3 ||
        !/^[a-z0-9][a-z0-9-]{1,62}$/.test(config.allocation || '') ||
        !path.isAbsolute(config.docker || '/usr/bin/docker'))
      throw Error('Use an administrator-owned, immutable three-worker allocation.');
    this.docker = config.docker || '/usr/bin/docker';
    this.quarantined = false;
  }
  async command(args, input, options) { return execute(this.docker, args, input, options); }
  async native(role, request, signal) {
    const result = await this.command(['exec', '-i', '--user', '0:0', this.config.containers[role],
      'python', '-c', nativeScript], request, { signal });
    const parsed = JSON.parse(result);
    if (parsed?.error) throw Error('Native CUDA service rejected the operation: ' + JSON.stringify(parsed.error));
    return parsed;
  }
  async check() {
    if (this.quarantined) throw Error('CUDA allocation is quarantined after cancellation; recover it before continuing.');
    const all = JSON.parse(await this.command(['inspect', ...Object.values(this.config.containers)], undefined, { timeout: 10000 }));
    for (const [role, id] of Object.entries(this.config.containers)) {
      const item = all.find(row => row.Id === id), host = item?.HostConfig;
      if (!item?.State.Running || item.Image !== this.config.imageId || host.Privileged ||
          host.NetworkMode !== 'none' || host.PidsLimit < 1 || host.PidsLimit > 512 ||
          item.Config.Labels?.['com.docker.compose.project'] !== this.config.allocation ||
          item.Config.Labels?.['com.docker.compose.service'] !== role ||
          (item.Mounts || []).some(mount => mount.Type === 'bind') ||
          (role === 'main' && item.Config.User !== '10001:10001') ||
          (role === 'evaluator' && !host.ReadonlyRootfs) ||
          (role !== 'evaluator' && (!item.Config.Env.includes('NVIDIA_VISIBLE_DEVICES=void') || (host.DeviceRequests || []).length)))
        throw Error('CUDA worker isolation or image identity mismatch.');
      if (role !== 'main' && (host.NanoCpus <= 0 || host.Memory <= 0)) throw Error('CUDA workers need actual CPU/memory limits.');
      if (role === 'evaluator' && !(host.DeviceRequests || []).some(device =>
          device.Driver === 'nvidia' && device.DeviceIDs?.length === 1 && device.DeviceIDs[0] === this.config.gpuUuid))
        throw Error('CUDA evaluator must have exactly its assigned GPU.');
      const paths = Object.keys(installed).filter(file => role === 'main' ? !file.includes('candidate_compiler') && !file.includes('candidate_server') : true);
      const hashes = await this.native(role, { operation: 'identity', paths: [...paths, '/app/model.py', '/opt/cuda-agent-workspace/model.py'] });
      if (hashes['/app/model.py'] !== this.config.taskSha256 || hashes['/opt/cuda-agent-workspace/model.py'] !== this.config.taskSha256) throw Error('CUDA reference model identity mismatch.');
      for (const file of paths) if (hashes[file] !== (file.startsWith('/app/') ? imageLock.protectedFiles[file.slice(5)] : (file === '/opt/cuda-agent-lock/protected.sha256' ? imageLock.protectedManifestSha256 : upstream.files[installed[file]]))) throw Error('CUDA installed upstream source mismatch: ' + installed[file]);
      await this.command(['exec', '--user', '0:0', id, 'sh', '-c', 'cd /app && sha256sum -c /opt/cuda-agent-lock/protected.sha256'], undefined, { timeout: 10000 });
    }
    const gpu = (await this.command(['exec', this.config.containers.evaluator, 'nvidia-smi', '--query-gpu=uuid,driver_version,compute_cap', '--format=csv,noheader'], undefined, { timeout: 10000 })).trim().split(/,\s*/);
    if (gpu.length !== 3 || gpu[0] !== this.config.gpuUuid || gpu[1] !== '595.71.05' || gpu[2] !== '8.9') throw Error('CUDA native GPU/driver/profile mismatch.');
    return { ready: true, nativeQualification: 'requires-qualification-record', gpuUuid: this.config.gpuUuid };
  }
  async attest() {
    // The source-only client sandbox is the same unprivileged, GPU-free UID used
    // by upstream. No model credentials or agent kernel enter this allocation.
    const main = this.config.containers.main;
    const response = JSON.parse(await this.command(['exec', '-i', '--user', '10003:10003', main,
      'python', '-c', nativeScript], { operation: 'attest' }));
    if (response.attested !== true) throw Error('CUDA client sandbox attestation failed.');
  }
  async compile(args, sequence, signal) {
    return this.native('compiler', { operation: 'compile', sequence,
      sourceSha256: args.sourceSha256, files: args.files }, signal);
  }
  async baseline(sequence, signal) {
    await this.attest();
    let record = await this.native('evaluator', { operation: 'baseline_record' }, signal);
    if (!record) {
      await this.native('compiler', { operation: 'baseline', sequence }, signal);
      record = await this.native('evaluator', { operation: 'baseline_record' }, signal);
    }
    return record;
  }
  async evaluate(ticket, operation, sequence, signal) {
    await this.baseline(sequence, signal);
    const submission = { ...ticket.submission, turn_number: sequence, evaluation_mode: operation };
    const response = await this.native('compiler', { operation: 'evaluate', submission }, signal);
    const record = await this.native('evaluator', { operation: 'record', candidateId: response.candidate_id }, signal);
    if (record.source_manifest_sha256 !== ticket.sourceSha256) throw Error('Evaluator ledger differs from compiler ticket.');
    return record;
  }
  async cancel() {
    this.quarantined = true;
    // Only the exact owned allocation is stopped. Stopping the native workers
    // also removes isolated compile/GPU descendants left by an interrupted exec.
    await this.command(['stop', '--time', '3', ...Object.values(this.config.containers)], undefined, { timeout: 20000 });
    const details = JSON.parse(await this.command(['inspect', ...Object.values(this.config.containers)], undefined, { timeout: 10000 }));
    if (details.some(item => item.State.Running)) throw Error('CUDA cancellation cleanup could not be confirmed.');
  }
  async recover() {
    await this.cancel();
    await this.command(['start', ...Object.values(this.config.containers)], undefined, { timeout: 20000 });
    this.quarantined = false;
    for (let attempt = 0; attempt < 30; attempt++) {
      try { return await this.check(); } catch (error) { if (attempt === 29) throw error; await new Promise(resolve => setTimeout(resolve, 1000)); }
    }
  }
}
module.exports = { DockerBackend, execute, installed };
