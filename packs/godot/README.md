# Godot local game tools

The repository-owned `godot.local` MCP provides five scoped Godot 4 tools: `project_status`, `inspect_scene_source`, `inspect_scene_runtime`, `check_project`, and `run_scene`. `domain_tool_list` and `domain_tool_describe` disclose only the current Broker allowlist. The two accompanying Skills cover source inspection and native development checks.

Install Godot 4 and put `godot` on `PATH`, or set `INDUSTRIAL_HARNESS_GODOT_BIN` to its absolute executable path. The binary version is checked when a session starts; the exact patch version is recorded in its runtime identity. No Godot binary is shipped with the pack. The bundled GDScript inspector and Node runtime are pinned by the Domain Pack resource manifest.

`project_status` and `inspect_scene_source` only read bounded project files. `inspect_scene_runtime` loads and instantiates a scene; `check_project` runs a headless import; `run_scene` starts a scene for at most 180 frames. Those three operations can execute project scripts or write files and have mutating risk. The gateway binds the selected project and rejects paths escaping it. It does not sandbox Godot or enumerate every file the engine may write. Action receipts state this limitation explicitly. A successful import/run is a native smoke check, not gameplay acceptance.

For a fresh project, run `check_project` before runtime scene inspection so texture and other imports exist. The Web Export Viewer still requires its separate bridge and export setup; these tools operate on source projects and do not create a Web export.

Validation: `node --test tests/integration/godot-mcp-policy.test.cjs` exercises Broker disclosure, settings override, MCP allowlist/schema/path enforcement, receipts and a real Godot 4 scene when the local 4.4.1 binary is present. `pnpm test` and `pnpm test:architecture` cover shared regressions.
