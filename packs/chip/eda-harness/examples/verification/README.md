# Independent extraction, LVS, and waveform tools

Run with the tools image built from the repository's `Dockerfile.tools`:

```sh
uv run eda --project examples/verification run-until verification --wait
```

`layout.extract` runs Magic with the bundled SCMOS teaching technology on an actual
MOS layout. `verify.netgen` compares that extracted SPICE netlist against the independently
written reference. Only Netgen's explicit unique-match result (`1`) passes; an empty,
ambiguous, port-mismatched, or property-mismatched comparison does not pass.
`debug.waveform` converts an input VCD to FST, reads it back, loads the FST in GTKWave
under a temporary virtual display, and saves a signal-selection session. The waveform
is a fixed test input, not a claim of simulation coverage for this device.

The saved `wave.gtkw` and `wave.fst` can be opened together in a desktop GTKWave installation:

```sh
gtkwave wave.fst wave.gtkw
```

The container action is batch processing; it does not expose a remote desktop or
install a macOS GUI. Keep the wave file alongside the session when downloading artifacts.

SCMOS is not Nangate45. This example validates the Magic/Netgen tool path, not Nangate45
foundry signoff. For another process, replace the layout, Magic technology and extraction
settings, reference netlist, and Netgen setup together. Declare each technology/setup file
in `inputs` and its action's `input_types` so cache keys track it. The existing KLayout
Nangate45 DRC/LVS workflow is retained.
