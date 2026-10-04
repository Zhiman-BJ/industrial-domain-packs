---
name: godot-game-develop
description: Build or repair a Godot game using scoped native checks and explicit acceptance evidence.
---

Discover the current `godot.game.*` IDs with `domain_tool_list`; use `domain_tool_describe` to inspect arguments before calling them. Check `project_status` and inspect relevant `.tscn` source. Run `check_project` to import assets and report parse diagnostics before `inspect_scene_runtime`; that operation instantiates a scene and can run its scripts. After edits, rerun `check_project` and use `run_scene` for a bounded headless smoke run. These checks can write project files and require approval under the host policy.

Native execution success does not establish gameplay correctness. Compare the observed nodes, dimensions, frame regions and behavior with the task's stated acceptance criteria. For visual or interactive tasks, inspect rendered evidence or run an explicit task-specific check. Record failed or incomplete checks honestly; a timeout has an uncertain side-effect outcome. Project file editing may use the agent's normal authorized file tools; these MCP tools provide focused observation and verification evidence.
