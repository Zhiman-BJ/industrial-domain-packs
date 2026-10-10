"""Controller-side export of native Kimi calls matched to execution receipts.

Printed CLI streams can omit ToolCallPart fragments. Keep native model-visible
results separate from full controller observations; neither certifies reasoning
or circuit correctness. This module is not a public model tool.
"""
import hashlib
import json
import uuid
from pathlib import Path


def read_rows(path, root, errors):
    with path.open() as stream:
        for line, raw in enumerate(stream, 1):
            source = {'file': str(path.relative_to(root)), 'line': line}
            try:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError('Expected object')
            except ValueError:
                errors.append(dict(source, issue='Malformed trace record'))
                continue
            yield source, row


def client_schema_rejection(result):
    """Recognize only the native client's pre-dispatch JSON-schema rejection."""
    if not isinstance(result, dict) or result.get('is_error') is not True:
        return False
    if result.get('output') not in ('', []):
        return False
    message = result.get('message')
    display = result.get('display')
    return (isinstance(message, str) and message.startswith('Error validating JSON arguments:')
            and isinstance(display, list) and any(
                isinstance(item, dict) and item.get('text') == 'Invalid arguments' for item in display))


def export(directory, destination=None):
    root = Path(directory).resolve()
    errors, calls, receipts, hashes, matched = [], [], {}, {}, set()
    rejected = 0
    source = root / 'model-trajectory.jsonl'
    wires = sorted((root / 'kimi-native/sessions').glob('*/*/wire.jsonl'))
    for path in [source, *wires]:
        if path.is_file():
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if source.is_file():
        for location, event in read_rows(source, root, errors):
            if event.get('event') not in ('tool_action', 'dispatch_error') or event.get('actor') == 'controller':
                continue
            key = (event.get('round_id'), event.get('mcp_request_id'))
            if None in key or key in receipts:
                errors.append(dict(location, issue='Missing or duplicate controller request identity'))
                continue
            receipts[key] = (location, event)
    else:
        errors.append({'issue': 'Controller trajectory missing'})
    if not wires:
        errors.append({'issue': 'Native wire missing; printed stream is not a substitute'})
    for path in wires:
        by_id, active = {}, None
        for location, row in read_rows(path, root, errors):
            message = row.get('message') or {}
            kind, value = message.get('type'), message.get('payload') or {}
            if kind in ('StepBegin', 'TurnBegin', 'TurnEnd'):
                active = None
            elif kind == 'ToolCall':
                ident, function = value.get('id'), value.get('function') or {}
                if not isinstance(ident, str) or ident in by_id:
                    errors.append(dict(location, issue='Missing or duplicate native call ID'))
                    active = None
                    continue
                active = dict(call_id=ident, tool=function.get('name'),
                              arguments_text=function.get('arguments') or '',
                              native_call=location, fragment_lines=[], result=None)
                by_id[ident] = active
                calls.append(active)
            elif kind == 'ToolCallPart':
                fragment = value.get('arguments_part')
                if active is None or not isinstance(fragment, str) or not isinstance(active['arguments_text'], str):
                    errors.append(dict(location, issue='Orphan or invalid native argument fragment'))
                    continue
                active['arguments_text'] += fragment
                active['fragment_lines'].append(location['line'])
            elif kind == 'ToolResult':
                call = by_id.get(value.get('tool_call_id'))
                if call is None or call['result'] is not None:
                    errors.append(dict(location, issue='Orphan or duplicate native result'))
                    continue
                call['result'] = value.get('return_value')
                call['native_result'] = location
                if call is active:
                    active = None
    transitions = []
    for call in calls:
        location = call['native_call']
        try:
            arguments = json.loads(call['arguments_text'])
            if not isinstance(arguments, dict):
                raise ValueError('Expected object arguments')
        except (ValueError, TypeError):
            errors.append(dict(location, issue='Native arguments are incomplete or invalid'))
            continue
        result = call['result']
        if not isinstance(result, dict):
            errors.append(dict(location, issue='Native tool result missing'))
            continue
        if client_schema_rejection(result):
            rejected += 1
            transitions.append(dict(kind='client_rejection', tool_call_id=call['call_id'],
                tool=call['tool'], arguments=arguments, native_call=location,
                fragment_lines=call['fragment_lines'], native_result=call['native_result'],
                model_visible_result=result, operation_executed=False,
                controller_receipt=None, reward=None, narrative_status='UNASSESSED'))
            continue
        observations = []
        for item in result.get('output', []) if isinstance(result.get('output'),list) else []:
            try:
                observation = json.loads(item.get('text', ''))
            except (ValueError, TypeError):
                continue
            if isinstance(observation, dict) and isinstance(observation.get('trace_context'), dict):
                observations.append(observation)
        if len(observations) != 1:
            errors.append(dict(location, issue='Missing or ambiguous native receipt context'))
            continue
        context = observations[0]['trace_context']
        key = (context.get('round_id'), context.get('mcp_request_id'))
        if key not in receipts or key in matched:
            errors.append(dict(location, issue='Missing or multiply linked controller receipt'))
            continue
        receipt_location, receipt = receipts[key]
        name = call['tool']
        if (arguments != receipt.get('arguments') or not isinstance(name, str) or
                not (name == receipt.get('tool') or name.endswith('__' + str(receipt.get('tool'))))):
            errors.append(dict(location, issue='Native call and controller receipt differ'))
            continue
        if observations[0].get('returncode') != (receipt.get('observation') or {}).get('returncode'):
            errors.append(dict(location, issue='Native and controller return codes differ'))
            continue
        matched.add(key)
        transitions.append(dict(kind='executed', tool_call_id=call['call_id'], tool=receipt['tool'], arguments=arguments,
            action=receipt.get('action'), round_id=key[0], mcp_request_id=key[1],
            native_call=location, fragment_lines=call['fragment_lines'], native_result=call['native_result'],
            model_visible_result=result, controller_receipt=receipt, controller_source=receipt_location,
            reward=None, narrative_status='UNASSESSED'))
    for key in receipts.keys() - matched:
        errors.append(dict(receipts[key][0], issue='Controller action not linked to native wire'))
    if not calls:
        errors.append({'issue': 'No native tool calls to export'})
    for name, expected in hashes.items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append({'file': name, 'issue': 'Trace changed during export'})
    report = dict(schema_version=1, status='PASS' if not errors else 'NEEDS_REVIEW',
        scope='Native call/receipt integrity only; no reasoning, design or training admission verdict.',
        source_sha256=hashes, calls=len(calls), linked=len(matched), client_rejections=rejected, errors=errors,
        narrative_status='UNASSESSED', formal_training_admission=False, actions_path=None)
    target = Path(destination) if destination is not None else root / 'exports' / uuid.uuid4().hex
    target.mkdir(parents=True, exist_ok=False)
    if not errors:
        actions = target / 'actions.jsonl'
        with actions.open('w') as stream:
            for transition in transitions:
                stream.write(json.dumps(transition, ensure_ascii=False) + '\n')
        report.update(actions_path=str(actions), actions_sha256=hashlib.sha256(actions.read_bytes()).hexdigest())
    from tools.session_recovery import durable_write
    durable_write(target / 'manifest.json', report)
    return report
