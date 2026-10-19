# godot source provenance

Imported from committed public MIT source in Zhiman-BJ/industrial-agent-harness. Exact commits, source/destination paths and pre-adaptation hashes are in ../../provenance/domain-migration.json. Preserve ../../licenses/industrial-agent-harness.MIT. Maintained content, including layout adaptations, is identified by ../../content-lock.json.

Native executables and licensed project assets are installed separately and retain their own terms. Source migration does not qualify a new operating system or remote profile. FreeCAD uses the committed public PR29 snapshot; unfinished working files were not imported.

## Public professional runtime contribution (2026-10-08)

The new runtime/, typed tools, Verifiers and examples are original public MIT contributions maintained in this repository. They use separately installed official native executables under their original licenses. The mechanical board and structural scene examples are self-authored source inputs, contain no customer data, private actor code, licensed artwork or third-party assets. Exact maintained source hashes are in content-lock.json and harness-pack.json. Legacy import provenance remains historical evidence; it does not describe the new public acceptance path. See ../../docs/professional-runtime.md for qualified scope and native dependency archive locks.

## Community skill imports (2026-10-13)

Two public MIT skill sets are vendored under skills/ to extend Godot development knowledge beyond the structural runtime surface. They are static Skill content only: they add no runtime tools, Verifiers or native dependencies, and they do not widen the qualified acceptance path of godot.scene.edit / godot.scene.verify.

- GodotPrompter (https://github.com/jame581/GodotPrompter), commit d68ea7f6e87163489af071e6440840ee105c4285, MIT. Selected 19 knowledge skills (gdscript-patterns, gdscript-advanced, godot-code-review, state-machine, event-bus, component-system, scene-organization, resource-pattern, godot-debugging, godot-testing, godot-optimization, 2d-essentials, 3d-essentials, animation-system, godot-ui, input-handling, save-load, player-controller, shader-basics — each including its references/ tree) are copied unchanged. License preserved at ../../licenses/godotprompter.MIT.
- haxqer/godot-skill (https://github.com/haxqer/godot-skill), commit 037c03e9858ae748bf66c010aeb9942dbe9b93d6, MIT. The complete skill/godot directory is vendored as skills/godot-project-toolkit (SKILL.md, references/, scripts/, templates/, agents/). The upstream repo root (ci/, tests/, packaging scripts) is not part of the Skill surface and was not imported. Two adaptations: frontmatter name godot -> godot-project-toolkit and the agents/openai.yaml $godot reference to match the pack namespace; content is otherwise unchanged. License preserved at ../../licenses/haxqer-godot-skill.MIT.

Both sources target Godot 4.x (toolkit verified on 4.7, knowledge skills documented for 4.3+); statements inside the Skills are upstream knowledge, not qualified engineering acceptance claims of this Pack. Cross-references to GodotPrompter skills that were not vendored (for example csharp-godot) remain as upstream text. Upstream scripts are executed by the agent through normal file operations, not by the qualified Runtime.
