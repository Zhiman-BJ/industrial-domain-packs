# KiCad Python compatibility

- Check CLI and Python-binding versions before use. File format acceptance is a runtime fact, not guaranteed by a generator_version string.
- Create all nets before traversing the pcbnew net map; mutation during traversal can invalidate SWIG iterators.
- Add a footprint to its owning board before `Flip`; KiCad 9 needs the board's layer map and can segfault on a detached footprint. Set the final rotation after flipping.
- KiCad 9 via width APIs may require an explicit layer. Keep Python/C++ object ownership alive when assigning netclasses.
- CAD symbols may inherit from another symbol and have multiple units/body styles. Resolve inheritance before extracting pins. Unknown pins must fail generation; do not silently skip them.
- Use a strict s-expression parser for structure, not an indentation regex. Quoted values require escaping.
- Zone fill may crash particular headless builds. Run it in a subprocess and replace the source board only after successful fill/save. DRC/export does not establish that saved zones were filled unless verified. Zone outlines/bounding boxes never prove continuity.
- Use real copper layers and via layer spans. Tracks at the same XY on different layers do not connect without a suitable interconnect. Net names alone do not prove connectivity.
- Read DRC JSON item IDs/positions for repair. Calculate totals before limiting examples. Remove stale outputs before invoking tools; command failure must be UNKNOWN, not a clean old report.
- Footprint assignment is an electrical mapping operation. Preserve physical pad numbering; explicitly map symbol pins and validate every mapped pad.
