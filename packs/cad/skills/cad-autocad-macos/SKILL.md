---
name: cad-autocad-macos
description: Driving AutoCAD on macOS through the computer-use plugin: LISP entmake geometry, interactive DIMLINEAR dimensions with %%c text, GUI-only save dialogs for DWG/DXF export, /-to-: path-mangling workarounds, codepage semantics, and a table of verified dead ends. Use when the task requires AutoCAD itself — DWG delivery, the user's live session, or LISP execution. For plain 2D DXF deliverables prefer direct ezdxf authoring (cad-ezdxf).
---

# AutoCAD on macOS: operating traits

Scope: AutoCAD for Mac (verified on the 2027 release, simplified-Chinese UI),
driven through the computer-use plugin. Read the plugin's general
computer-use skill first — focus, IME, and dialog rules apply here as-is;
this file covers only what is specific to AutoCAD on the Mac.

## Route first: is AutoCAD even the right tool?

The deliverable-routing table lives in cad-intent-loop. Default for
producing a 2D drawing file: author DXF directly with ezdxf (cad-ezdxf,
the verified main path). Open AutoCAD only when:
- the deliverable must be authored/saved by AutoCAD itself (DWG exit),
- the work happens in the user's live drawing/session (their styles,
  template, manual edits),
- or a hand-edit must be absorbed in place.
Opening AutoCAD for a from-scratch 2D deliverable is the slow path —
expect roughly an order of magnitude more verify rounds than direct
authoring.

## Platform facts (verified)

- No COM, no .NET, no `accoreconsole`. The ONLY automation surfaces are:
  the GUI (toolsets/menus/dialogs), the command line (text input),
  AutoLISP (`(load ...)`), and .scr script files.
- Start from a blank doc using `acadiso.dwt` (mm); the exported DXF should
  carry `INSUNITS=4` — worth checking in whatever readback you use.

## Proven working pipeline

0. **Loading a file: `open -a`, never the open dialog** (verified):
   `open -a "AutoCAD 2027" /abs/path/file.dxf` puts the file straight into
   the app. Driving NSOpenPanel with computer-use is a verified dead end
   (see table).
1. **Geometry via LISP `entmake`** (NOT via GUI clicks): one .lsp file that
   writes all circles/lines/text, with a log file (`open ... "w"`,
   write-line START/END). Load via the command line with an absolute POSIX
   path: `(load "/path/to/file.lsp")`. A security sheet appears
   (文件加载 - 安全问题) → click 加载 → wait → read the log file.
   `entmake` is fine for CIRCLE, LINE, TEXT, MTEXT. Layers: pre-create with
   scripted `-LAYER`; `entdel` removes objects; `setvar` for cmdecho/filedia.
2. **Dimensions via INTERACTIVE `DIMLINEAR`**, one at a time, from the
   command line (never inside LISP):
   - Type `DIMLINEAR`, Enter, then the first point as `X,Y`, Enter, second
     point, Enter, then the dimension-line location.
   - At the dimension prompt press `T` (text option), then type `%%c120`-
     style text. **One prompt per type_text buffer, zoom-verify after every
     step** — a single type_text spanning multiple prompts makes the greedy
     text prompt swallow the later input.
   - AutoCAD recomputes group 42 (actual measurement) from the two points —
     use the TRUE endpoints of the diameter (e.g. (-60,0)→(60,0) for Ø120)
     and the measurement is right by construction.
3. **Fix up layers of interactive objects**: interactive commands create
   objects on the current layer (often 0). Move them with LISP:
   ```lisp
   (entmod (append (vl-remove-if '(lambda (x) (= (car x) 8)) (entget e))
                   (list (cons 8 "DIM"))))
   ```
4. **Save via the GUI dialog, never via the command line** (see dead ends):
   `Cmd+Shift+S` → dialog opens pre-filled with the current doc name and
   last-used directory → `set_value` the 保存为 field with the BARE FILENAME
   → pick file type from the PopUpButton (verify it changed) → 保存 →
   替换 if prompted. Repeat for the second format. DWG: `AutoCAD 2018
   图形 (*.dwg)`; DXF: `AutoCAD 2018 DXF (*.dxf)` (= AC1027).
   Note: after saving as DXF the CURRENT DOCUMENT becomes the .dxf —
   subsequent saves keep working from the same dialog.
5. **Zoom to extents before screenshot**: command line `ZOOM` Enter `E`
   Enter (short commands survive focus flakiness better).

## Dead ends — do NOT retry

| Approach | Outcome |
|---|---|
| `(command "DIMLINEAR" ...)` / `DIMALIGNED` inside LISP | hangs forever (repeatable command never terminates) |
| `entmake` of DIMENSION entities | group 42 silently recomputed from defpoints by AutoCAD; text comes out as plain number without `%%c`. Unfixable from LISP (`entmod` on dim text silently fails) |
| Hand-written DIMSTYLE via entmake | "参数太多" (too many parameters) error; cloning STANDARD's table record also fails |
| `entmakex` LAYER table record **without a 330 owner handle** | silently returns nil, no error, no log progress — layer is NOT created |
| Command-line `CD` / `._CD` / `._-saveas` / `_SAVEAS` with full paths | wrong command names or the path's `/` → `:` conversion produces malformed literal filenames like `:Users:foo:bar.dxf` |
| `DXFOUT` typed on the command line (full or bare path) | swallowed / no-op in this environment; the GUI dialog is the reliable exporter |
| `entmod` to change a DIMENSION's text | silent no-op |
| Driving the open dialog (NSOpenPanel) with computer-use | foreground drops mid-dialog; Cmd+Shift+G and typed paths ignored; file-type menu closes on open. Load via `open -a "AutoCAD 2027" <abs path>` from the shell instead |
| DWG export via libredwg 0.14 `dwgwrite` | writes corrupt DWG: coordinates as -1e20, CJK mojibake, round-trip loses all entities (`Duplicate handle`, MATERIAL/MLEADERSTYLE unsupported). Verified DWG exit = GUI Save As; ODA converter untested |

## Working LISP patterns

- Scripted layer creation (terminates cleanly, unlike interactive `LAYER`):
  ```lisp
  (command "._-LAYER" "_New" "DIM" "")
  ```
- Log discipline in every LISP: `open ... "w"`, write-line START / each
  stage / END. **A log stuck at START = script aborted; debug, don't assume.**
- Object counting: `(ssget "X" '((0 . "DIMENSION")))` + `sslength`.
- Wrap risky calls in `vl-catch-all-apply` and log the error value.
- Keep LISP files pure ASCII (command-line `load` of paths with `/` is fine;
  file *contents* with GBK/Chinese strings caused problems — build Chinese
  TEXT entities in the same LISP via `(strcat ...)` of plain characters, or
  create such text via the GUI).

## Export / codepage semantics (verified)

- `%%c` survives DXF export verbatim (readback sees `%%c120`; render as Ø).
- **UTF-8 `×` is LOST** on export through the ANSI_936 codepage: text
  entered as `4×%%c12` exports as `4%%c12`. If a spec demands `×`, enter a
  character the codepage carries (e.g. `X`) or adjust the spec with a
  documented reason.
- Reading back with ezdxf: `d.get_geometry_block()` on the DXF DIMENSION
  yields the TEXT/MTEXT entities directly — no `render()` rebuild needed
  for AutoCAD-produced DXF.
- Group 42 = AutoCAD's recomputed actual measurement; group 10/11 defpoints
  include extension lines, so do NOT compare "measurement-line endpoint
  distance" naively — compare the TEXT NUMBER against group 42 instead.
- `×` survives DWG round-trips (DWG is Unicode): SaveAs-DWG from a UTF-8 DXF
  keeps `4×%%c12` verbatim. Only ASCII DXF export through the codepage eats it.
- DXF produced by converters (libredwg dwg2dxf, ODA) can fail plain
  `ezdxf.readfile` (missing EOF, odd attributes such as CIRCLE carrying
  `insert`); parse with `ezdxf.recover.readfile`.

## Dialog anatomy (simplified-Chinese UI)

- Save dialog title: `图形另存为`. Fields: 位置 (PopUpButton showing folder
  name), 保存为 (TextField), 标签 (TextField), 文件类型 (PopUpButton),
  取消/保存.
- The 文件类型 PopUpButton: click → `get_app_state` with a query (e.g.
  DXF or DWG) lists MenuItems → click target → **re-read the PopUpButton
  value** (first click occasionally doesn't stick).
- Replace sheet title fragment: `你要替换它吗？`, button label 替换.
- File-panel buttons: act via element-id press (AXPress), not coordinate
  clicks — panel clicks frequently land in "background" once the app has
  dropped foreground, while AXPress still reaches the button.
- Every LISP `load` triggers a separate `文件加载 - 安全问题` sheet
  (加载 / 不加载) — handle it each time.
- Stuck command-line prompt recovery: Enter on an object-selection prompt
  ends the loop; ESC often does not reach it.

## Session gotchas

- Switch the input source to English (e.g. ABC) before any command-line or
  type_text input: a Chinese IME state corrupts commands and `%%c` text.
- AutoCAD frequently drops foreground after a single keystroke sequence;
  `activate_app` before EVERY typing burst.
- The command line sits at the bottom of the main window — re-derive its
  click point from the screenshot mapping rather than hard-coding
  coordinates.
- The tab bar shows the doc name with `*` for unsaved changes — a quick
  `get_app_state` top-level check tells you whether a save actually
  committed (asterisk disappears).
