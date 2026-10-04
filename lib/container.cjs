const { execFile } = require("node:child_process");
function command(args, { timeout = 30000, maxBuffer = 1024 * 1024 } = {}) {
  // Only trusted coordinator Docker settings cross this boundary; no model keys.
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([name]) =>
      /^(?:PATH|HOME|DOCKER_HOST|DOCKER_CONTEXT|DOCKER_CONFIG|XDG_RUNTIME_DIR|SSH_AUTH_SOCK|TMPDIR|LANG)$/.test(
        name,
      ),
    ),
  );
  return new Promise((resolve) =>
    execFile(
      "docker",
      args,
      { env, timeout, maxBuffer, encoding: "buffer" },
      (error, stdout, stderr) =>
        resolve({ code: error?.code || 0, stdout, stderr, error }),
    ),
  );
}
module.exports = { command };
