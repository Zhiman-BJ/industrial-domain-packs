---
name: pcb-design-e2e
description: Design and repair KiCad projects with the fixed PCB Bench public tools, complete engineering references and native evidence.
---

This repository-owned entry registers the PCB design Skill. At session creation,
the Harness loads the complete verified `pcb-design-e2e` resource directory from
`INDUSTRIAL_HARNESS_PCB_BENCH_DIR`, including references and assets. Missing or
changed resources are an explicit setup error, never a fallback to this stub.

Use scoped `pcb.bench.*` tools through `domain_tool_list`, `domain_tool_describe`
and `domain_tool_call`. Native actions and verification belong to the PCB Domain
Runtime. Viewer output and successful processes cannot establish acceptance.
