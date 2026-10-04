---
name: cad-intent-loop
description: The intent-layer workflow for CAD tasks, dimension-neutral — spec as the single source of truth, artifacts regenerated rather than hand-patched, semantic diff to absorb user edits (intent vs appearance classification), silent-overwrite guard, programmatic acceptance gates that must be provably able to fail, evidence-chain reporting, and deliverable routing (2D DXF → ezdxf, FCStd/STEP → FreeCAD, DWG → AutoCAD). Use when iterating any CAD deliverable across turns, absorbing user edits, or deciding which tool to route a deliverable to.
---

# CAD intent loop: spec → artifact → absorb → redeliver

Architecture (field-verified): design intent lives in a spec — for 2D drawings
an external spec.json; for parametric models the FCStd sketch/constraints
themselves. Generated files are regenerable artifacts; CAD apps are
display/delivery only. Full regeneration from spec beats surgical file
edits — cheap, verifiable, no drift.

## Route by deliverable, not by tool

| Deliverable | Carrier | Tool skill |
|---|---|---|
| 2D drawing file | DXF | cad-ezdxf (direct authoring — the default, fastest path) |
| Parametric 2D sketch the user keeps editing in FreeCAD | FCStd | cad-freecad-headless |
| 3D model | FCStd + STEP | cad-freecad-headless |
| DWG-native deliverable, or the user's live drawing session | DWG | cad-autocad-macos |

## Acceptance — always programmatic

Read the final artifact back and score it against the spec; never eyeball,
never trust the build script's claims about what it produced. Judge the
geometry that was read back. Every check must be provably able to fail — run
it once against a known-bad design and watch it FAIL; a check that cannot
fail is decoration. Cap fix-rerounds (e.g. 5) and archive every FAIL output.

## Absorbing user edits (the diff loop)

1. **BEFORE any regeneration**: semantic-diff the last generated artifact
   against the current file (2D DXF: `scripts/dxf_diff.py` in cad-ezdxf).
   Any unexpected diff = user edit or corruption — **NEVER regenerate
   silently**. Silent overwrite of user work is the worst failure mode of
   this architecture.
2. Classify every diff: `intent` (patch the spec) / `appearance` (ignore) /
   `ambiguous` (ask the user; record the options). Appearance-level diffs
   must never enter the spec.
3. **Semantic lift**: four holes rigidly rotated = patch
   `bolt_start_angle_deg`, NOT four hardcoded coordinates. Parameterize the
   pattern, not the instance.
4. Hand-edits are often internally inconsistent (a moved hole with its
   centerline left behind; an enlarged circle with stale dimension text).
   Detect dangling references — a centerline pointing at geometry that
   moved, a dimension whose measurement no longer matches — and surface
   them as must-ask ambiguities. Regeneration fixes them from spec;
   disclose the fix.
5. In parametric carriers (FCStd) the user edits parameters/constraints
   directly; the next readback absorbs those edits — the diff loop above
   applies to exported or non-parametric artifacts.
6. Archive `spec_before.json` / `spec_after.json` and per-round verify
   output.

## Evidence chain

Every claim in a final report links the exact command and the raw output
file that backs it. A claim without archived evidence is treated as false —
fabricated success gets caught exactly this way.
