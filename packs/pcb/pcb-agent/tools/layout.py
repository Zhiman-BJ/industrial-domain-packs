"""Read native layout geometry without choosing placements or scoring aesthetics."""
from tools import pcb_editor as ed


def _box(bounds):
    return {'min': [ed.to_mm(bounds.GetX()), ed.to_mm(bounds.GetY())],
            'max': [ed.to_mm(bounds.GetX() + bounds.GetWidth()),
                    ed.to_mm(bounds.GetY() + bounds.GetHeight())]}


def _text(item, board):
    return {'id': item.m_Uuid.AsString(), 'text': item.GetText(),
            'at': [ed.to_mm(item.GetPosition().x), ed.to_mm(item.GetPosition().y)],
            'rotation_deg': item.GetTextAngle().AsDegrees(),
            'size_mm': [ed.to_mm(item.GetTextSize().x), ed.to_mm(item.GetTextSize().y)],
            'layer': board.GetLayerName(item.GetLayer()),
            'visible': item.IsVisible(), 'mirrored': item.IsMirrored(),
            'bounds_mm': _box(item.GetBoundingBox())}


def annotate(board, parts):
    """Enrich inspection rows in memory; all coordinates use current board space."""
    footprints = {fp.GetReference(): fp for fp in board.GetFootprints()}
    for part in parts:
        fp = footprints[part['ref']]
        fp.BuildCourtyardCaches()
        courtyards = []
        for layer in (ed.pcbnew.F_CrtYd, ed.pcbnew.B_CrtYd):
            shape = fp.GetCourtyard(layer)
            if shape.OutlineCount():
                courtyards.append({'layer': board.GetLayerName(layer),
                                   'bounds_mm': _box(shape.BBox())})
        part['geometry'] = {
            'bounds_mm': _box(fp.GetBoundingBox(False, False)),
            'courtyards': courtyards,
            'texts': {'reference': _text(fp.Reference(), board),
                      'value': _text(fp.Value(), board)}}
        for row, pad in zip(part['pads'], fp.Pads()):
            row.update(size_mm=[ed.to_mm(pad.GetSize().x), ed.to_mm(pad.GetSize().y)],
                       rotation_deg=pad.GetOrientationDegrees(),
                       bounds_mm=_box(pad.GetBoundingBox()))
    edges = [item for item in board.GetDrawings() if item.GetLayer() == ed.pcbnew.Edge_Cuts]
    return {'edge_bounds_mm': _box(ed.edge_bounding_box(board)) if edges else None,
            'scope': 'Native board-space axis-aligned envelopes, including drawn stroke extents. '
                     'Pad sizes use pad-local axes. Footprint bounds exclude text. Empty courtyards '
                     'mean unavailable geometry. No collision, height, electrical or aesthetic verdict.'}
