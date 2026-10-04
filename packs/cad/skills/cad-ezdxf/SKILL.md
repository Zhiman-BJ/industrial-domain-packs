---
name: cad-ezdxf
description: Direct 2D DXF authoring and verification with ezdxf — the default route for 2D drawing deliverables. Dimstyle traps (EZDXF default dimlfac=100, dimdec), %%c text anchor-matching, × lost through the ANSI_936 codepage, the semantic dxf_diff script, and delivery conversion (vector PDF; DWG only via AutoCAD GUI — libredwg writes corrupt files). Use when authoring, verifying, diffing, or converting 2D DXF files.
---

# ezdxf: direct 2D DXF authoring (operating traits)

The default route for 2D drawing files: author the DXF directly from the
spec instead of driving a CAD app. The workflow (spec → artifact → absorb →
verify) is defined in cad-intent-loop; this file carries the ezdxf-specific
operating knowledge.

## Verify gate contents (2D)

`verify.py` (task-side) reads the final DXF back and scores against the
spec: units, layers, circles by layer/diameter/center, bolt patterns
(radius, spacing, start angle), dimension actual measurements AND display
texts, title texts, centerline counts. exit 1 = fix loop (see
cad-intent-loop for the gate rules).

Traps already baked into the gate — do not re-introduce:
- radius/diameter ×2 semantics: read back radius×2 and compare against the
  nominal diameter;
- display text vs actual measurement: check BOTH (they can diverge
  silently);
- ezdxf's built-in "EZDXF" dimstyle carries `dimlfac=100` — override
  `dimlfac=1` or every dimension text is ×100;
- `dimdsep`/`dimdec`: integer mm drawings want `dimdec=0`;
- Ø is `%%c` in DXF text; anchor-match text by ENDING (a substring check
  passes on `%%c12000` when you expect `%%c120`);
- AutoCAD ASCII DXF export loses `×` through the ANSI codepage (survives
  DWG round-trips — DWG is Unicode).

## Semantic diff: `scripts/dxf_diff.py`

Field-tested semantic differ for the absorb loop (cad-intent-loop):
clusters CIRCLE by (layer, center, diameter), LINE by (layer, endpoint
set, linetype), TEXT by (content, position), DIMENSION by (measurement,
display text); TOL 1e-3; pairs by entity handle first (same handle
modified = strongest evidence). Same-reader caveat: both sides parsed
with ezdxf — trusted, not independent; for delivery-grade assurance add
one cross-tool readback.

## Delivery conversion

- **PDF**: ezdxf drawing pipeline (`Frontend` + `MatplotlibBackend`,
  `PdfPages`) — vector output, verified.
- **DWG**: NO headless path verified. libredwg 0.14 `dwgwrite` writes
  corrupt DWG (coordinates as −1e20, CJK mojibake) — do not use. Verified
  path: AutoCAD GUI Save As (see cad-autocad-macos: `open -a` to load,
  AXPress the save button, bare filename via `set_value`). ODA File
  Converter untested.
- DXF→DWG round-trip is lossless for this pipeline's entities (verified;
  `×` and CJK survive); parse converter output with `ezdxf.recover.readfile`
  — converter DXF (libredwg dwg2dxf, ODA) can fail plain `ezdxf.readfile`
  (missing EOF, odd attributes such as CIRCLE carrying `insert`).
