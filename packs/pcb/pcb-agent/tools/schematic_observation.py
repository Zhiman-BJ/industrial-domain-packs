"""Native schematic pages; cursors bind filters and the actual sheet revision."""
from tools import schematic_ops
from tools.argument_schema import InputError


def inspect(sheet, ref, item_id, kind, offset, limit, expected_sha256):
    result = schematic_ops.inspect(schematic_ops.source(sheet))
    digest = result['schematic_sha256']
    if (expected_sha256 and expected_sha256 != digest) or (offset and not expected_sha256):
        error = InputError('STALE_SCHEMATIC_QUERY' if expected_sha256 else 'UNBOUND_SCHEMATIC_PAGE',
            'arguments.expected_sha256', expected_sha256, {'current_schematic_sha256': digest},
            'Restart at offset 0 on this sheet, then follow next_call without changing its revision or filters.')
        error.operation_executed = False
        raise error
    rows = sorted((o for o in result['objects'] if
        (ref is None or o.get('ref') == ref) and (item_id is None or o['id'] == item_id)
        and (not kind or o['kind'] == kind)), key=lambda o: o['id'])
    result.update(collection='schematic_objects', coordinate_units='mm', objects=rows[offset:offset+limit],
        total_items=len(rows), offset=offset, page_complete=True,
        query=dict(sheet=result['sheet'], ref=ref, item_id=item_id, kind=kind, limit=limit, expected_sha256=digest),
        measurement_scope='Actual objects on the selected sheet and filters; missing objects on a partial page are not absent wires or labels.')
    update_page(result)
    return result


def update_page(result):
    count = len(result['objects']); offset = result['offset']; total = result['total_items']
    more = offset + count < total
    result.update(returned_items=count, has_more=more, next_offset=offset+count if more else None,
        section_complete=offset == 0 and count == total,
        next_call={'tool':'inspect_schematic', 'arguments':dict(result['query'], offset=offset+count,
            limit=max(1,min(result['query']['limit'],count)))} if more else None)


def output_schema():
    from tools.observation_schema import common_observation_schema
    schema = common_observation_schema()
    schema['required'] += ['sheet','schematic_sha256','collection','objects','offset','total_items','returned_items','has_more','next_call','section_complete']
    schema['properties'].update(sheet={'type':'string'}, schematic_sha256={'type':'string'},
        collection={'const':'schematic_objects'}, coordinate_units={'const':'mm'},
        objects={'type':'array','items':{'type':'object','required':['id','kind'],
            'properties':{'id':{'type':'string'},'kind':{'type':'string'},'pins':{'type':'object'},'fields':{'type':'object'},'points':{'type':'array'}}}},
        offset={'type':'integer','minimum':0}, total_items={'type':'integer','minimum':0},
        returned_items={'type':'integer','minimum':0}, has_more={'type':'boolean'},
        page_complete={'type':'boolean'}, section_complete={'type':'boolean'}, next_call={'type':['object','null']})
    schema['description']='Complete native objects per delivered page; follow next_call for the remaining query, including after byte compaction. Filtered coverage does not cover the whole sheet.'
    return schema
