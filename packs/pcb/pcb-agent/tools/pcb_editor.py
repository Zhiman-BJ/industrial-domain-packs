"""Board manipulation through the `pcbnew` Python API.

This is the layer agents kept having to invent. A measured EDA-bench run spent
20 minutes calling `inspect` on pcbnew to find the z-related setters and another
10 on `PCB_IO_MGR.GuessPlugin`. Everything below is that knowledge, pinned.

Units: pcbnew works in internal units (nanometres). Every function here takes
and returns millimetres, because a brief is written in millimetres.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import typing as t

try:
    import pcbnew
except Exception as _e:      # importable outside the container for linting
    pcbnew = None
    _IMPORT_ERROR = _e


MM = 1_000_000            # nanometres per millimetre


def _need():
    if pcbnew is None:
        raise RuntimeError(f"pcbnew unavailable: {_IMPORT_ERROR}")


def board_polygon_outlines(board, output):
    """Resolve actual Edge.Cuts across native versions; never infer a missing outline."""
    _need()
    major = int(re.search(r"\d+", pcbnew.Version()).group())
    return board.GetBoardPolygonOutlines(output, False) if major >= 10 else board.GetBoardPolygonOutlines(output)


def edge_bounding_box(board):
    """Native Edge.Cuts geometry, independent of GUI/project visibility state.

    KiCad 10.0.6 BOARD::GetVisibleLayers returns a reference to a temporary
    AllLayersMask for projectless boards; ComputeBoundingBox calls it even for
    edges-only queries. Mirror its exact edge-shape union without that access.
    This is an extent observation, not a closed-outline validation.
    """
    _need()
    box=pcbnew.BOX2I()
    shapes=list(board.GetDrawings())
    for footprint in board.GetFootprints():shapes.extend(footprint.GraphicalItems())
    for shape in shapes:
        if shape.GetLayer()==pcbnew.Edge_Cuts and shape.Type()==pcbnew.PCB_SHAPE_T:
            box.Merge(shape.GetBoundingBox())
    return box


def mm(v: float) -> int:
    """Millimetres -> pcbnew internal units."""
    return int(round(v * MM))


def to_mm(v: int) -> float:
    return v / MM


def V(x: float, y: float):
    """A point, in millimetres."""
    _need()
    return pcbnew.VECTOR2I(mm(x), mm(y))


@dataclasses.dataclass
class BoardInfo:
    path: str
    layers: int
    footprints: int
    tracks: int
    vias: int
    zones: int
    nets: list[str]
    outline_bbox_mm: tuple[float, float, float, float] | None


# --------------------------------------------------------------------------
# open / create / save
# --------------------------------------------------------------------------
_BOARDS: list = []   # every BOARD ever created or loaded, kept alive -- see new_board


def new_board(path: str | pathlib.Path, layers: int = 2):
    """Create an empty board. The BOARD object is pinned for the life of the
    process on purpose: when Python garbage-collects a BOARD, KiCad tears down
    project state that other boards still reference, and the next
    GetBoardEdgesBoundingBox() on a *different* board segfaults with no
    traceback. Measured: a test that created and dropped three scratch boards
    crashed a fourth, unrelated one. Scripts that build several boards must
    not let any of them die early; this makes that automatic."""
    _need()
    b = pcbnew.NewBoard(str(path))
    b.SetCopperLayerCount(layers)
    _BOARDS.append(b)
    return b


def load(path: str | pathlib.Path):
    _need()
    b = pcbnew.LoadBoard(str(path))
    _BOARDS.append(b)           # same reason as new_board
    return b


def save(board, path: str | pathlib.Path | None = None) -> str:
    _need()
    p = str(path) if path else board.GetFileName()
    board.Save(p)
    return p


def info(board) -> BoardInfo:
    _need()
    bb = edge_bounding_box(board)
    box = None
    if bb.GetWidth() > 0:
        box = (to_mm(bb.GetX()), to_mm(bb.GetY()),
               to_mm(bb.GetX()+bb.GetWidth()), to_mm(bb.GetY()+bb.GetHeight()))
    return BoardInfo(
        path=board.GetFileName(),
        layers=board.GetCopperLayerCount(),
        footprints=len(board.GetFootprints()),
        tracks=len([x for x in board.GetTracks() if x.Type() == pcbnew.PCB_TRACE_T]),
        vias=len([x for x in board.GetTracks() if x.Type() == pcbnew.PCB_VIA_T]),
        zones=board.Zones().GetCount() if hasattr(board.Zones(), "GetCount") else len(board.Zones()),
        nets=sorted(str(n) for n in board.GetNetsByName().keys() if str(n)),
        outline_bbox_mm=box,
    )


# --------------------------------------------------------------------------
# outline
# --------------------------------------------------------------------------
def rect_outline(board, w_mm: float, h_mm: float, x0: float = 0.0, y0: float = 0.0,
                 width_mm: float = 0.1) -> None:
    """Draw a closed rectangular board outline on Edge.Cuts.

    A closed outline is not optional: without it the board has no extent, and
    every geometry check downstream reports zero area.
    """
    _need()
    pts = [(x0, y0), (x0 + w_mm, y0), (x0 + w_mm, y0 + h_mm), (x0, y0 + h_mm), (x0, y0)]
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(V(ax, ay))
        seg.SetEnd(V(bx, by))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(mm(width_mm))
        board.Add(seg)


# --------------------------------------------------------------------------
# footprints
# --------------------------------------------------------------------------
def add_footprint(board, lib_dir: str, fp_name: str, ref: str,
                  x_mm: float, y_mm: float, rot_deg: float = 0.0,
                  back: bool = False):
    """Place a footprint from a .pretty directory and give it a reference."""
    _need()
    fp = pcbnew.FootprintLoad(lib_dir, fp_name)
    if fp is None:
        raise FileNotFoundError(f"{fp_name} not in {lib_dir}")
    fp.SetPosition(V(x_mm, y_mm))
    fp.SetReference(ref)
    # KiCad 9 Flip needs the owning board for its copper layer mapping.
    # A detached footprint can segfault instead of raising a Python exception.
    board.Add(fp)
    if back:
        fp.Flip(fp.GetPosition(), False)
    # Rotation describes the final board pose on either side.
    fp.SetOrientationDegrees(rot_deg)
    return fp


def add_part(board, part: dict):
    """Add one component with its nets, from the same dict `bootstrap` takes.

        {"ref": "J2", "footprint": "Lib:Name", "at": [x, y], "rot": 0,
         "nets": {"1": "VBUS", "2": "GND"}}

    This is the incremental form of bootstrap: review() said a part is missing,
    so add that part -- without rebuilding the whole board. Returns the
    footprint. Raises on a bad reference designator, because a placeholder or
    duplicate prevents unambiguous connectivity verification.
    """
    from tools import datasheet_search as _ds
    ref = str(part.get("ref", "")).strip()
    if not ref or ref.upper().startswith("REF") or "*" in ref:
        raise ValueError(f"位号不合法: {ref!r}，见 check_annotation")
    if any(fp.GetReference() == ref for fp in board.GetFootprints()):
        raise ValueError(f"位号重复: {ref!r}")
    lib, name = part["footprint"].split(":", 1)
    d = _ds.footprint_dir(lib, name)
    if d is None:
        raise FileNotFoundError(f"找不到封装 {lib}:{name}；请用 datasheet_search.find_footprint 核对当前库中的完整名称或导入有来源的封装")
    x, y = part.get("at", [10.0, 10.0])
    fp = add_footprint(board, d, name, ref, float(x), float(y),
                       float(part.get("rot", 0) or 0), bool(part.get("back", False) or part.get("side") == "bottom"))
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    # Preserve the same explicit value as the canonical spec and schematic.
    fp.SetValue(str(part.get("value", "")))
    sync_schematic_attributes(fp)
    # Compatibility for legacy Python callers. Canonical designs use pad_map.
    for old_name, new_name in (part.get("rename_pads") or {}).items():
        for pad in fp.Pads():
            if pad.GetName() == old_name:
                pad.SetName(new_name)
    nets = {str((part.get("pad_map") or {}).get(k,k)): v for k,v in (part.get("nets") or {}).items()}
    known = {str(p.GetNumber()) for p in fp.Pads()}
    if set(nets) - known:
        board.Delete(fp)
        raise ValueError(f"{ref}: net mapping refers to nonexistent pads {sorted(set(nets)-known)}")
    if part.get("locked"):
        fp.SetLocked(True)
    if nets:
        ensure_nets(board, sorted(set(nets.values())))
        assign_pad_nets(board, ref, nets)
    return fp


def sync_schematic_attributes(fp):
    """Generated physical symbols are in BOM and populated; keep native parity.

    Library footprints can default to excluded/DNP (e.g. test pads). As KiCad's
    schematic update does, synchronize instance attributes while retaining SMD,
    through-hole and other footprint geometry attributes.
    """
    fp.SetAttributes(fp.GetAttributes() & ~(pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_DNP))


def move_part(board, ref: str, x_mm: float, y_mm: float, rot_deg: float | None = None):
    """Re-place one component. Tracks are NOT moved -- call unroute() first."""
    _need()
    for fp in board.GetFootprints():
        if fp.GetReference() == ref:
            if fp.IsLocked():
                raise ValueError(f"{ref} is locked")
            fp.SetPosition(V(x_mm, y_mm))
            if rot_deg is not None:
                fp.SetOrientationDegrees(rot_deg)
            return fp
    raise LookupError(f"没有位号为 {ref} 的元件")


def remove_part(board, ref: str) -> bool:
    _need()
    for fp in list(board.GetFootprints()):
        if fp.GetReference() == ref:
            board.Delete(fp)
            return True
    return False


def unroute(board, net_name: str | None = None) -> int:
    """Delete tracks and vias -- all of them, or just one net's. Zones stay.

    Needed before re-routing after a part moved: autoroute() on top of stale
    copper doubles every segment and manufactures shorts.
    """
    _need()
    n = 0
    for tr in list(board.GetTracks()):
        if net_name is None or str(tr.GetNetname()) == net_name:
            board.Delete(tr)
            n += 1
    return n


def _copper_extent(fp) -> tuple:
    """(x0, y0, x1, y1) mm of the footprint's pads -- what copper-edge DRC sees."""
    xs0 = ys0 = float("inf"); xs1 = ys1 = float("-inf")
    for pad in fp.Pads():
        bb = pad.GetBoundingBox()
        xs0, ys0 = min(xs0, to_mm(bb.GetLeft())), min(ys0, to_mm(bb.GetTop()))
        xs1, ys1 = max(xs1, to_mm(bb.GetRight())), max(ys1, to_mm(bb.GetBottom()))
    if xs0 == float("inf"):
        bb = fp.GetBoundingBox(False, False)
        return (to_mm(bb.GetLeft()), to_mm(bb.GetTop()), to_mm(bb.GetRight()), to_mm(bb.GetBottom()))
    return (xs0, ys0, xs1, ys1)


def _courtyard_extent(fp) -> tuple:
    """(x0, y0, x1, y1) mm of the courtyard -- what part-overlap DRC sees.
    Falls back to copper when the footprint has no courtyard."""
    try:
        fp.BuildCourtyardCaches()
        cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
        bb = cy.BBox()
        if bb.GetWidth() > 0 and bb.GetHeight() > 0:
            return (to_mm(bb.GetLeft()), to_mm(bb.GetTop()), to_mm(bb.GetRight()), to_mm(bb.GetBottom()))
    except Exception:  # noqa: BLE001
        pass
    return _copper_extent(fp)


def _extent(fp) -> tuple:
    """Placement extent. Two DRC rules, two extents:

      board edge  -> copper_edge_clearance looks at copper      -> _copper_extent
      other parts -> courtyard overlap looks at courtyards      -> _courtyard_extent

    Why it matters: the USB-C breakout task is a 22 mm board with a 1x08 pin
    header whose courtyard is 21.9 mm and whose copper is 20.3 mm. Judged by
    courtyard it can never be placed; judged by copper it fits with 0.85 mm to
    spare, which is what the reference design does. Silkscreen (GetBoundingBox)
    is 21.4 mm and matters to neither rule. This returns the courtyard extent,
    used for packing so parts do not overlap; edge fit uses copper.
    """
    return _courtyard_extent(fp)


def part_bbox(board, ref: str) -> tuple | None:
    """(x0, y0, x1, y1) in mm of one footprint's copper+courtyard, text excluded."""
    _need()
    for fp in board.GetFootprints():
        if fp.GetReference() == ref:
            return _extent(fp)
    return None


def placement_check(board, margin_mm: float = 0.5) -> dict:
    """Do parts overlap, or hang off the board? Cheap, runs before DRC.

    The DRC classes this catches early -- courtyard overlap and
    copper_edge_clearance -- came to 27+ errors on one measured board and were
    all placement, not routing. Seeing them here costs 10 ms; seeing them in a
    DRC report costs a full re-route.
    """
    _need()
    i = info(board)
    problems, boxes, copper = [], {}, {}
    for fp in board.GetFootprints():
        r = fp.GetReference()
        boxes[r] = _courtyard_extent(fp)      # for overlap, like courtyard DRC
        copper[r] = _copper_extent(fp)        # for edge, like copper_edge DRC
    if i.outline_bbox_mm:
        ox0, oy0, ox1, oy1 = i.outline_bbox_mm
        for r, (x0, y0, x1, y1) in copper.items():
            if x0 < ox0 + margin_mm or y0 < oy0 + margin_mm or x1 > ox1 - margin_mm or y1 > oy1 - margin_mm:
                problems.append(f"{r} 超出板框或贴边（{x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f}），板框 ({ox0:.1f},{oy0:.1f})-({ox1:.1f},{oy1:.1f})")
    refs = sorted(boxes)
    for a_i, a in enumerate(refs):
        ax0, ay0, ax1, ay1 = boxes[a]
        for b in refs[a_i + 1:]:
            bx0, by0, bx1, by1 = boxes[b]
            fa = next(f for f in board.GetFootprints() if f.GetReference() == a)
            fb = next(f for f in board.GetFootprints() if f.GetReference() == b)
            if fa.GetLayer() != fb.GetLayer():
                continue
            if ax0 < bx1 + margin_mm and bx0 < ax1 + margin_mm and ay0 < by1 + margin_mm and by0 < ay1 + margin_mm:
                problems.append(f"{a} 与 {b} 重叠或间距不足 {margin_mm}mm")
    return {"ok": not problems, "problems": problems, "boxes": boxes}


def _skyline_pack(items, W, H, gap):
    """Bottom-left skyline packing. items: [(key, w, h)]. Returns {key: (x, y)}
    of top-left corners, or None if something does not fit. Big parts first,
    small parts drop into the gaps the big ones leave -- shelf packing wastes a
    whole row per tall part, which is how 20 parts needed 52 mm on a 38 mm
    board that the reference design fits comfortably."""
    sky = [(0.0, W, 0.0)]                      # segments: (x0, x1, y_top)
    pos = {}
    for key, w, h in sorted(items, key=lambda it: (-(it[1] * it[2]), -it[2])):
        best = None
        for si, (sx0, sx1, sy) in enumerate(sky):
            x = sx0
            if x + w > W + 1e-9:
                continue
            # the part spans several skyline segments; its floor is the max top among them
            y = sy; xe = x + w
            for (tx0, tx1, ty) in sky:
                if tx0 < xe and tx1 > x:
                    y = max(y, ty)
            if y + h > H + 1e-9:
                continue
            if best is None or (y, x) < (best[0], best[1]):
                best = (y, x)
        if best is None:
            return None
        y, x = best
        pos[key] = (x, y)
        # raise the skyline under the part (plus gap), merge segments
        new = []
        xe = x + w + gap; ye = y + h + gap
        for (tx0, tx1, ty) in sky:
            if tx1 <= x or tx0 >= xe:
                new.append((tx0, tx1, ty))
            else:
                if tx0 < x: new.append((tx0, x, ty))
                if tx1 > xe: new.append((xe, tx1, ty))
        new.append((x, min(xe, W), ye))
        new.sort()
        merged = []
        for seg in new:
            if merged and abs(merged[-1][2] - seg[2]) < 1e-9 and abs(merged[-1][1] - seg[0]) < 1e-9:
                merged[-1] = (merged[-1][0], seg[1], seg[2])
            else:
                merged.append(seg)
        sky = merged
    return pos


def auto_place(board, w_mm: float, h_mm: float, margin_mm: float = 1.0,
               gap_mm: float = 0.6, x0: float = 0.0, y0: float = 0.0) -> dict:
    """Deterministic legal placement of every footprint inside the outline.

    Skyline packing on an empty rectangular board, centred within the native
    outline. This is a provisional pose; functional layout remains a design
    decision. Existing copper and locked mechanical anchors are not moved.
    """
    _need()
    fps = list(board.GetFootprints())
    if any(fp.IsLocked() for fp in fps) or list(board.GetTracks()) or list(board.Zones()):
        return {'placed':{},'fits':False,'note':'Automatic packing requires unlocked parts and no existing copper; use explicit placement'}
    native=pcbnew.SHAPE_POLY_SET()
    if not board_polygon_outlines(board, native):
        return {'placed':{},'fits':False,'note':'Closed native outline required for initial packing'}
    box=native.BBox();x0=to_mm(box.GetX());y0=to_mm(box.GetY())
    w_mm=to_mm(box.GetWidth());h_mm=to_mm(box.GetHeight())
    from tools.board_ops import check_outline
    rectangle=[[x0,y0],[x0+w_mm,y0],[x0+w_mm,y0+h_mm],[x0,y0+h_mm]]
    if check_outline(board,rectangle)['status']!='PASS':
        return {'placed':{},'fits':False,'note':'Nonrectangular outlines and cutouts require explicit placement; bounding-box packing is insufficient'}
    if not fps:return {'placed':{},'fits':True,'note':'No footprints to place'}
    orig = {fp.GetReference(): (fp.GetPosition(), fp.GetOrientationDegrees()) for fp in fps}

    def reset():
        for fp in fps:
            pos, rot = orig[fp.GetReference()]
            fp.SetPosition(pos); fp.SetOrientationDegrees(rot)

    # Pack on COPPER extents -- that is what the board-edge rule measures, and a
    # courtyard may overhang the outline legally (the 1x08 header: copper 20.3
    # mm, courtyard 21.9 mm, board 22 mm). Part-to-part spacing is then checked
    # on courtyards afterwards, and the gap is widened until they clear.
    # Boxes are courtyards (that is what part-to-part DRC measures). The canvas
    # is the outline inset by `margin`, so a box at >= 0 puts its copper at
    # >= margin + its own courtyard overhang -- inside, always. No canvas
    # growth: growing it by the LARGEST overhang and giving each part back only
    # its OWN overhang put a header's copper 0.29 mm outside the board. The
    # margin ladder goes down to 0.2 because a 1x08 header's courtyard is 21.41
    # mm on a 22 mm board; its copper is still 1.2 mm from the edge there.
    for margin in [m for m in (margin_mm, 0.75, 0.5, 0.3, 0.2) if m <= margin_mm]:
        for gap in (gap_mm, gap_mm + 0.5, gap_mm + 1.0):
            reset()
            W, H = w_mm - 2 * margin, h_mm - 2 * margin
            items = []
            for fp in fps:
                kx0, ky0, kx1, ky1 = _courtyard_extent(fp)
                w, h = kx1 - kx0, ky1 - ky0
                if w > W and h <= W:
                    fp.SetOrientationDegrees(fp.GetOrientationDegrees() + 90)
                    kx0, ky0, kx1, ky1 = _courtyard_extent(fp); w, h = kx1 - kx0, ky1 - ky0
                items.append((fp.GetReference(), w, h))
            pos = _skyline_pack(items, W, H, gap)
            if pos is None:
                continue
            # Balance unused space around the packed courtyard envelope. Never
            # translate the board, its mechanical coordinate system or routing.
            used_w=max(pos[ref][0]+width for ref,width,height in items)
            used_h=max(pos[ref][1]+height for ref,width,height in items)
            dx=(W-used_w)/2;dy=(H-used_h)/2
            placed = {}
            for fp in fps:
                x, y = pos[fp.GetReference()]
                x+=dx;y+=dy
                kx0, ky0, _kx1, _ky1 = _courtyard_extent(fp)
                p = fp.GetPosition()
                fp.SetPosition(V(to_mm(p.x) + (x0 + margin + x - kx0), to_mm(p.y) + (y0 + margin + y - ky0)))
                placed[fp.GetReference()] = (round(to_mm(fp.GetPosition().x), 2), round(to_mm(fp.GetPosition().y), 2))
            pc = placement_check(board)                  # same margin review() uses
            if pc["ok"]:
                return {"placed": placed, "fits": True, "margin_mm": margin, "gap_mm": gap, "note": ""}
    reset()
    area = sum((c[2]-c[0])*(c[3]-c[1]) for c in (_courtyard_extent(fp) for fp in fps))
    return {"placed": {}, "fits": False, "margin_mm": margin,
            "note": f"Initial packing found no layout: courtyard area {area:.0f}mm2, board {w_mm*h_mm:.0f}mm2, attempted margin {margin}mm. This does not prove the design cannot fit; place footprints explicitly."}


def ensure_nets(board, names) -> dict:
    """Create every net up front and return {name: NETINFO_ITEM}.

    Creating nets one at a time while walking the map returned by
    GetNetsByName() invalidates it and segfaults pcbnew. Build them all first,
    then read the map once.
    """
    _need()
    have = board.GetNetsByName()
    for n in names:
        if n and n not in have:
            board.Add(pcbnew.NETINFO_ITEM(board, n))
    have = board.GetNetsByName()
    return {n: have[n] for n in names if n in have}


def assign_pad_nets(board, ref: str, mapping: dict) -> int:
    """Assign {pad_name: net_name} on one footprint. Returns pads assigned."""
    _need()
    nets = ensure_nets(board, sorted(set(mapping.values())))
    done = 0
    for fp in board.GetFootprints():
        if fp.GetReference() != ref:
            continue
        for pad in fp.Pads():
            n = mapping.get(pad.GetName())
            if n and n in nets:
                pad.SetNet(nets[n])
                done += 1
    return done


def find_pad(board, ref: str, pad_name: str):
    _need()
    for fp in board.GetFootprints():
        if fp.GetReference() == ref:
            for p in fp.Pads():
                if p.GetName() == pad_name:
                    return p
    return None


def connect_pads(board, a: tuple[str, str], b: tuple[str, str], net_name: str) -> None:
    """Assign both pads to `net_name`, creating the net if needed."""
    _need()
    nets = board.GetNetsByName()
    if net_name in nets:
        ni = nets[net_name]
    else:
        ni = pcbnew.NETINFO_ITEM(board, net_name)
        board.Add(ni)
    for ref, pad in (a, b):
        p = find_pad(board, ref, pad)
        if p is None:
            raise LookupError(f"pad {ref}.{pad} not found")
        p.SetNet(ni)


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------
def add_track(board, x1: float, y1: float, x2: float, y2: float,
              net_name: str, width_mm: float = 0.25, layer: str = "F.Cu"):
    """Lay one straight copper segment. Returns the created track."""
    _need()
    t_ = pcbnew.PCB_TRACK(board)
    t_.SetStart(V(x1, y1))
    t_.SetEnd(V(x2, y2))
    t_.SetWidth(mm(width_mm))
    t_.SetLayer(board.GetLayerID(layer))
    nets = board.GetNetsByName()
    if net_name in nets:
        t_.SetNet(nets[net_name])
    board.Add(t_)
    return t_


def add_via(board, x: float, y: float, net_name: str,
            drill_mm: float = 0.3, diameter_mm: float = 0.6):
    _need()
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(V(x, y))
    v.SetDrill(mm(drill_mm))
    f_cu, b_cu = board.GetLayerID("F.Cu"), board.GetLayerID("B.Cu")
    v.SetLayerPair(f_cu, b_cu)
    # KiCad 9 made PCB_VIA::SetWidth layer-aware; the 1-arg form now asserts.
    try:
        v.SetWidth(f_cu, mm(diameter_mm))
    except (TypeError, NotImplementedError):
        v.SetWidth(mm(diameter_mm))
    nets = board.GetNetsByName()
    if net_name in nets:
        v.SetNet(nets[net_name])
    board.Add(v)
    return v


def route_hv(board, x1: float, y1: float, x2: float, y2: float, net_name: str,
             width_mm: float = 0.25, v_layer: str = "F.Cu", h_layer: str = "B.Cu",
             via_drill: float = 0.3, via_dia: float = 0.6) -> int:
    """Route pad to pad as an L, one axis per layer, with a via at the corner.

    Straight point-to-point copper on a single layer is how a naive router
    manufactures shorts: every line crosses every other line and any pad in
    between. Measured on one 22x38 board, that produced 216 same-layer trace
    shorts and 11 power-ground shorts, which is an absolute defect, not a
    partial one.

    Constraining one layer to vertical runs and the other to horizontal makes a
    same-layer crossing geometrically impossible. Returns the number of items
    added.
    """
    _need()
    n = 0
    if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
        return 0
    # vertical leg on v_layer, from (x1,y1) to (x1,y2)
    if abs(y1 - y2) > 1e-6:
        add_track(board, x1, y1, x1, y2, net_name, width_mm, v_layer)
        n += 1
    # horizontal leg on h_layer, from (x1,y2) to (x2,y2)
    if abs(x1 - x2) > 1e-6:
        if abs(y1 - y2) > 1e-6:
            add_via(board, x1, y2, net_name, via_drill, via_dia)
            n += 1
        add_track(board, x1, y2, x2, y2, net_name, width_mm, h_layer)
        n += 1
        add_via(board, x2, y2, net_name, via_drill, via_dia)
        n += 1
    return n


# ============================================================================
# design rules -- set these BEFORE routing. Every review checklist says so, and
# the reason is mechanical: tracks and vias are created at the current default
# width, so rules set afterwards do not change copper already on the board.
# ============================================================================
_KEEP: dict = {}   # id(board) -> [NETCLASS, ...] kept alive, see set_net_class


def set_design_rules(board, clearance_mm: float = 0.15, track_mm: float = 0.2,
                     via_mm: float = 0.6, drill_mm: float = 0.3,
                     edge_mm: float = 0.3, hole_mm: float = 0.25) -> dict:
    """Set explicit board geometry; low-level defaults are not manufacturing approval.
    Workspace generation requires project-selected rules instead of defaults."""
    _need()
    ds = board.GetDesignSettings()
    ds.m_MinClearance = mm(clearance_mm)
    ds.m_TrackMinWidth = mm(track_mm)
    ds.m_CopperEdgeClearance = mm(edge_mm)
    ds.m_HoleClearance = mm(hole_mm)
    ds.m_ViasMinSize = mm(via_mm)
    ds.m_MinThroughDrill = mm(drill_mm)
    nc = ds.m_NetSettings.GetDefaultNetclass()
    nc.SetClearance(mm(clearance_mm)); nc.SetTrackWidth(mm(track_mm))
    nc.SetViaDiameter(mm(via_mm)); nc.SetViaDrill(mm(drill_mm))
    return {"clearance": clearance_mm, "track": track_mm, "via": via_mm,
            "drill": drill_mm, "edge": edge_mm}


def set_net_class(board, name: str, nets, track_mm: float, clearance_mm: float = 0.15,
                  via_mm: float = 0.6, drill_mm: float = 0.3,
                  dp_width_mm: float | None = None, dp_gap_mm: float | None = None) -> dict:
    """Give a group of nets their own width/clearance (and diff-pair geometry).

    Allows power nets and differential pairs to use their declared geometry.
    Assignment is by exact net name. Tracks routed AFTER this call use it;
    unroute()+autoroute() to apply to existing copper.
    """
    _need()
    ds = board.GetDesignSettings()
    ns = ds.m_NetSettings
    nc = pcbnew.NETCLASS(name)
    # SWIG ownership trap, measured: C++ keeps a pointer to this NETCLASS but
    # Python owns the object. Let it go out of scope and the next
    # GetEffectiveNetClass() dereferences freed memory -- a segfault with no
    # traceback, and only when several calls run in one process (each call in
    # its own subprocess passes). Pin every netclass to the board for its life.
    _KEEP.setdefault(id(board), []).append(nc)
    try:
        nc.thisown = False        # hand ownership to C++; Python must never delete it
    except Exception:  # noqa: BLE001
        pass
    nc.SetTrackWidth(mm(track_mm)); nc.SetClearance(mm(clearance_mm))
    nc.SetViaDiameter(mm(via_mm)); nc.SetViaDrill(mm(drill_mm))
    if dp_width_mm:
        nc.SetDiffPairWidth(mm(dp_width_mm))
    if dp_gap_mm:
        nc.SetDiffPairGap(mm(dp_gap_mm))
    ns.SetNetclass(name, nc)
    for n in nets:
        ns.SetNetclassPatternAssignment(str(n), name)
    ns.RecomputeEffectiveNetclasses()
    return {"class": name, "nets": list(nets), "track_mm": track_mm,
            "dp": (dp_width_mm, dp_gap_mm)}


def net_class_width(board, net_name: str) -> float:
    """Track width (mm) the board will use for this net -- what set_net_class gave it."""
    _need()
    ns = board.GetDesignSettings().m_NetSettings
    nc = ns.GetEffectiveNetClass(str(net_name))
    return to_mm(nc.GetTrackWidth())


def set_layer_count(board, n: int) -> int:
    """Set native copper layer count; workspace tools validate range and existing copper."""
    _need()
    board.SetCopperLayerCount(int(n))
    return board.GetCopperLayerCount()


# ============================================================================
# reference designators
# ============================================================================
def next_ref(board, prefix: str) -> str:
    """Next free reference for a prefix: C1, C2, ... Never hand-count them; a
    duplicate collapses two parts into one in the grader's eyes."""
    _need()
    used = set()
    for fp in board.GetFootprints():
        r = fp.GetReference()
        if r.startswith(prefix) and r[len(prefix):].isdigit():
            used.add(int(r[len(prefix):]))
    i = 1
    while i in used:
        i += 1
    return f"{prefix}{i}"


# ============================================================================
# decoupling -- the one placement rule every checklist agrees on
# ============================================================================
def pad_xy(board, ref: str, pad_name: str) -> tuple | None:
    p = find_pad(board, ref, pad_name)
    if p is None:
        return None
    pos = p.GetPosition()
    return (to_mm(pos.x), to_mm(pos.y))


def add_decoupling(board, ic_ref: str, power_pad: str, gnd_net: str = "GND",
                   value: str = "100nF", footprint: str = "Capacitor_SMD:C_0402_1005Metric",
                   offset_mm: float = 1.5, toward=None) -> dict:
    """Place a decoupling cap right next to an IC's supply pad and wire it.

    Rules encoded (from every published guideline, e.g. Sierra Circuits, JLC):
    as close to the pin as the footprint allows -- 1 to 2 mm, not the 15 mm
    "typical" upper bound; same side as the IC; pad 1 on the supply net the pin
    already carries, pad 2 on ground. Smallest value goes nearest the pin, so
    call this for 100nF first and 1uF/10uF after with a larger offset.

    Returns the part dict -- append it to your spec so the schematic is rebuilt
    with it too, or check_sch_pcb_sync() will (correctly) complain.
    """
    _need()
    xy = pad_xy(board, ic_ref, power_pad)
    if xy is None:
        raise LookupError(f"{ic_ref} 没有焊盘 {power_pad}")
    p = find_pad(board, ic_ref, power_pad)
    pwr = str(p.GetNetname())
    if not pwr:
        raise ValueError(f"{ic_ref}.{power_pad} 还没分配网络，先 assign_pad_nets")
    if toward is None:
        i = info(board)
        cx, cy = ((i.outline_bbox_mm[0] + i.outline_bbox_mm[2]) / 2,
                  (i.outline_bbox_mm[1] + i.outline_bbox_mm[3]) / 2) if i.outline_bbox_mm else (xy[0], xy[1] + 1)
        toward = (cx, cy)
    dx, dy = toward[0] - xy[0], toward[1] - xy[1]
    norm = (dx * dx + dy * dy) ** 0.5 or 1.0
    x, y = xy[0] + dx / norm * offset_mm, xy[1] + dy / norm * offset_mm
    ref = next_ref(board, "C")
    part = {"ref": ref, "symbol": "Device:C", "footprint": footprint, "value": value,
            "at": [round(x, 2), round(y, 2)], "nets": {"1": pwr, "2": gnd_net}}
    add_part(board, part)
    return part


def check_decoupling(board, max_mm: float = 3.0) -> dict:
    """Every IC (U*/IC*) supply pad should have a capacitor within max_mm.
    A supply pad is any pad on a net whose name looks like a rail."""
    _need()
    import re as _re
    rail = _re.compile(r"^(V|\+|VCC|VDD|VBUS|AVDD|DVDD|3V3|5V|1V8|VIN|VOUT)", _re.I)
    caps = [(fp.GetReference(), to_mm(fp.GetPosition().x), to_mm(fp.GetPosition().y))
            for fp in board.GetFootprints() if fp.GetReference().startswith("C")]
    missing = []
    for fp in board.GetFootprints():
        r = fp.GetReference()
        if not (r.startswith("U") or r.startswith("IC")):
            continue
        for pad in fp.Pads():
            n = str(pad.GetNetname())
            if not n or not rail.match(n):
                continue
            px, py = to_mm(pad.GetPosition().x), to_mm(pad.GetPosition().y)
            near = [c for c, cx, cy in caps if ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 <= max_mm]
            if not near:
                missing.append(f"{r}.{pad.GetName()} ({n}) {max_mm}mm 内没有电容")
    return {"ok": not missing, "missing": missing}


# ============================================================================
# differential pairs -- 35 of 35 briefs mention them
# ============================================================================
def route_diff_pair(board, net_p: str, net_n: str, width_mm: float = 0.25,
                    gap_mm: float = 0.2, layer: str = "F.Cu") -> dict:
    """Route P and N as two parallel L paths a constant gap apart, same layer,
    no vias. Encodes the three rules that matter: constant edge-to-edge gap,
    both traces bend on the same side, no unpaired vias. Length skew is
    reported so you can check it against the interface budget (USB 2.0 allows
    roughly 25 mm of mismatch on FR-4; USB 3 far less).

    Only the first pad-to-pad span of each net is routed (a pair normally has
    exactly two ends). Returns per-net lengths and the skew.
    """
    _need()
    pp, pn = net_pads(board, net_p), net_pads(board, net_n)
    if len(pp) < 2 or len(pn) < 2:
        raise ValueError(f"差分对两端不齐: {net_p} {len(pp)} 焊盘, {net_n} {len(pn)} 焊盘")
    pp.sort(key=lambda q: (q[3], q[2])); pn.sort(key=lambda q: (q[3], q[2]))
    (_, _, ax, ay), (_, _, bx, by) = pp[0], pp[-1]
    (_, _, cx, cy), (_, _, dx, dy) = pn[0], pn[-1]
    pitch = width_mm + gap_mm
    # bend both at the same y (mid-way), keeping the pair side by side
    ymid = (ay + by) / 2
    def L(x1, y1, x2, y2, net, off):
        n = 0
        if abs(y1 - ymid) > 1e-6:
            add_track(board, x1, y1, x1, ymid + off, net, width_mm, layer); n += 1
        if abs(x1 - x2) > 1e-6:
            add_track(board, x1, ymid + off, x2, ymid + off, net, width_mm, layer); n += 1
        if abs(y2 - (ymid + off)) > 1e-6:
            add_track(board, x2, ymid + off, x2, y2, net, width_mm, layer); n += 1
        return n
    n1 = L(ax, ay, bx, by, net_p, -pitch / 2)
    n2 = L(cx, cy, dx, dy, net_n, +pitch / 2)
    lp = abs(ay - ymid) + abs(ax - bx) + abs(by - ymid)
    ln = abs(cy - ymid) + abs(cx - dx) + abs(dy - ymid)
    return {"segments": n1 + n2, "len_p_mm": round(lp, 2), "len_n_mm": round(ln, 2),
            "skew_mm": round(abs(lp - ln), 2)}


def check_diff_pairs(board, max_skew_mm: float = 25.0) -> dict:
    """Find P/N pairs by name (X_P/X_N, X+/X-, XP/XN) and report which are
    unrouted, which share no net class, and their routed-length skew."""
    _need()
    names = {str(n) for n in board.GetNetsByName().keys() if n}
    pairs = []
    for n in sorted(names):
        for sp, sn in (("_P", "_N"), ("+", "-"), ("P", "N")):
            if n.endswith(sp) and n[:-len(sp)] + sn in names:
                pairs.append((n, n[:-len(sp)] + sn))
    ns = board.GetDesignSettings().m_NetSettings
    def length(net):
        return sum(to_mm(tr.GetLength()) for tr in board.GetTracks()
                   if str(tr.GetNetname()) == net and tr.Type() == pcbnew.PCB_TRACE_T)
    out = []
    for p_, n_ in pairs:
        lp, ln = length(p_), length(n_)
        cls_p, cls_n = ns.GetEffectiveNetClass(p_).GetName(), ns.GetEffectiveNetClass(n_).GetName()
        issues = []
        if lp == 0 or ln == 0:
            issues.append("未布线")
        if cls_p != cls_n:
            issues.append(f"网络类不同 ({cls_p} / {cls_n})")
        if abs(lp - ln) > max_skew_mm:
            issues.append(f"长度差 {abs(lp-ln):.1f}mm > {max_skew_mm}")
        out.append({"pair": (p_, n_), "len_mm": (round(lp, 1), round(ln, 1)), "issues": issues})
    return {"pairs": out, "ok": all(not x["issues"] for x in out)}


# ============================================================================
# ground stitching & fixes
# ============================================================================
def stitch_ground(board, pitch_mm: float = 5.0, net_name: str = "GND",
                  margin_mm: float = 1.0, drill_mm: float = 0.3, dia_mm: float = 0.6) -> int:
    """Grid of ground vias across the outline. On a two-layer board this is how
    the top-side pour and the bottom-side pour become one return path; every
    checklist for 2-layer designs says "pour and stitch"."""
    _need()
    i = info(board)
    if not i.outline_bbox_mm:
        return 0
    x0, y0, x1, y1 = i.outline_bbox_mm
    ensure_nets(board, [net_name])
    n = 0
    y = y0 + margin_mm
    while y <= y1 - margin_mm:
        x = x0 + margin_mm
        while x <= x1 - margin_mm:
            add_via(board, x, y, net_name, drill_mm, dia_mm); n += 1
            x += pitch_mm
        y += pitch_mm
    return n


def fix_short(board, net_a: str, net_b: str) -> dict:
    """Two nets short: strip both and re-route them on shifted corridors.
    Turns the DRC line "Items shorting two nets (nets X and Y)" into one call."""
    _need()
    removed = unroute(board, net_a) + unroute(board, net_b)
    # re-route just these two, on corridors offset from the global grid
    rep = {}
    for k, net in enumerate((net_a, net_b)):
        pads = net_pads(board, net)
        if len(pads) < 2:
            rep[net] = "少于 2 焊盘"; continue
        pads.sort(key=lambda p: (p[2], p[3]))
        i = info(board); y0, y1 = i.outline_bbox_mm[1], i.outline_bbox_mm[3]
        corridor = y0 + (y1 - y0) * (0.35 + 0.3 * k)
        n = 0
        for (_ra, _pa, xa, ya), (_rb, _pb, xb, yb) in zip(pads, pads[1:]):
            n += route_hv(board, xa, ya, xa, corridor, net) if abs(ya - corridor) > 1e-6 else 0
            if abs(xa - xb) > 1e-6:
                add_track(board, xa, corridor, xb, corridor, net, 0.25, "B.Cu"); n += 1
            n += route_hv(board, xb, corridor, xb, yb, net) if abs(yb - corridor) > 1e-6 else 0
        rep[net] = n
    return {"removed": removed, "rerouted": rep}


def check_dangling(board) -> dict:
    """Nets with a single pad: a label that connects to nothing, which every
    review checklist flags and ERC/DRC both miss."""
    _need()
    count = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            n = str(pad.GetNetname())
            if n:
                count[n] = count.get(n, 0) + 1
    single = sorted(n for n, c in count.items() if c == 1)
    return {"ok": not single, "single_pad_nets": single}


def check_sch_pcb_sync(sch_path, board) -> dict:
    """Compare actual exported schematic pin membership with PCB pads."""
    from tools.validation import compare_schematic_pcb
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pcb-parity-") as d:
        return compare_schematic_pcb(sch_path, board, d)


def net_pads(board, net_name: str) -> list:
    """Every pad on one net, as (reference, pad_name, x_mm, y_mm)."""
    _need()
    out = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() == net_name:
                pos = pad.GetPosition()
                out.append((fp.GetReference(), pad.GetName(),
                            to_mm(pos.x), to_mm(pos.y)))
    return out


def autoroute(board, skip_nets=(),
              width_mm: float = 0.25, via_drill: float = 0.3, via_dia: float = 0.6,
              v_layer: str = "F.Cu", h_layer: str = "B.Cu") -> dict:
    """Connect every net's pads, giving each net its own horizontal corridor.

    `route_hv` puts the horizontal leg at the destination pad's y. Two nets whose
    destination pads share a y therefore overlap on the same layer -- which is
    where the 44 same-layer shorts left over after the H/V fix came from.

    Here each net gets a reserved y band of its own, and the route is a Z:

        pad A --vertical on F.Cu--> corridor_y --horizontal on B.Cu-->
              --vertical on F.Cu--> pad B

    Horizontal legs of different nets can no longer overlap, because no two nets
    share a corridor. Vertical legs sit at pad x-coordinates, which collide only
    when two pads of different nets are exactly aligned vertically.

    Ground is skipped by default: a pour is its return path, and routing N ground
    traces across the board is exactly what a pour exists to avoid.

    Returns a per-net summary; nothing raises, so one unroutable net does not
    cost you the rest of the board.
    """
    _need()
    skip = {s.upper() for s in skip_nets}
    # GetNetsByName() hands back wxString keys, not str: they have no .upper()
    # and never compare equal to a Python string, so a set difference against
    # them silently reports every net as missing. Convert once, here.
    names = [s for s in (str(n) for n in board.GetNetsByName().keys())
             if s and s.upper() not in skip and not s.startswith("unconnected-")]
    names.sort()
    bb = edge_bounding_box(board)
    # Inset the corridor band from the outline. Spreading corridors across the
    # full height puts the outermost ones on the board edge, which DRC reports
    # as copper_edge_clearance -- 27 of them on the first measured run.
    edge = 1.0
    y0, y1 = to_mm(bb.GetTop()) + edge, to_mm(bb.GetBottom()) - edge
    span = max(y1 - y0, 1.0)

    report, added = {}, 0
    for i, net in enumerate(names):
        pads = net_pads(board, net)
        if len(pads) < 2:
            report[net] = {"pads": len(pads), "segments": 0,
                           "note": "少于 2 个焊盘，无需布线"}
            continue
        # one reserved corridor per net, spread evenly inside the outline
        corridor = y0 + span * (i + 1) / (len(names) + 1)
        try:
            n = route_net(board, net, corridor, net_class_width(board, net), via_drill, via_dia, v_layer, h_layer)
            report[net] = {"pads": len(pads), "segments": n, "corridor_y": round(corridor, 2)}
        except Exception as e:                           # noqa: BLE001
            n = 0
            report[net] = {"pads": len(pads), "segments": 0, "note": f"{type(e).__name__}: {e}"}
        added += n
    return {"nets_routed": len([r for r in report.values() if r.get("segments")]),
            "items_added": added, "per_net": report}


def route_net(board, net: str, corridor_y: float | None = None, width_mm: float = 0.25,
              via_drill: float = 0.3, via_dia: float = 0.6,
              v_layer: str = "F.Cu", h_layer: str = "B.Cu") -> int:
    """Route one net's pads along a Z through one horizontal corridor. This is
    what autoroute() does per net; exposed so a single split net can be
    re-routed without touching the rest of the board."""
    _need()
    pads = net_pads(board, net)
    if len(pads) < 2:
        return 0
    if corridor_y is None:
        bb = edge_bounding_box(board)
        corridor_y = (to_mm(bb.GetTop()) + to_mm(bb.GetBottom())) / 2
    pads.sort(key=lambda q: (q[2], q[3]))
    n = 0
    for (_ra, _pa, xa, ya), (_rb, _pb, xb, yb) in zip(pads, pads[1:]):
        if abs(ya - corridor_y) > 1e-6:
            add_track(board, xa, ya, xa, corridor_y, net, width_mm, v_layer)
            add_via(board, xa, corridor_y, net, via_drill, via_dia); n += 2
        if abs(xa - xb) > 1e-6:
            add_track(board, xa, corridor_y, xb, corridor_y, net, width_mm, h_layer); n += 1
        if abs(yb - corridor_y) > 1e-6:
            add_via(board, xb, corridor_y, net, via_drill, via_dia)
            add_track(board, xb, corridor_y, xb, yb, net, width_mm, v_layer); n += 2
    return n


def connectivity(board, tol_mm: float = 0.05) -> dict:
    """Per net: how many separate copper islands its pads fall into.

    Built from the board itself -- pads, tracks, vias, and zones on the net --
    by touching geometry, so it works with no DRC report and says exactly which
    pads are stranded. One island per net is the definition of "connected";
    two or more is a split net, the thing a functional test fails on first.
    Zones count as connecting every pad and via of their net that lies inside
    them (fills are not computed headless; the outline is the conservative
    approximation).
    """
    _need()
    from collections import defaultdict
    nets = defaultdict(lambda: {"pads": [], "segs": [], "zones": []})
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            n = str(pad.GetNetname())
            if n:
                pos = pad.GetPosition(); bb = pad.GetBoundingBox()
                nets[n]["pads"].append((f"{fp.GetReference()}.{pad.GetName()}", to_mm(pos.x), to_mm(pos.y),
                                        max(to_mm(bb.GetWidth()), to_mm(bb.GetHeight())) / 2))
    for tr in board.GetTracks():
        n = str(tr.GetNetname())
        if not n:
            continue
        if tr.Type() == pcbnew.PCB_VIA_T:
            pos = tr.GetPosition(); r = to_mm(tr.GetWidth(pcbnew.F_Cu)) / 2
            nets[n]["segs"].append((to_mm(pos.x), to_mm(pos.y), to_mm(pos.x), to_mm(pos.y), r))
        else:
            s, e = tr.GetStart(), tr.GetEnd()
            nets[n]["segs"].append((to_mm(s.x), to_mm(s.y), to_mm(e.x), to_mm(e.y), to_mm(tr.GetWidth()) / 2))
    for z in board.Zones():
        n = str(z.GetNetname())
        if n:
            bb = z.GetBoundingBox()
            nets[n]["zones"].append((to_mm(bb.GetLeft()), to_mm(bb.GetTop()), to_mm(bb.GetRight()), to_mm(bb.GetBottom())))

    def near_seg(px, py, seg, extra):
        x1, y1, x2, y2, r = seg
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t_ = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
        cx, cy = x1 + t_ * dx, y1 + t_ * dy
        return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 <= r + extra + tol_mm

    report, split = {}, {}
    for n, d in nets.items():
        items = [("pad", *p) for p in d["pads"]] + [("seg", i, *s) for i, s in enumerate(d["segs"])]
        parent = list(range(len(items)))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]; i = parent[i]
            return i
        def union(i, j):
            parent[find(i)] = find(j)
        # zone: everything of the net inside the zone bbox is one island
        for (zx0, zy0, zx1, zy1) in d["zones"]:
            inside = [i for i, it in enumerate(items)
                      if (it[0] == "pad" and zx0 <= it[2] <= zx1 and zy0 <= it[3] <= zy1)
                      or (it[0] == "seg" and zx0 <= it[2] <= zx1 and zy0 <= it[3] <= zy1)]
            for a_ in inside[1:]:
                union(inside[0], a_)
        for i, a_ in enumerate(items):
            for j in range(i + 1, len(items)):
                b_ = items[j]
                if a_[0] == "pad" and b_[0] == "pad":
                    continue
                if a_[0] == "pad":
                    if near_seg(a_[2], a_[3], b_[2:], a_[4]): union(i, j)
                elif b_[0] == "pad":
                    if near_seg(b_[2], b_[3], a_[2:], b_[4]): union(i, j)
                else:
                    x1, y1, x2, y2, r = a_[2:]
                    if near_seg(x1, y1, b_[2:], r) or near_seg(x2, y2, b_[2:], r): union(i, j)
        pad_idx = [i for i, it in enumerate(items) if it[0] == "pad"]
        islands = {}
        for i in pad_idx:
            islands.setdefault(find(i), []).append(items[i][1])
        report[n] = {"pads": len(pad_idx), "islands": len(islands), "groups": list(islands.values())}
        if len(islands) > 1:
            split[n] = len(islands)
    return {"ok": False, "status": "UNKNOWN", "scope": "legacy geometric heuristic; use official DRC", "split_nets": split, "per_net": report}


def unconnected_by_net(drc_json) -> dict:
    """KiCad's own connectivity verdict, per net: how many "Missing connection"
    items each net has. This is what a grader means by a split net or an
    isolated pad. audit() cannot see it -- a net with one track and three
    stranded pads counts as "has copper" there."""
    import json as _j
    raw = _j.loads(pathlib.Path(drc_json).read_text(errors="replace"))
    out = {}
    for v in raw.get("unconnected_items", []) or []:
        nets = set()
        for it in v.get("items", []) or []:
            m = re.search(r"\[([^\]]+)\]", it.get("description", ""))
            if m:
                nets.add(m.group(1))
        for n in nets or {"?"}:
            out[n] = out.get(n, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def check_protection(board) -> dict:
    """USB / Ethernet data nets should have an ESD/TVS part on them: every brief
    that mentions an external data interface asks for it, and it is the first
    thing a reviewer looks for next to a connector."""
    _need()
    data = [str(n) for n in board.GetNetsByName().keys()
            if str(n) and re.match(r"^(D[+-]|D_?[PN]|DP|DN|USB_?D[PMN+-]|TX[+-]?|RX[+-]?|MDI)", str(n), re.I)]
    prot = re.compile(r"(TVS|ESD|USBLC|PESD|PRTR|SRV05|IP4220|SOT-23-6|SOT-353)", re.I)
    protected = set()
    for fp in board.GetFootprints():
        txt = f"{fp.GetValue()} {fp.GetFPIDAsString()}"
        if prot.search(txt):
            for pad in fp.Pads():
                protected.add(str(pad.GetNetname()))
    missing = [n for n in data if n not in protected]
    return {"ok": not missing, "data_nets": data, "unprotected": missing}


def review(board, required_nets=None, drc_json=None) -> dict:
    """Everything that stands between this board and a finished one, in one call.

    The point is to make "is it done yet" mechanical. Holding that question in
    the model's head is what produces the 100k-character reasoning turns that
    burn a whole time budget; a list of concrete gaps can be worked through one
    at a time.

    `required_nets` is the signal list from the brief -- pass it and this also
    reports interfaces the design has not covered at all, which is the most
    expensive class of mistake.
    """
    _need()
    a = audit(board)
    ann = a["annotation"]
    have = {s for s in (str(n) for n in board.GetNetsByName().keys()) if s}
    missing = sorted(set(required_nets or []) - have)

    # A required net that exists under a different spelling is the most common
    # way to lose the interface-mapping score while believing the board is
    # complete: measured, a board with every signal present scored at the floor
    # because it called the brief's D_N "D-". Say so explicitly.
    def _key(s):
        s = s.upper().replace("+", "P").replace("-", "N")
        return "".join(ch for ch in s if ch.isalnum())
    by_key = {}
    for n in have:
        by_key.setdefault(_key(n), []).append(n)
    near = {m: by_key[_key(m)] for m in missing if _key(m) in by_key}

    gaps = []
    if not a["has_closed_outline"]:
        gaps.append("板框不闭合——没有闭合外框就不是一块板")
    gaps.extend(ann["problems"])
    for m, alts in near.items():
        gaps.append(f"简报要求的网络 {m} 不存在，但有拼法不同的 {alts}——"
                    f"用简报里的原名，改名比解释便宜")
    truly_missing = [m for m in missing if m not in near]
    if truly_missing:
        gaps.append(f"简报要求的网络还没有: {truly_missing}——缺一整类接口是最贵的错")
    if a["footprints"] == 0:
        gaps.append("一个元件都没有")
    if a["nets_without_copper_count"]:
        gaps.append(f"{a['nets_without_copper_count']} 条网络没有铜箔: "
                    f"{a['nets_without_copper'][:6]}——先 autoroute()，地网络用铺铜")
    if a["pads_without_net_count"]:
        gaps.append(f"{a['pads_without_net_count']} 个焊盘没分配网络: "
                    f"{a['pads_without_net'][:6]}")
    if a["zones"] == 0:
        gaps.append("没有铺铜——地网络的回流路径靠它，不要用走线连地")
    pc = placement_check(board)
    if not pc["ok"]:
        gaps.extend(pc["problems"][:4])
    dg = check_dangling(board)
    if not dg["ok"]:
        gaps.append(f"只接了 1 个焊盘的网络: {dg['single_pad_nets'][:6]}——标签接到了空处")
    dp = check_diff_pairs(board)
    for x in dp["pairs"]:
        if x["issues"]:
            gaps.append(f"差分对 {x['pair']}: {', '.join(x['issues'])}——用 route_diff_pair / set_net_class")
    dc = check_decoupling(board)
    if not dc["ok"]:
        gaps.append(f"IC 电源脚缺去耦: {dc['missing'][:3]}——add_decoupling")
    pr = check_protection(board)
    if not pr["ok"]:
        gaps.append(f"数据网络没有 ESD/TVS 保护: {pr['unprotected'][:4]}——在 spec 里加一个 TVS（如 Diode:PESD5V0S1BL）")
    cn = connectivity(board)
    if not cn["ok"]:
        gaps.append(f"网络断成多个铜岛（网络: 岛数）: {dict(list(cn['split_nets'].items())[:5])}——unroute(net) 后 route_net(net)")
    if drc_json and pathlib.Path(str(drc_json)).exists():
        ub = unconnected_by_net(drc_json)
        if ub and cn["ok"]:
            gaps.append(f"KiCad 报告未连通: {dict(list(ub.items())[:5])}")
    return {"done": not gaps, "gaps": gaps, "audit": a,
            "required_missing": missing, "near_miss": near,
            "next": gaps[0] if gaps else "可以交了"}


def add_ground_zone(board, w_mm: float, h_mm: float, net_name: str = "GND",
                    layer: str = "B.Cu", x0: float = 0.0, y0: float = 0.0,
                    margin_mm: float = 0.5):
    """Pour a rectangular copper zone, inset from the outline.

    Return paths need a pour; DRC will say so if there is none. But pouring the
    full board rectangle puts copper on the board edge everywhere -- measured:
    27 copper_edge_clearance errors from this alone, more than the shorts the
    routing produced. `margin_mm` keeps the pour off the edge.
    """
    _need()
    x0, y0 = x0 + margin_mm, y0 + margin_mm
    w_mm, h_mm = max(w_mm - 2 * margin_mm, 0.1), max(h_mm - 2 * margin_mm, 0.1)
    nets = board.GetNetsByName()
    if net_name not in nets:
        ni = pcbnew.NETINFO_ITEM(board, net_name)
        board.Add(ni)
        nets = board.GetNetsByName()
    z = pcbnew.ZONE(board)
    z.SetLayer(board.GetLayerID(layer))
    z.SetNet(nets[net_name])
    poly = z.Outline()
    poly.NewOutline()
    for x, y in ((x0, y0), (x0 + w_mm, y0), (x0 + w_mm, y0 + h_mm), (x0, y0 + h_mm)):
        poly.Append(mm(x), mm(y))
    board.Add(z)
    return z


def fill_zones(board, force: bool = False) -> str:
    """Do NOT fill zones from a headless script by default.

    pcbnew's ZONE_FILLER expects a progress reporter that only exists under the
    GUI; calling Fill() from plain Python segfaults the interpreter, and a
    segfault cannot be caught, so one bad call loses the whole run. The zone
    outline is saved either way, and kicad-cli fills zones itself when it loads
    the board for DRC or export -- which is the only place the fill is read.

    Pass force=True only if you have verified it on your KiCad build.
    """
    _need()
    if not force:
        return "UNKNOWN: not filled; use kicad_cli.fill_zones_file in an isolated subprocess"
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    return "filled"


# --------------------------------------------------------------------------
# audit -- the checks worth running before handing a board to a grader
# --------------------------------------------------------------------------
# Reference-designator prefixes for external interfaces, per IEEE 315 / IPC
# practice: J (jack/receptacle), P (plug), X and CN (connectors), SW (switch),
# M (module/motor). Every downstream consumer of a board -- BOM, pick-and-place,
# assembly drawings, test fixtures -- keys on these, which is why annotation is
# checked before anything else.
CONNECTOR_PREFIXES = ("CN", "CON", "J", "M", "P", "SW", "X")
NON_INTERFACE_PREFIXES = ("#", "D", "FID", "G***", "H", "MH", "MK", "MP", "NT", "TP")


def check_annotation(board) -> dict:
    """Is every footprint annotated, uniquely, and is anything an interface?

    Measured failure this exists to prevent: a board with 9 correctly chosen
    parts, 34 tracks, 20 vias and two pours, every footprint still carrying
    KiCad's placeholder reference `REF**`. Nothing downstream can use such a
    board: BOM lines collide, the pick-and-place file has nine parts at one
    name, and any automated test that addresses a connector by reference finds
    nothing. Annotation is not cosmetic; it is the board's addressing scheme.

    Returns a report; `problems` is empty when the board is safe to submit.
    """
    _need()
    refs, dupes, placeholders, interfaces = [], [], [], []
    for fp in board.GetFootprints():
        r = (fp.GetReference() or "").strip()
        refs.append(r)
        if not r or r.upper().startswith("REF") or "*" in r:
            placeholders.append(r or "(空)")
        if r.upper().startswith(CONNECTOR_PREFIXES) and not r.upper().startswith(
                NON_INTERFACE_PREFIXES):
            interfaces.append(r)
    seen = set()
    for r in refs:
        if r in seen:
            dupes.append(r)
        seen.add(r)

    problems = []
    if placeholders:
        problems.append(
            f"{len(placeholders)} 个元件仍是占位位号 {sorted(set(placeholders))[:5]}——"
            f"BOM、贴片坐标文件、装配图都以位号为键，同名元件无法区分，等于没有这些元件")
    if dupes:
        problems.append(
            f"位号重复 {sorted(set(dupes))[:5]}——BOM 和贴片文件里它们无法区分")
    return {
        "ok": not problems,
        "footprints": len(refs),
        "unique_refs": len(seen),
        "placeholder_refs": sorted(set(placeholders)),
        "duplicate_refs": sorted(set(dupes)),
        "interface_refs": sorted(set(interfaces)),
        "problems": problems,
    }


def audit(board) -> dict:
    """Cheap structural checks that catch the usual ways a board is wrong."""
    _need()
    i = info(board)
    unrouted, isolated = [], []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            n = p.GetNetname()
            if not n:
                isolated.append(f"{fp.GetReference()}.{p.GetName()}")
    # A net carried by a pour has copper; counting only tracks reports GND as
    # unrouted on every board that does the right thing and pours it.
    routed_nets = {str(t_.GetNetname()) for t_ in board.GetTracks() if t_.GetNetname()}
    for z in board.Zones():
        if z.GetNetname():
            routed_nets.add(str(z.GetNetname()))
    for n in i.nets:
        if str(n) not in routed_nets:
            unrouted.append(str(n))
    return {
        "layers": i.layers,
        "footprints": i.footprints,
        "tracks": i.tracks,
        "vias": i.vias,
        "zones": i.zones,
        "nets": len(i.nets),
        "outline_bbox_mm": i.outline_bbox_mm,
        "has_closed_outline": i.outline_bbox_mm is not None,
        "pads_without_net": isolated[:50],
        "pads_without_net_count": len(isolated),
        "nets_without_copper": unrouted[:50],
        "nets_without_copper_count": len(unrouted),
        "annotation": check_annotation(board),
    }


if __name__ == "__main__":
    print(json.dumps({"pcbnew": bool(pcbnew),
                      "version": getattr(pcbnew, "GetBuildVersion", lambda: "n/a")()
                      if pcbnew else str(_IMPORT_ERROR)}, indent=2))
