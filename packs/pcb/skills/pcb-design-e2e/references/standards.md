# Generic KiCad construction baseline

This environment follows KiCad's native project formats and command-line checks. The
baseline is deliberately factory-neutral; a manufacturer profile is not inferred.

- **Schematic:** resolve symbols and pin types, run KiCad ERC, export the native
  netlist, and compare component/value/footprint/pin membership against design intent and any compiled immutable
  predicates; uncompiled textual obligations remain UNKNOWN. Hierarchical sheets and missing dependencies remain explicit failures or
  unknowns.
- **PCB:** generate from the verified schematic, preserve net identities, inspect real
  pad anchors and board outline, then run KiCad DRC. DRC covers clearance, width,
  unconnected items, hole constraints and board-rule violations; it does not prove
  function, thermal safety or EMC compliance.
- **Delivery:** export Gerbers, drill files, BOM and placement data from the same
  revision and retain source hashes. A successful export is a packaging result, not a
  manufacturing or electrical approval.
- **Evidence:** every simulation or protocol result binds to the schematic/PCB hashes,
  input model hashes, conditions and captured output. Missing independent evidence is
  `UNKNOWN`, never an implied pass.

KiCad 10 references (use the frozen session's own version for legacy sessions):

- https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html (design rules and DRC)
- https://docs.kicad.org/10.0/en/eeschema/eeschema.html (schematic/ERC workflow)
- https://docs.kicad.org/10.0/en/cli/cli.html (native check/export commands)
- https://dev-docs.kicad.org/en/file-formats/sexpr-intro/ (pin/pad syntax and geometry)
- https://ngspice.sourceforge.io/docs.html (SPICE models, analyses and simulator documentation)

These references describe KiCad behavior only. They do not supply task answers,
commercial component facts, or factory-specific tolerances.
