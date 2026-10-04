"""KLayout batch script: merge routed DEF with standard-cell GDS geometry."""

import json
import os
from pathlib import Path

import pya

src = Path(os.environ["EDA_INPUT_DIR"])
deps = Path(os.environ["EDA_DEPS_DIR"])
tech = pya.Technology()
tech.load(str(src / "platform/FreePDK45.lyt"))
options = tech.load_layout_options
lefdef = options.lefdef_config
lefdef.lef_files = [
    str(src / "platform/lef/NangateOpenCellLibrary.tech.lef"),
    str(src / "platform/lef/NangateOpenCellLibrary.macro.mod.lef"),
]
lefdef.dbu = 0.0005
options.lefdef_config = lefdef
layout = pya.Layout()
layout.read(str(deps / "physical.route/layout.def"), options)
top = layout.cell(os.environ["EDA_TOP"])
if top is None:
    raise ValueError("DEF top cell missing")
library = pya.Layout()
library.read(str(src / "platform/gds/NangateOpenCellLibrary.gds"))
original_dbu = layout.dbu
standard_cells = {cell.name for cell in library.each_cell()}
for cell in list(layout.each_cell()):
    if cell.name in standard_cells and cell.cell_index() != top.cell_index():
        cell.clear()
        cell.copy_tree(library.cell(cell.name))
# copy_tree rescales library geometry into the DEF database units. Reading a
# second file into the same Layout overwrites dbu and corrupts routed coordinates.
assert layout.dbu == original_dbu, "GDS merge changed DEF physical scale"
empty_cells = [layout.cell(i).name for i in top.called_cells() if layout.cell(i).is_empty()]
if empty_cells:
    raise ValueError(f"Unresolved standard cells: {empty_cells}")
expected_bbox = top.dbbox()
expected_dbu = layout.dbu
output = pya.SaveLayoutOptions()
output.select_cell(top.cell_index())
layout.write("routed.gds", output)
check = pya.Layout()
check.read("routed.gds")
assert check.cell(os.environ["EDA_TOP"]) is not None
assert check.dbu == expected_dbu
assert check.cell(os.environ["EDA_TOP"]).dbbox() == expected_bbox
Path("streamout-checks.json").write_text(
    json.dumps(
        {
            "checks": [
                {"name": "GDS round-trip contains design top", "passed": True},
                {
                    "name": "DEF database units preserved during library merge",
                    "passed": layout.dbu == original_dbu,
                },
                {"name": "All referenced standard cells have geometry", "passed": not empty_cells},
            ]
        }
    )
)
