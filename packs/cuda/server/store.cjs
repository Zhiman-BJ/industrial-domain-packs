const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
class RequestStore {
  constructor(directory) {
    if (!path.isAbsolute(directory)) throw Error('CUDA evidence directory must be absolute.');
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    if (fs.lstatSync(directory).isSymbolicLink()) throw Error('CUDA evidence directory cannot be a symlink.');
    this.directory = fs.realpathSync(directory);
    fs.chmodSync(this.directory, 0o700);
  }
  file(id) { if (!/^[a-f0-9-]{36}$/.test(id)) throw Error('Invalid CUDA request ID.'); return path.join(this.directory, id + '.json'); }
  read(id) { const file = this.file(id); if (!fs.existsSync(file)) return null;
    const info = fs.lstatSync(file); if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > 40 * 1024 * 1024) throw Error('Invalid CUDA evidence file.');
    return JSON.parse(fs.readFileSync(file, 'utf8')); }
  write(id, value) { const file = this.file(id), tmp = file + '.' + crypto.randomUUID();
    fs.writeFileSync(tmp, JSON.stringify(value), { mode: 0o600, flag: 'wx' }); fs.renameSync(tmp, file); }
  entries() { const files = fs.readdirSync(this.directory).filter(file => /^[a-f0-9-]{36}\.json$/.test(file));
    if (files.length > 4096) throw Error('CUDA session request limit reached.'); let bytes = 0; for (const file of files) bytes += fs.statSync(path.join(this.directory, file)).size;
    if (bytes > 256 * 1024 * 1024) throw Error('CUDA session evidence quota exceeded.');
    return files.map(file => this.read(file.slice(0, -5))); }
  ticket(id) { if (!/^[a-f0-9]{64}$/.test(id)) throw Error('Invalid compiler ticket.');
    const entries = this.entries().filter(item => item.status === 'completed' && item.ticket && item.role === 'compiler' && item.output?.ticketId === id);
    if (entries.length !== 1) throw Error('Compiler ticket is unavailable.'); return entries[0].ticket; }
}
module.exports = { RequestStore };
