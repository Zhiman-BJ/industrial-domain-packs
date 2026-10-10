"""Controller-owned coverage of delivered schematic pages, never candidate claims.

A partial query may justify a fully observed local object edit, but never an
absence claim or whole-sheet replacement. This is an observation boundary,
not a circuit verifier. Files are protected with the other controller receipts.
"""
import hashlib
import json
from pathlib import Path
from tools.argument_schema import InputError
from tools.session_recovery import durable_write

SCHEMATIC_MUTATIONS = frozenset(('generate_schematic','place_schematic_component','set_schematic_field',
    'set_schematic_wire','remove_schematic_item','set_schematic_label'))


def _path(root):
    return Path(root)/'session/observation-coverage.json'


def record(root, tool, delivered):
    if tool=='inspect_board' and delivered.get('returncode')==0:
        return record_board(root,delivered.get('result',{}))
    if tool != 'inspect_schematic' or delivered.get('returncode') != 0:
        return
    result = delivered.get('result', {})
    if result.get('collection') != 'schematic_objects':
        return
    path = _path(root)
    state = json.loads(path.read_text()) if path.exists() else {}
    sheet = result['sheet']; digest = result['schematic_sha256']
    entry = state.get(sheet, {})
    if entry.get('sha256') != digest:
        entry = dict(sha256=digest, queries={}, objects={})
    query = result['query']
    key = json.dumps({k:query.get(k) for k in ('ref','item_id','kind')},sort_keys=True)
    coverage = entry['queries'].setdefault(key, dict(query=query, offsets=[], total=result['total_items']))
    coverage['offsets'] = sorted(set(coverage['offsets']) | set(range(result['offset'], result['offset']+len(result['objects']))))
    coverage['complete'] = len(coverage['offsets']) == coverage['total'] and coverage['offsets'] == list(range(coverage['total']))
    for obj in result['objects']:
        entry['objects'][obj['id']] = {k:obj[k] for k in ('id','ref','unit','kind') if k in obj}
    state[sheet] = entry
    durable_write(path,state)


def require(root, tool, args):
    if tool in BOARD_MUTATIONS:
        return require_board(root,tool,args)
    if tool not in SCHEMATIC_MUTATIONS:
        return
    path = _path(root)
    state = json.loads(path.read_text()) if path.exists() else {}
    sheet = args.get('sheet','board.kicad_sch')
    # Enumerate actual hierarchy, including sheets never observed in this
    # session. Missing coverage must not authorize replacing an existing file.
    if tool == 'generate_schematic':
        from tools import hierarchy
        sheets = [str(p.relative_to(Path(root).resolve())) for p in
                  hierarchy.dependencies(Path(root)/'board.kicad_sch')]
    else:
        sheets = [sheet]
    for sheet in sheets:
        entry = state.get(sheet, {})
        native = Path(root)/sheet
        if not native.exists():continue  # New sheet: no existing drawing to replace.
        current = hashlib.sha256(native.read_bytes()).hexdigest()
        fresh = current == entry.get('sha256')
        complete = [v['query'] for v in entry['queries'].values() if v.get('complete')] if fresh else []
        if any(not any(q.get(k) for k in ('ref','item_id','kind')) for q in complete):
            continue
        item = args.get('item_id')
        if tool != 'generate_schematic' and item and fresh and item in entry['objects']:
            continue
        ref = args.get('ref')
        if tool in ('place_schematic_component','set_schematic_field') and ref and fresh:
            if any(q.get('ref') == ref and not q.get('kind') and not q.get('item_id') for q in complete):
                continue
        expected = {'sheet':sheet,'current_schematic_sha256':current,
                    'required_coverage':'complete current target ref/UUID for local edits, or all objects on affected sheets for regeneration/new wire or label'}
        query = {'sheet':sheet,'expected_sha256':current}
        if tool != 'generate_schematic' and item:query['item_id']=item
        elif tool in ('place_schematic_component','set_schematic_field') and ref:query['ref']=ref
        expected['next_calls'] = [{'tool':'inspect_schematic','arguments':query}]
        error = InputError('MISSING_OBSERVATION' if not entry else 'INCOMPLETE_OBSERVATION' if fresh else 'STALE_OBSERVATION',
            'observation_coverage',{'tool':tool,'sheet':sheet},expected,
            'Use inspect_schematic for the affected sheet/ref/UUID and follow next_call. A partial page cannot prove missing connections. Preserve other objects; rerun ERC/netlist after editing.')
        error.operation_executed = False
        raise error


BOARD_MUTATIONS = frozenset(('generate_pcb','update_pcb','place_component','lock_component','set_component_text',
    'remove_board_item','set_track_width','set_pad_zone_connection','add_track','add_arc','add_via',
    'unroute','add_zone','add_keepout','add_text','set_board_outline','set_layer_count','route_board',
    'route_differential_pair','tune_length'))


def _complete_object(obj):
    if isinstance(obj,dict):
        return not obj.get('omitted_fields') and all(_complete_object(v) for v in obj.values())
    if isinstance(obj,list):return all(_complete_object(v) for v in obj)
    return True


def record_board(root, result):
    digest=result.get('board_sha256')
    if not digest:return
    path=_path(root);state=json.loads(path.read_text()) if path.exists() else {}
    entry=state.get('board.kicad_pcb',{})
    if entry.get('sha256')!=digest:entry=dict(sha256=digest,queries={},objects={})
    objects=[]
    if isinstance(result.get('object'),dict):objects.append(result['object'])
    for key in ('items','parts','copper'):objects.extend(result.get(key,[]))
    for obj in objects:
        if _complete_object(obj) and obj.get('id'):
            entry['objects'][obj['id']]={k:obj[k] for k in ('id','ref','kind','net') if k in obj}
    if result.get('collection') in ('parts','pads','copper','drawings'):
        q=result['query'];key=json.dumps({k:v for k,v in q.items() if k not in ('limit','expected_board_sha256','include_geometry')},sort_keys=True)
        coverage=entry['queries'].setdefault(key,dict(query=q,offsets=[],total=result['total_items']))
        coverage['offsets']=sorted(set(coverage['offsets'])|set(range(result['offset'],result['offset']+len(result['items']))))
        coverage['complete']=coverage['offsets']==list(range(coverage['total']))
    # A complete summary copper array is usable, but an overview shortened by
    # the transport must never become a full query merely because it has IDs.
    if 'copper' in result and not result.get('omitted_fields',{}).get('copper') and _complete_object(result['copper']):
        q=result.get('collection_queries',{}).get('copper',{}).get('arguments',{})
        if q:entry['queries']['summary_copper']=dict(query=q,complete=True)
    state['board.kicad_pcb']=entry;durable_write(path,state)


def require_board(root, tool, args):
    path=_path(root);native=Path(root)/'board.kicad_pcb'
    if not native.exists():return
    entry=json.loads(path.read_text()).get('board.kicad_pcb',{}) if path.exists() else {}
    digest=hashlib.sha256(native.read_bytes()).hexdigest();fresh=entry.get('sha256')==digest
    complete=[v['query'] for v in entry['queries'].values() if v.get('complete')] if fresh else []
    global_collections={q['collection'] for q in complete if not any(q.get(k) for k in ('ref','net_name','layer','bounds_mm'))}
    if global_collections >= {'copper','parts','pads','drawings'}:return
    if tool=='update_pcb' and not args.get('prune_affected') and 'parts' in global_collections:return
    item=args.get('item_id')
    if fresh and item and item in entry['objects']:return
    ref=args.get('ref')
    if fresh and ref and tool in ('place_component','lock_component','set_component_text') and any(o.get('ref')==ref and o.get('kind')=='FOOTPRINT' for o in entry['objects'].values()):return
    net=args.get('net_name') or args.get('net')
    if net and tool in ('add_track','add_arc','add_via','add_zone','unroute','tune_length'):
        if any(q.get('collection')=='copper' and q.get('net_name') in ('',None,net) and not any(q.get(k) for k in ('bounds_mm','layer','ref')) for q in complete):return
    if tool=='update_pcb' and not args.get('prune_affected'):
        queries=[{'collection':'parts'}]
        coverage='complete current parts query for copper-preserving PCB synchronization'
    elif item:
        queries=[{'item_id':item}];coverage='complete current target UUID'
    elif ref and tool in ('place_component','lock_component','set_component_text'):
        queries=[{'collection':'parts','ref':ref}];coverage='complete current target footprint'
    elif net and tool in ('add_track','add_arc','add_via','add_zone','unroute','tune_length'):
        queries=[{'collection':'copper','net_name':net}];coverage='complete current affected-net copper query'
    else:
        queries=[{'collection':c} for c in ('parts','pads','copper','drawings') if c not in global_collections]
        coverage='all current collections for a global board edit'
    calls=[{'tool':'inspect_board','arguments':dict(q,expected_board_sha256=digest)} for q in queries]
    error=InputError('MISSING_OBSERVATION' if not entry else 'INCOMPLETE_OBSERVATION' if fresh else 'STALE_OBSERVATION',
        'observation_coverage',{'tool':tool},{'current_board_sha256':digest,
        'required_coverage':coverage,'next_calls':calls},
        'Read the required scope using expected.next_calls; follow each returned next_call until complete, then retry this edit. Unrelated collections are not required for a local edit or copper-preserving update_pcb.')
    error.operation_executed=False;raise error
