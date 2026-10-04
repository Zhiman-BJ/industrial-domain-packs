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
