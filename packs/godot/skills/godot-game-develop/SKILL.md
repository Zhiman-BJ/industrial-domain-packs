---
name: godot-game-develop
description: Typed text-scene edits and independent Godot import/geometry/frame verification through Industrial Runtime.
---

# Godot structural tasks

Use industrial_tool_describe / industrial_action_call with `godot.scene.edit` and `godot.scene.verify`. Every professional mutation enters the scoped Domain Runtime. Legacy `godot.game.*` native MCP writes are frozen standalone diagnostics and are not disclosed by Harness.

First release is Godot 4.7.2 on macOS Apple Silicon. Edit an ordinary `.tscn` file with a current `expectedSha256` and changes such as `{section:"node:Box",property:"position",value:{type:"Vector3",value:[1,2,3]}}` or `{section:"resource:Box_1",property:"size",value:{type:"Vector3",value:[2,3,4]}}`. Supports node position, rotation_degrees, scale, visible and BoxMesh size. Use the shared `project.files.apply` Runtime Action for scripts/config/assets; it invalidates engineering acceptance.

After every edit, reinspect and resolve a new scope. Run `godot.scene.verify` with file, frames (1..180) and nonempty explicit expect entries such as `{node:"Box",property:"mesh_size",value:[2,3,4]}`. Native import, separate resolved property/geometry readback and exact frame completion must succeed. All expectations must hold in readback and before/after the run. Script/parse errors, early quit, missing reports, timeout and cancellation cannot pass. The supported gate verifies structural properties; it does not prove arbitrary gameplay, rendering, audio or performance. Native headless/Dummy text operation and offline sandbox diagnostics are retained.

Godot works on an isolated input snapshot; `.godot` cache and generated imports remain in Runtime storage. Hashes bind original scene, scripts/assets and config. Outside project paths, symlinks, hard links and stale scopes are rejected. Install Godot 4.7.2 or set `INDUSTRIAL_HARNESS_GODOT_CMD` to its executable. A native exit or legacy `not_run` receipt is not engineering acceptance.
