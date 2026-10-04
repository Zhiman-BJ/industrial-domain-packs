# FreeCAD native Domain Pack

Harness-owned fixed FreeCAD 1.1.4 bridge: parametric recipes, constrained rectangle/circle sketches, pads, holes, boxes, cylinders and boolean solids. Shared Desktop/CLI Runtime tools preserve actions, artifacts and separate-process readback verification. Native execution is qualified on macOS arm64 only.

Install upstream FreeCAD separately and set `INDUSTRIAL_HARNESS_FREECAD_CMD` if it is outside `/Applications`. This Pack does not bundle FreeCAD, Python macros, paid services or an independent MCP execution server. Geometry validation does not establish strength or manufacturability.

See [integration and limits](../../doc/freecad-domain-pack.md). Run native acceptance with:

```bash
node --test tests/integration/freecad-runtime.test.cjs
pnpm --filter @industrial-agent-harness/desktop test:cad
```

`scripts/setup-freecad.cjs` prepares the pinned official runtime for macOS arm64 CI. Its upstream download SHA and provenance are recorded in the integration guide.
