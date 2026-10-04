"""Executed by KLayout, with a generated job.json; contains no design or PDK data."""

import json
from pathlib import Path

import pya

job = json.loads(Path("job.json").read_text())
tech = pya.Technology()
tech.load(job["technology"])
options = tech.load_layout_options
lefdef = options.lefdef_config
lefdef.lef_files = job["lefs"]
lefdef.dbu = job["dbu"]
options.lefdef_config = lefdef
layout = pya.Layout()
layout.read(job["layout"], options)
top = layout.cell(job["top"])
if top is None:
    raise ValueError("DEF top cell missing")
original_dbu = layout.dbu
seen = set()
for file in job["libraries"]:
    library = pya.Layout()
    library.read(file)
    names = {cell.name for cell in library.each_cell()}
    if seen & names:
        raise ValueError("Ambiguous cell definitions across GDS libraries")
    seen |= names
    for cell in list(layout.each_cell()):
        if cell.name in names and cell.cell_index() != top.cell_index():
            cell.clear()
            cell.copy_tree(library.cell(cell.name))
empty = [layout.cell(i).name for i in top.called_cells() if layout.cell(i).is_empty()]
if empty or layout.dbu != original_dbu:
    raise ValueError(f"Unresolved cells or changed physical scale: {empty}")
bbox = top.dbbox()
output = pya.SaveLayoutOptions()
output.select_cell(top.cell_index())
layout.write("layout.gds", output)
check = pya.Layout()
check.read("layout.gds")
checks = {"top_present": check.cell(job["top"]) is not None, "dbu_preserved": check.dbu == original_dbu}
checks["bbox_preserved"] = checks["top_present"] and check.cell(job["top"]).dbbox() == bbox
checks["nonempty_geometry"] = not bbox.empty()
Path("streamout.json").write_text(json.dumps(checks))
