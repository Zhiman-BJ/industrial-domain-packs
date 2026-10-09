---
name: godot-game-inspect
description: Inspect current Godot source and independently verify bounded scene properties.
---

Use `industrial_tool_describe` and `project.files.read` for current scene paths and source hashes. Source values do not prove resolved native values or frame behavior. Describe `godot.scene.verify`, then call it through `industrial_action_call` with the current expectedStateId, an explicit scene file, 1..180 frames and nonempty node/property expectations. Report the canonical Verification and evidence hashes. A preview, model statement or successful process exit never establishes acceptance. After source changes rerun verification; historical successful evidence remains attributed to its original inputs.
