#!/bin/sh
set -eu
cp "$EDA_INPUT_DIR/waves/counter.vcd" wave.vcd
vcd2fst wave.vcd wave.fst
fst2vcd wave.fst > roundtrip.vcd
# Virtual display provides the same GUI loader as interactive GTKWave.
GSETTINGS_BACKEND=memory xvfb-run -a gtkwave --script "$EDA_INPUT_DIR/scripts/wave.tcl" wave.fst
