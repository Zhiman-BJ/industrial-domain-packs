# FreeCAD native Domain Pack

Harness-owned fixed FreeCAD 1.1.4 bridge: parametric recipes, constrained rectangle/circle sketches, pads, holes, boxes, cylinders and boolean solids. Shared Desktop/CLI Runtime tools preserve actions, artifacts and separate-process readback verification. Native execution is qualified on macOS arm64 only. The Ubuntu Linux/amd64 profile below is qualification-pending; its presence does not advertise remote or Tencent AGS support.

Versioned edits change known parameters or feature dimensions, profile and origin from a hash-bound recipe. Originals remain intact. Outputs include BREP and source-bound native sketch companions for the official OCCT AIS/V3d WebGL2 Viewer. A separate FreeCAD process reopens the saved FCStd and exports actual geometry, constraint references/dimensions and fully-constrained status, not recipe-derived display guesses. The Viewer is bundled with Desktop separately from this native Pack.

A compatible Harness Pack Manager prepares the pinned official FreeCAD application on macOS Apple Silicon. For standalone development or older consumers, install upstream FreeCAD separately and set `INDUSTRIAL_HARNESS_FREECAD_CMD` if it is outside `/Applications`. This Pack does not bundle FreeCAD, Python macros, paid services or an independent MCP execution server. Geometry validation does not establish strength or manufacturability.

See [integration and limits](../../doc/freecad-domain-pack.md). Run native acceptance with:

```bash
node --test tests/integration/freecad-runtime.test.cjs
pnpm --filter @industrial-agent-harness/desktop test:cad
```

`scripts/setup-freecad.cjs` prepares the pinned official runtime for macOS arm64 CI. Its upstream download SHA and provenance are recorded in the integration guide.

Apple Silicon packaged Desktop offers this Pack on first launch and automatically prepares the pinned official FreeCAD. Settings → Domains supports readiness and repair. Development command overrides remain supported outside the managed install. See [installation flow](../../doc/macos-cad-distribution.md).

## Automatic result presentation

The 1.1.4-pack.5 production path declares model/export/preview relationships and independent diagnostics. Input snapshots bind edits and inspection reports to an exact source version. Failed/cancelled operations expose only actual readable output; they do not replace a successful model. Harness contract presentation schema 1 is optional for older consumers, whose existing Artifact references remain valid.

Distribution 1.1.4-pack.7 combines these result declarations with the managed native installation recipe and measured footprint from pack.6. The Runtime sources remain identical to pack.5; installation metadata does not change geometry or verification semantics.

Distribution 1.1.4-pack.8 makes the user-path containment check case-insensitive on macOS, where the default filesystem resolves directory names in any casing while `realpath` keeps the caller's spelling. Registered project paths whose case differs from the on-disk entry no longer fail every action with "FreeCAD user paths escaped the Action directory". Linux comparisons stay exact, and genuine escapes (including through symlinks) remain rejected.

Distribution 1.1.4-pack.9 preserves a snapshot of validated `inputs.expect` in the
local execution result for build, inspect, export and edit. The shared verifier
reads `result.expect`, including volume, solid count, bounds and tolerance, so the
remote report-contract migration no longer silently drops local requirements.
`node --test tests/freecad-local-runtime.test.cjs` at the repository root exercises
these production execute/verifier paths with synthetic native-process output;
the native acceptance commands above remain separate.

## Linux qualification-pending profile

`freecad-linux-cpu` pins Ubuntu 24.04 (`linux/amd64`) and the official FreeCAD 1.1.4 Linux x86_64 AppImage. The image build verifies the AppImage SHA-256 before extraction and starts the AGS `envd` daemon beside the narrow snapshot worker. The image and profile remain qualification-pending until a real Linux worker invocation, independent readback and protected service adapter pass the remote qualification gate.
