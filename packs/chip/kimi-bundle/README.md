# Kimi Code + Chip Domain Pack

[简体中文](README.zh-CN.md)

A standalone Chip Pack derivative for collecting native agent trajectories. It
ships the **unmodified official Kimi Code CLI 2.1.1 executable**, Chip's 25-tool
stdio MCP, a native `chip-design` Skill, Python 3.13.16 and hash-locked Python
dependencies. The default installer prepares the Pack's full Docker tool recipe.
It uses no Industrial Agent Harness application or orchestration libraries.

## Install on Linux x86_64

```bash
wget -O install-kimi-chip.sh https://github.com/Zhiman-BJ/industrial-domain-packs/releases/download/kimi-chip-v0.1.0-preview.1/install-kimi-chip.sh
bash install-kimi-chip.sh
~/.local/bin/kimi-chip login
cd /absolute/path/to/project
~/.local/bin/kimi-chip
```

The bootstrap prepares missing curl, git and ripgrep, and Docker Engine on
supported Ubuntu/Debian systemd hosts. It asks sudo only for host setup. Existing
accessible Docker is reused. The first full tool-image build downloads large
upstream images. Model credentials, task inputs and any process/PDK licenses are
provided separately. Host viewers are optional.

Use `--image EXISTING_IMAGE` to reuse and check a prepared full EDA image. Use
`--skip-image` for an explicit CLI/MCP-only install; this does **not** prepare EDA
execution. `--prefix /absolute/bundle` and `--bin-dir /absolute/bin` change install
locations. Set `KIMI_CODE_HOME=/absolute/data` during installation and invocation
for an independent collection worker. Other distributions can use the bundle's
`install.sh` after preparing Docker, bash, git, ripgrep, curl, tar and flock.

The archive includes the upstream executable, Python and all MCP libraries;
installing that archive does not fetch a model, npm package or Python package.
Docker image preparation still needs network access unless `--image` supplies an
existing image. The complete toolchain and a licensed PDK are separate things.

## Collect and export native trajectories

All arguments go straight to the upstream CLI:

```bash
cd /absolute/path/to/project
kimi-chip --auto -p "$(cat /absolute/task.txt)" --output-format stream-json > turn.jsonl
kimi-chip session list --json
kimi-chip --continue -p "Continue the same task" --output-format stream-json > continuation.jsonl
kimi-chip export SESSION_ID -o trajectory.zip --no-include-global-log
```

`--auto` is upstream Never Ask mode; choose it explicitly for unattended collection.
Without it, native approval behavior applies. The bundle adds no turn timer or
step limit. Upstream print mode is unbounded by default; project action resource
limits and individual MCP request timeouts still apply. Long EDA computation is
asynchronous: submit once and poll its run ID.

The default native data directory is `~/.local/share/kimi-chip`:

- `config.toml`: ordinary upstream provider/model settings. Use native login or
  edit this file to configure your OpenAI-compatible endpoint and `api_key_env`.
- `mcp.json`: direct `chip` stdio registration, with no gateway.
- `skills/chip-design/`: the Pack Skill and observed installed image identity.
- `sessions/<workDirKey>/<sessionId>/agents/*/wire.jsonl`: upstream session event
  streams, including request traces and tool listings.
- `chip-bundle-install.json`: exact Pack commit, upstream binary hash and image ID.

The native export ZIP retains the session directory. `--output-format stream-json`
is live output; retain the native session/export as the source trajectory. The
bundle does not rewrite messages, intercept tool results or synthesize reasoning.
Archive the install receipt and project configuration alongside each dataset so
that its kernel, tools and inputs remain attributable. Automatic CLI updates are
disabled using upstream's supported environment setting; update the pinned bundle
deliberately. An explicit native `upgrade` command is outside the pinned release.

Project `.kimi-code/skills` and `.agents/skills` remain available through native
discovery. Generic user Skills under `~/.agents/skills` are also discovered by
upstream; collection workers should deliberately control their user/project Skill
directories. The wrapper does not inject a custom agent profile or system prompt.

## Scope and maintenance

Maintained entirely under `packs/chip/kimi-bundle/` in Domain Packs. `build.py`
packages only clean committed source and checksum-pinned upstream artifacts.
`configure.py` writes standard native MCP/Skill config; the shell entry ends in
`exec upstream/kimi "$@"`. No agent loop, Broker, TaskService, canonical records,
extra approvals or execution proxy is introduced. Harness and remote consumers
retain their existing Pack integrations.

This preview targets native Linux amd64. Installation checks connectivity and tool
inventory; engineering acceptance comes from the configured project's actual
verification. EDA actions retain their original persistent state/run/artifact
records. Kimi interruption does not automatically cancel an asynchronous Pack run.
This is a collection distribution, not a foundry signoff qualification.

Build on Linux with Python >=3.12, curl, git and tar:

```bash
python3 packs/chip/kimi-bundle/build.py
```

Licenses for Kimi, Python, Chip and bundled dependencies are retained in the
archive. Native tools and PDKs retain their individual terms. Internal repository
documentation, credentials, customer projects and historical runs are excluded.
