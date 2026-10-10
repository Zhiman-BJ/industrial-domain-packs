# Managed native dependency declarations

Consumer release 0.4.1 extends the existing FreeCAD `runtimeAssets` declarations to the already qualified macOS Apple Silicon PCB and Godot profiles. A compatible Harness Pack Manager consumes these trusted declarations; this repository does not own installation UI, download execution, canonical readiness state or a second installer. The developer `setup-native.cjs` scripts remain optional diagnostic entry points.

| Domain | Native archive | Download bytes | App payload bytes | Required executables |
| --- | --- | ---: | ---: | --- |
| CAD | FreeCAD 1.1.4 arm64 DMG | 649760538 | 2625114576 | `freecadcmd` |
| PCB | KiCad 10.0.6 universal DMG | 1404303659 | 5104942382 | `kicad-cli`, bundled `python3.9` with pcbnew |
| Godot | Godot 4.7.2 stable universal ZIP | 170622178 | 353682925 | `Godot` |

All exact official HTTPS URLs and SHA-256 digests are in the host manifests. On 2026-10-09 the three existing official archives were independently hashed and their byte sizes checked. KiCad and FreeCAD app sizes were measured from read-only official DMG contents; Godot's app size matches its ZIP entries. `installedSize` is the sum of regular-file bytes and symbolic-link target-text bytes without following links. It describes the app payload, not exact filesystem allocation, free-space admission, temporary extraction space, the retained download cache or project outputs. Harness should label displayed installed sizes accordingly.

`app` is the installed bundle basename. `archiveApp` identifies its bounded location inside the archive (`KiCad/KiCad.app` in the KiCad DMG, `Godot.app` in the Godot ZIP). `executable` and each `environmentExecutables` value are relative to the installed app. The primary `environment` mapping is unchanged; KiCad additionally maps `INDUSTRIAL_HARNESS_KICAD_PYTHON` to `Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9`. This makes both native entry points available through the shared manager without user shell or environment setup. Version probes use `--version`; FreeCAD retains its existing compatibility flag and private profile directories.

The original KiCad and Godot bundles passed `codesign --verify --deep --strict`, and their real version commands returned 10.0.6 and 4.7.2 stable official. Manifest tests enforce agreement with the existing qualified dependency locks, required executable mappings, download sizes and unchanged platform boundaries. These checks validate source declarations and original native bundles; the updated Harness consumer must separately exercise real managed install, cancellation, repair, restart and native task flows. They do not publish an installer or qualify a new platform or engineering task.

Chip has no managed native asset recipe in this release. Local RTL verification requires Python 3.13, Verilator and a C++ toolchain; broader EDA flows need their own tools, images and PDK. Pack presence must not be presented as complete EDA readiness. CUDA remains a developer remote-service client requiring both authenticated role-specific MCP endpoints and a matching fixed reference model. Remote clients need no local GPU, while service provisioning and credentials remain administrator prerequisites. CUDA keeps an empty `qualifiedBundlePlatforms` list and does not enter automatic desktop bundles. Godot export templates and AutoCAD are also outside these managed declarations.

The execution scope remains the previously qualified FreeCAD bounded modelling, KiCad rectangular mounting-board edits/DRC/geometry and Godot structural scene checks. There is no new remote, Linux, Windows, Intel Mac, full PCB manufacturing or unrestricted game QA qualification.
