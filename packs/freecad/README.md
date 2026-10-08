# FreeCAD native Domain Pack

Harness-owned fixed FreeCAD 1.1.4 bridge: parametric recipes, constrained rectangle/circle sketches, pads, holes, boxes, cylinders and boolean solids. Shared Desktop/CLI Runtime tools preserve actions, artifacts and separate-process readback verification. Native execution is qualified on macOS arm64 only.

Versioned edits change known parameters or feature dimensions, profile and origin from a hash-bound recipe. Originals remain intact. Outputs include BREP and source-bound native sketch companions for the official OCCT AIS/V3d WebGL2 Viewer. A separate FreeCAD process reopens the saved FCStd and exports actual geometry, constraint references/dimensions and fully-constrained status, not recipe-derived display guesses. The Viewer is bundled with Desktop separately from this native Pack.

Install upstream FreeCAD separately and set `INDUSTRIAL_HARNESS_FREECAD_CMD` if it is outside `/Applications`. This Pack does not bundle FreeCAD, Python macros, paid services or an independent MCP execution server. Geometry validation does not establish strength or manufacturability.

See [integration and limits](../../doc/freecad-domain-pack.md). Run native acceptance with:

```bash
node --test tests/integration/freecad-runtime.test.cjs
pnpm --filter @industrial-agent-harness/desktop test:cad
```

`scripts/setup-freecad.cjs` prepares the pinned official runtime for macOS arm64 CI. Its upstream download SHA and provenance are recorded in the integration guide.

Apple Silicon packaged Desktop offers this Pack on first launch and automatically prepares the pinned official FreeCAD. Settings → Domains supports readiness and repair. Development command overrides remain supported outside the managed install. See [installation flow](../../doc/macos-cad-distribution.md).
