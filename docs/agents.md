# Domain Pack Agents

The owner package adds six role definitions in version 0.6.0: Chip engineer,
PCB designer, Godot developer, FreeCAD designer, CAD drawing author and CUDA
optimizer. Their descriptions, domain instructions and Skill references are
maintained here. Harness owns role selection, user-created roles, project and
session settings, conversion to the selected agent kernel, and execution.

This is additive trusted metadata. It does not introduce an agent loop or a
new native execution profile. Consumers pin the new full owner commit and
content identity to adopt the definitions. Native tool and runtime versions
retain their existing identities; the package version and immutable owner
commit identify these additional Agent resources.

To make the roles available through future Pack updates, distribution versions
advance to Chip 0.6.3, PCB 0.1.0-kicad.3, Godot 0.2.3 and FreeCAD 1.1.4-pack.8.
The CAD distribution includes both FreeCAD and CAD drawing roles. CUDA has no
qualified desktop bundle and retains its existing distribution metadata. These
new distribution archives have not been published; previous versions retain
their original bytes. Native assets, Runtime tools, Verifiers and their locked
identities are unchanged.

## Consumer API

`consumerMetadata().agents` returns declarations in this shape:

```json
{
  "id": "cad.freecad.designer",
  "title": "FreeCAD designer",
  "description": "Build and revise parametric parts with measurable requirements and independent geometry verification.",
  "domain": "cad",
  "packId": "freecad-pack",
  "resourcePath": "agents/cad.freecad.designer.md",
  "skills": ["cad.freecad.headless"]
}
```

`agentResource(id)` resolves a declared Agent and returns a fresh copy of its
declaration plus `file` (an absolute path) and `instructions` (UTF-8 text).
Instructions are ordinary Markdown body text, without kernel-specific
frontmatter or templates. Resources must be nonempty ordinary files under the
owning Pack's `agents/` directory, no larger than 128 KiB, with no symlink path
components. Resource bytes must match `content-lock.json` when loaded.

`hostPacks()` includes each host Pack's `agents`, derived from the same consumer
catalog. The CAD guidance Pack has no host provider; its Agent remains available
through `consumerMetadata()`. Each source `pack.json` lists its Agent resource
paths in `agents`, so Pack archives and the installed owner package retain the
same resources. Consumers must load the role from its owning Pack, preserve its
source identity and enforce the project's Pack availability policy.

## Optional role configuration

The optional `skills`, `tools`, `disallowedTools` and `subagents` fields are
arrays of strings. Harness validates and resolves these references before
starting a session; it must not silently drop a missing role or unavailable
requested restriction.

- `skills` uses consumer Skill IDs. As a primary Agent, it selects a session
  Skill allowlist intersected with the effective global defaults and project
  overrides, then the current Broker scope. An explicit project setting may
  override a global default. An omitted list inherits the allowed session Skills;
  an explicit empty list selects none. Delegated Agents inherit the primary
  session's Skill scope; the declaration does not create per-child Skill grants.
  This controls registered domain Skill disclosure, not physical access to
  arbitrary files or the kernel's separate discovery of native project Skills.
- `tools` and `disallowedTools` refer to the selected kernel's public tool names
  or supported patterns. They can narrow tool access but cannot grant protected
  industrial mutation rights. The Broker, Runtime and approval policy remain
  authoritative. These initial Pack roles do not impose kernel-specific names.
- `subagents` references role IDs resolved by Harness, including supported
  built-in roles. Pack instructions cannot create their own delegation loop or
  bypass the host's subtask lifecycle. The initial roles leave delegation at
  the host default.

Harness appends these role instructions to its own base prompt and scope. A
Pack does not supply credentials, choose a project path, change the kernel or
model, disable verification, or override authorization. Disabled Packs and
unsupported execution platforms do not become executable by selecting a role.

## Validation

`npm test` checks unique role identity, Pack/domain membership, Skill references,
source/catalog consistency, defensive copies and resource path, size, symlink
and content integrity failures. `npm run lock:check` checks that the content
lock and package file inventory include the maintained Markdown resources.
These are contract checks; they do not establish new native qualification or
measure the model's performance when following a role.
