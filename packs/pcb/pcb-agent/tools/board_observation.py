"""Read-only, revision-bound native board queries. Bounds are not connectivity."""
from tools import pcb_editor as ed, layout, postroute, validation as v
from tools.argument_schema import InputError


def reject(code, field, value, expected, remedy):
    error = InputError(code, 'arguments.' + field, value, expected, remedy)
    error.operation_executed = False
    raise error


def inspect(include_copper=False, include_geometry=False, item_id='', net_name='',
            collection='summary', offset=0, limit=10, layer='', bounds_mm=None,
            ref='', expected_board_sha256=''):
    from tools.board_ops import PAD_ZONE_CONNECTIONS, _require_item
    digest = v.file_hash('board.kicad_pcb')
    if expected_board_sha256 and expected_board_sha256 != digest:
        reject('STALE_BOARD_QUERY', 'expected_board_sha256', expected_board_sha256,
               {'current_board_sha256': digest}, 'Restart the query at offset 0 on the current board.')
    if offset and not expected_board_sha256:
        reject('UNBOUND_BOARD_PAGE', 'expected_board_sha256', '', {'required': True},
               'Use next_call from the preceding page; it preserves the board revision and filters.')
    if collection == 'summary' and (offset or limit != 10 or layer or bounds_mm is not None or ref):
        reject('MISSING_BOARD_COLLECTION', 'collection', collection,
               {'enum': ['copper', 'pads', 'parts', 'drawings']}, 'Select a collection for pagination or spatial filters.')
    if item_id and collection != 'summary':
        reject('CONFLICTING_QUERY', 'item_id', item_id, {'collection': 'summary'},
               'Use an exact UUID query or a paged collection query, not both.')
    if ref and collection not in ('parts','pads'):
        reject('INVALID_REF_FILTER','ref',ref,{'collection':['parts','pads']},'Use ref with a pads or parts collection; copper filters use net, layer or bounds.')
    if bounds_mm is not None and any(bounds_mm[0][i] > bounds_mm[1][i] for i in (0, 1)):
        reject('INVALID_BOUNDS', 'bounds_mm', bounds_mm, {'order': '[[xmin,ymin],[xmax,ymax]]'},
               'Supply an ordered native board-space bounding rectangle in mm.')
    board = ed.load('board.kicad_pcb')
    enabled_layers = {board.GetLayerName(i) for i in board.GetEnabledLayers().Seq()}
    if layer and layer not in enabled_layers:
        reject('UNKNOWN_LAYER', 'layer', layer, {'enabled_layers': sorted(enabled_layers)}, 'Use a current enabled layer name.')
    if net_name and net_name not in board.GetNetsByName():
        reject('UNKNOWN_NET', 'net_name', net_name, {'candidate_nets': list(board.GetNetsByName())[:50]},
               'Use a candidate net from the current board inspection or DRC report.')
    connections = {value: name for name, value in PAD_ZONE_CONNECTIONS.items()}

    def base(obj):
        return {'id': obj.m_Uuid.AsString(), 'kind': obj.GetClass(),
                'layer': board.GetLayerName(obj.GetLayer()),
                'layers': [board.GetLayerName(i) for i in obj.GetLayerSet().Seq()],
                'bounds_mm': layout._box(obj.GetBoundingBox())}

    parts = []
    for fp in board.GetFootprints():
        part = dict(base(fp), ref=fp.GetReference(), value=fp.GetValue(),
                    at=[ed.to_mm(fp.GetPosition().x), ed.to_mm(fp.GetPosition().y)],
                    rotation=fp.GetOrientationDegrees(), side='bottom' if fp.IsFlipped() else 'top', locked=fp.IsLocked())
        part['bounds_mm'] = layout._box(fp.GetBoundingBox(False, False))
        part['pads'] = [dict(base(p), kind='PAD', ref=fp.GetReference(), pin=p.GetNumber(), net=p.GetNetname(),
                            zone_connection=connections[int(p.GetLocalZoneConnection())],
                            xy=[ed.to_mm(p.GetPosition().x), ed.to_mm(p.GetPosition().y)],
                            size_mm=[ed.to_mm(p.GetSize().x), ed.to_mm(p.GetSize().y)],
                            rotation_deg=p.GetOrientationDegrees(), shape_code=int(p.GetShape())) for p in fp.Pads()]
        part['pad_count'] = len(part['pads'])
        part['children_query'] = {'tool': 'inspect_board', 'arguments': dict(collection='pads', ref=part['ref'],
                                   expected_board_sha256=digest)}
        parts.append(part)
    if ref and not any(p['ref'] == ref for p in parts):
        reject('UNKNOWN_COMPONENT', 'ref', ref, {'existing_refs': sorted(p['ref'] for p in parts)}, 'Use a reference from this candidate.')
    copper = []
    for obj in [*board.GetTracks(), *board.Zones()]:
        entry = dict(base(obj), net=obj.GetNetname(), at=[ed.to_mm(obj.GetPosition().x), ed.to_mm(obj.GetPosition().y)])
        if isinstance(obj, ed.pcbnew.PCB_VIA):
            entry.update(diameter_mm=ed.to_mm(obj.GetWidth(obj.TopLayer())), drill_mm=ed.to_mm(obj.GetDrillValue()))
        elif obj.GetClass() in ('PCB_TRACK', 'PCB_ARC'):
            entry.update(start=[ed.to_mm(obj.GetStart().x), ed.to_mm(obj.GetStart().y)],
                         end=[ed.to_mm(obj.GetEnd().x), ed.to_mm(obj.GetEnd().y)], width_mm=ed.to_mm(obj.GetWidth()))
            if obj.GetClass() == 'PCB_ARC':
                entry['mid'] = [ed.to_mm(obj.GetMid().x), ed.to_mm(obj.GetMid().y)]
        else:
            entry.update(filled=bool(obj.IsFilled()), geometry_scope='Native zone envelope; filled polygon contact is not evaluated.')
        copper.append(entry)
    drawings = [base(x) for x in board.GetDrawings()]
    pads = [p for part in parts for p in part['pads']]
    geometry = layout.annotate(board, parts) if include_geometry else None
    common = dict(board_sha256=digest, coordinate_units='mm',
                  measurement_scope='Native geometry and axis-aligned envelope intersections only; no copper contact or connectivity verdict.')
    if item_id:
        objects = {x['id']: x for x in copper + drawings + parts + pads}
        item = _require_item(objects, item_id)
        if net_name and item.get('net') != net_name:
            reject('ITEM_NET_MISMATCH', 'net_name', net_name, {'actual_net': item.get('net')}, 'Use the actual net or omit the net filter.')
        if item['kind']=='FOOTPRINT':item={k:val for k,val in item.items() if k!='pads'}
        return dict(common, object=item)

    def matches(item):
        if net_name and item.get('net') != net_name and not any(p['net'] == net_name for p in item.get('pads', [])):
            return False
        if ref and item.get('ref') != ref: return False
        if layer and layer not in item['layers']: return False
        if bounds_mm is not None:
            box = item['bounds_mm']
            if any(box['max'][i] < bounds_mm[0][i] or box['min'][i] > bounds_mm[1][i] for i in (0, 1)): return False
        return True

    query = dict(collection=collection, include_geometry=include_geometry, net_name=net_name, layer=layer,
                 ref=ref, expected_board_sha256=digest, limit=limit)
    if bounds_mm is not None: query['bounds_mm'] = bounds_mm
    if collection != 'summary':
        rows = sorted((x for x in dict(parts=parts, pads=pads, copper=copper, drawings=drawings)[collection] if matches(x)), key=lambda x: x['id'])
        if collection == 'parts': rows = [{k: val for k, val in x.items() if k != 'pads'} for x in rows]
        result = dict(common, collection=collection, items=rows[offset:offset + limit], total_items=len(rows),
                      offset=offset, query=query, page_complete=True)
        update_page(result)
        return result
    result = dict(common, parts=[p for p in parts if matches(p)], layers=board.GetCopperLayerCount(),
                  measurements=postroute.measure_board('board.kicad_pcb'))
    if net_name:
        result['measurements']['nets'] = {k: val for k, val in result['measurements']['nets'].items() if k == net_name}
        result['measurements']['placements'] = {k: val for k, val in result['measurements']['placements'].items() if any(p['ref'] == k for p in result['parts'])}
    if geometry: result['geometry'] = geometry
    if include_copper or net_name: result.update(copper=[x for x in copper if matches(x)], drawings=drawings)
    result['collection_queries'] = {name:{'tool':'inspect_board','arguments':dict(collection=name,net_name=net_name,expected_board_sha256=digest)} for name in ('parts','pads','copper','drawings')}
    result['inspection_hint'] = 'Use collection=copper/pads/parts/drawings for complete pages, optionally with bounds_mm, layer or net_name; follow next_call. item_id selects one current UUID. Footprint children_query pages all pads. Full stdout remains available.'
    return result


def update_page(result):
    """Advance by entries actually delivered, including after byte compaction."""
    count = len(result.get('items', [])); offset = result['offset']; total = result['total_items']
    more = offset + count < total
    result.update(returned_items=count, has_more=more, next_offset=offset + count if more else None,
                  section_complete=offset == 0 and count == total,
                  next_call={'tool': 'inspect_board', 'arguments': dict(result['query'], offset=offset + count,
                                limit=max(1, min(result['query']['limit'], count)))} if more else None)


def output_schema():
    point = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'maxItems': 2}
    common = {'id': {'type': 'string'}, 'kind': {'type': 'string'}, 'layers': {'type': 'array', 'items': {'type': 'string'}},
              'bounds_mm': {'type': 'object', 'properties': {'min': point, 'max': point}, 'required': ['min', 'max']}}
    variants = []
    for kinds, props, required in [
        (['PCB_TRACK', 'PCB_ARC'], {'start': point, 'end': point, 'mid': point, 'width_mm': {'type': 'number'}}, ['start', 'end', 'width_mm']),
        (['PCB_VIA'], {'at': point, 'diameter_mm': {'type': 'number'}, 'drill_mm': {'type': 'number'}}, ['at', 'diameter_mm', 'drill_mm']),
        (['PAD'], {'xy': point, 'size_mm': point, 'rotation_deg': {'type': 'number'}, 'pin': {'type': 'string'}, 'ref': {'type': 'string'}}, ['xy', 'size_mm', 'rotation_deg', 'pin', 'ref']),
        (['FOOTPRINT'], {'at': point, 'pad_count': {'type': 'integer'}, 'children_query': {'type': 'object'}}, ['at', 'pad_count', 'children_query']),
        (['ZONE'], {'at': point, 'filled': {'type': 'boolean'}}, ['at', 'filled']),
    ]:
        variants.append({'type': 'object', 'properties': dict(common, **props, kind={'enum': kinds}), 'required': ['id', 'kind', 'layers', 'bounds_mm'] + required})
    variants.append({'type': 'object', 'properties': dict(common,kind={'type':'string','not':{'enum':['PCB_TRACK','PCB_ARC','PCB_VIA','PAD','FOOTPRINT','ZONE']}}), 'required': ['id', 'kind', 'layers', 'bounds_mm'],
                     'description': 'Other native drawing kinds expose only common geometry fields.'})
    return {'type': 'object', 'required': ['status', 'scope', 'next_action', 'board_sha256', 'coordinate_units'],
            'properties': {'status': {'type':'string'}, 'scope': {'type':'string'}, 'next_action': {'type':'string'},
                           'verification_role': {'type':'string'}, 'board_sha256': {'type': 'string'}, 'coordinate_units': {'const': 'mm'},
                           'items': {'type': 'array', 'items': {'anyOf': variants}}, 'object': {'anyOf': variants},
                           'total_items': {'type': 'integer'}, 'returned_items': {'type': 'integer'},
                           'has_more': {'type': 'boolean'}, 'next_call': {'type': ['object', 'null']}},
            'description': 'Paged collection returns items and next_call; summary returns parts/measurements. Track endpoints are absent on vias. Bounds are envelopes, not contact proof.'}
