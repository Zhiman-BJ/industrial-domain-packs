# Imported source and distribution boundaries

| Component | Source | Terms and notices |
| --- | --- | --- |
| EDA Harness source and educational fixtures | Public Chip Pack in Industrial Agent Harness commit `01d8ea2c01bdf3e3be858b1bcee0c29b174ad8c5` | Original EDA contributions use MIT; retain `packs/chip/eda-harness/LICENSE`, `UPSTREAM_PROVENANCE.md` and the imported file record |
| Chip Core adapter and MCP smoke script | Same public Harness commit | MIT; original notice retained in `licenses/industrial-agent-harness.MIT` |
| Python and optional npm dependencies | `uv.lock`, exported constraints and optional netlist-viewer package lock | Dependencies are installed separately and retain the licenses shipped in their packages |
| Native EDA tools and upstream container images | The inherited `Dockerfile.tools` and source scripts | Native tools, system libraries and upstream images retain their own terms; no binaries or built images are included in this source bootstrap |

Historical license and source details are retained in [Chip provenance](packs/chip/PROVENANCE.md). Public source redistribution does not relicense third-party software or authorize redistribution of customer inputs, PDKs or licensed assets. A future image publisher must preserve the applicable upstream notices and matching source/build information before distributing binary images.

PCB-bench private actor resources, customer designs and existing deployment data are outside this repository. The public catalog will add a domain only after its actual source and distribution boundary are established.

## Maintained multi-domain migration

Godot source/bridge, PCB public bridge, FreeCAD PR29 runtime, CAD/domain Skills and resource hash validation retain the Harness MIT notice. Exact imports are in provenance/domain-migration.json. PCB private actor is external. Godot, FreeCAD, KiCad, Verilator and their system/image dependencies retain their upstream licenses; this source repository does not bundle their executables. Verilator 5.026 official image is pinned by digest in the shared RTL recipe. No registry distribution of built binary images is part of this migration.

## CUDA adapter

The CUDA MCP transport, runtime client, Verifier and Skill are original MIT contributions. CUDA-Agent-Harbor is a private external execution dependency; no upstream implementation or task corpus is redistributed. Its source and worker image identities are recorded under `packs/cuda/locks/`. Native dependencies retain their own terms.

## Godot community Skills

The Godot Pack vendors two public MIT Skill sets as static knowledge content: GodotPrompter (commit `d68ea7f6e87163489af071e6440840ee105c4285`, notice in `licenses/godotprompter.MIT`) and haxqer/godot-skill (commit `037c03e9858ae748bf66c010aeb9942dbe9b93d6`, notice in `licenses/haxqer-godot-skill.MIT`). Exact import scope and adaptations are recorded in [Godot provenance](packs/godot/PROVENANCE.md). They add no runtime tools or native dependencies and do not widen the Pack's qualified acceptance path.

## Optional KiCad 3D model catalog

The PCB Pack includes a complete metadata catalog for the official KiCad 3D
library 10.0.7, commit `8d4070daa0fa3f7a7f3c176f4ca7bffc4df392be`. It preserves
exact original STEP paths and verifies each model's original size and content
identity. The upstream notice is retained in
`packs/pcb/resources/licenses/kicad-3d-LICENSE.md` (CC BY-SA 4.0 with the KiCad
libraries exception). Model binaries are not bundled; a compatible host fetches
only referenced models from the pinned official source. These models retain
their upstream terms and notices. See [PCB provenance](packs/pcb/PROVENANCE.md).

## Optional KLayout layout viewer Python

The Chip distribution 0.6.3 declaration references official CPython 3.12.15 /
Astral python-build-standalone 20261013 archives and KLayout 0.30.12 PyPI wheels.
This source package does not contain those binaries. CPython retains its PSF
License Version 2 and historical notices, pip retains MIT and its vendor notices,
and bundled native libraries retain their upstream terms. The PBS build
repository itself uses MPL-2.0; this must not be presented as a blanket runtime
license. KLayout's published package license is GPL-3.0-or-later. Preserve the
complete installed license trees and corresponding source/license references.
Exact fixed URLs, license metadata and artifact hashes are recorded in
`packs/chip/resources/klayout-viewer/assets.json` and
[Chip provenance](packs/chip/PROVENANCE.md).
