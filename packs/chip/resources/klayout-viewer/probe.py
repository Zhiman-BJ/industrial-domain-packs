"""Read-only viewer installation probe; creates only temporary synthetic files."""
import os
import struct
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import klayout.db as db
import klayout.lay as lay

if db.__version__ != "0.30.12":
    raise RuntimeError("Unexpected KLayout viewer module version")

with tempfile.TemporaryDirectory(prefix="harness-klayout-viewer-probe-") as directory:
    gds = Path(directory) / "probe.gds"
    png = Path(directory) / "probe.png"
    layout = db.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("VIEWER_PROBE")
    top.shapes(layout.layer(1, 0)).insert(db.Box(0, 0, 1000, 1000))
    layout.write(str(gds))
    view = lay.LayoutView()
    try:
        view.load_layout(str(gds), True)
        view.add_missing_layers()
        view.max_hier()
        view.zoom_fit()
        view.save_image(str(png), 64, 64)
        header = png.read_bytes()[:24]
        if (len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n"
                or header[12:16] != b"IHDR"
                or struct.unpack(">II", header[16:24]) != (64, 64)):
            raise RuntimeError("KLayout viewer did not render the expected PNG")
    finally:
        del view

print("KLayout viewer PNG probe passed (0.30.12, 64x64)")
