"""Repair the pinned IHP LEC export; never remove functional standard cells."""
from pathlib import Path

config = Path("/OpenROAD-flow-scripts/flow/platforms/ihp-sg13g2/config.mk")
original = 'export REMOVE_CELLS_FOR_LEC ?= "bondpad_70* sg13g2*"'
text = config.read_text()
if text.count(original) != 1:
    raise SystemExit("Pinned ORFS IHP LEC export changed; review the patch before building")
config.write_text(text.replace(original, 'export REMOVE_CELLS_FOR_LEC ?= "bondpad_70*"'))
