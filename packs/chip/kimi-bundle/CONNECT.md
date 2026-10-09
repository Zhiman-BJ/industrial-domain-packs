# Connect an existing Kimi Code CLI

[简体中文](CONNECT.zh-CN.md)

Already have the official **native Kimi Code 2.1.1** on Linux amd64? Add Chip
Domain Pack to that installation with the independent connector:

```sh
wget -O connect-kimi-chip.sh https://github.com/Zhiman-BJ/industrial-domain-packs/releases/download/kimi-chip-v0.1.0-preview.1/connect-kimi-chip.sh
bash connect-kimi-chip.sh
```

The connector leaves your Kimi executable, model configuration, login credentials,
session history and automatic update settings in place. It installs a private
Python and Chip runtime, merges the `chip` server into native `mcp.json`, and adds
the native `chip-design` Skill. It shares the checksum-pinned archive with the
[standalone bundle](README.md); that archive includes upstream Kimi, but the
connector never executes that binary or replaces your existing CLI.

Only native Kimi Code **2.1.1** is qualified for this release. The connector checks
the existing CLI and rejects other versions, including the legacy Python CLI,
instead of guessing its configuration layout. It does not upgrade Kimi. An
upstream update after connection also needs renewed qualification before use as a
fixed trajectory-collection environment.

## Existing command, existing data directory

The default target is **`~/.kimi-code`**, the official native CLI's home. When you
already run Kimi with `KIMI_CODE_HOME`, use the same value for connection and every
subsequent launch. This differs from the standalone bundle's isolated data root.

```sh
# For a CLI not in PATH, select its existing executable explicitly.
bash connect-kimi-chip.sh --kimi-bin /absolute/path/to/kimi

# Reuse a custom native home and an already-installed full Chip image.
KIMI_CODE_HOME=/absolute/path/to/native-home \
  bash connect-kimi-chip.sh --image YOUR_EXISTING_CHIP_IMAGE

# Register only MCP and Skill without preparing an EDA image.
bash connect-kimi-chip.sh --skip-image
```

Run as the user who owns the existing Kimi installation. An installed CLI named
`kimicode` can be selected using `--kimi-bin /absolute/path/to/kimicode`; it still
must be the qualified native version. `--prefix /absolute/directory` chooses the
private runtime location. Repeated connection preserves unrelated MCP servers
and user settings. An edited or unmanaged `chip` entry or Skill resource causes a
clear conflict instead of replacement. The installer records source, native CLI
version observed at connection, image identity and managed resource hashes in
`$KIMI_CODE_HOME/chip-pack-connect.json`.

On supported Ubuntu/Debian hosts, the public entry prepares missing host utilities
and Docker using the same setup as the standalone bundle. Default installation
builds the pinned full EDA image; `--image` probes an existing image instead.
`--skip-image` connects MCP and Skill but does not make EDA execution available.
If new Docker group membership needs a temporary bridge, only Chip's MCP receives
that helper through its own environment. Continue launching your own Kimi command.

## Check and use the native integration

Restart Kimi and create a new session: MCP servers added to a configuration file
are not registered in an already-open session. In the interactive CLI:

```text
/mcp
/skill:chip-design
```

`/mcp` should show `chip` connected; the Skill explains the direct native Chip MCP
tools and available EDA workflows. A project `.kimi-code/mcp.json` entry named
`chip` overrides the user entry. Likewise, explicit `--skills-dir` replaces native
Skill discovery; include the native home Skill directory if you use that flag.

Run from your engineering project directory with your existing model configured:

```sh
kimi -p 'Use chip-design to inspect this project, run the required RTL checks, and report the actual evidence.' --output-format stream-json
kimi --continue -p 'Continue from the existing project and collected evidence.' --output-format stream-json
kimi session list --json
kimi export SESSION_ID -o chip-trajectory.zip --no-include-global-log
```

Use your selected executable path in place of `kimi` when needed. `-p` is the
native automatic batch mode; do not combine it with `--auto` or `--yolo`. Native
session records and export remain managed by Kimi. The connector adds no agent
loop, task deadline or step limit, and does not change native repeat detection or
per-tool/resource deadlines. Preserve the project and its `.eda` evidence together
with the session export and connection receipt when collecting training data.

The connector contains no model credentials, customer project, PDK or precomputed
engineering result. This standalone collection path uses the Pack's raw MCP;
Harness-integrated protected actions continue to use the Harness Runtime.
