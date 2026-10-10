"""Index recorded provider text and receipts without inventing or rewriting them."""
import hashlib
import json
from pathlib import Path


def payload(content):
    texts = [content] if isinstance(content, str) else [x.get('text', '') for x in (content or []) if isinstance(x, dict)]
    for text in texts:
        try:
            value = json.loads(text)
            if isinstance(value, dict): return value
        except (ValueError, TypeError): pass
    return {}


def build(directory):
    root = Path(directory); errors = []; events = []
    path = root / 'model-trajectory.jsonl'
    if path.is_file():
        for line, raw in enumerate(path.read_text().splitlines(), 1):
            try: events.append((line, json.loads(raw)))
            except ValueError: errors.append({'file': path.name, 'line': line, 'issue': 'Invalid event JSON'})
    actions = [(line, e) for line, e in events if e.get('event') in ('tool_action', 'dispatch_error')]
    calls = {}; messages = []; matches = set(); streams = {}
    for stream in sorted(root.glob('kimi-stream-*.jsonl')):
        round_id = stream.stem.removeprefix('kimi-stream-')
        streams[stream.name] = hashlib.sha256(stream.read_bytes()).hexdigest()
        for line, raw in enumerate(stream.read_text().splitlines(), 1):
            try: row = json.loads(raw)
            except ValueError:
                # Upstream prints terminal budget records outside stream JSON.
                if raw.startswith('Max number of steps reached:'): continue
                errors.append({'file': stream.name, 'line': line, 'issue': 'Non-JSON provider record'}); continue
            source = {'file': stream.name, 'line': line, 'round_id': round_id}
            if row.get('role') == 'assistant':
                blocks = row.get('content', [])
                thoughts = [{'block': i, 'characters': len(b.get('think', ''))} for i, b in enumerate(blocks) if isinstance(b, dict) and b.get('type') == 'think'] if isinstance(blocks, list) else []
                messages.append(dict(source, role='assistant', thinking_blocks=thoughts))
                for call in row.get('tool_calls') or []:
                    key = (round_id, call.get('id'))
                    if key in calls:
                        errors.append(dict(source, issue='Duplicate provider call id', tool_call_id=call.get('id'))); continue
                    calls[key] = dict(source, tool_call_id=call.get('id'), function=call.get('function'), status='UNRESOLVED')
            elif row.get('role') == 'tool':
                key = (round_id, row.get('tool_call_id')); call = calls.get(key)
                observation = payload(row.get('content')); context = observation.get('trace_context') or {}
                full_path = observation.get('stdout_path')
                candidates = [(n, e) for n, e in actions if
                    (context.get('mcp_request_id') is not None and e.get('mcp_request_id') == context['mcp_request_id'] and e.get('round_id') == context.get('round_id')) or
                    (full_path and (e.get('observation') or {}).get('stdout_path') == full_path)]
                if call is None or len(candidates) != 1:
                    errors.append(dict(source, issue='Missing or ambiguous receipt link', tool_call_id=row.get('tool_call_id'))); continue
                n, event = candidates[0]
                function=call.get('function') or {}
                try:
                    arguments=function.get('arguments',{})
                    if isinstance(arguments,str):arguments=json.loads(arguments)
                except (ValueError,TypeError):arguments=None
                if arguments != event.get('arguments'):
                    errors.append(dict(source,issue='Print-stream arguments differ from authoritative controller receipt; use native wire for export',tool_call_id=call.get('tool_call_id')))
                name=function.get('name','')
                if name != event.get('tool') and not name.endswith('__'+str(event.get('tool'))):
                    errors.append(dict(source,issue='Print-stream tool name differs from receipt',tool_call_id=call.get('tool_call_id')))
                if n in matches:
                    errors.append(dict(source, issue='Receipt linked more than once')); continue
                matches.add(n)
                call.update(status='LINKED', tool_result_line=line, trajectory_line=n, action=event.get('action'),
                            mcp_request_id=event.get('mcp_request_id'), receipt_event=event['event'],
                            operation_outcome=(event.get('observation') or {}).get('operation_outcome'))
    unresolved = [c for c in calls.values() if c['status'] != 'LINKED']
    unlinked = [n for n, e in actions if n not in matches]
    result = {'schema_version': 1, 'status': 'PASS' if not errors and not unresolved and not unlinked else 'NEEDS_REVIEW',
              'scope': 'Recorded message/call/receipt association only; no reasoning or design quality verdict.',
              'streams_sha256': streams, 'messages': messages, 'calls': list(calls.values()),
              'unlinked_action_lines': unlinked, 'errors': errors}
    from tools.session_recovery import durable_write
    from tools import native_trace
    result['native_export'] = native_trace.export(root)
    durable_write(root / 'trajectory-index.json', result)
    return {'status': result['status'], 'path': str(root / 'trajectory-index.json'),
            'calls': len(calls), 'linked': len(matches), 'unresolved': len(unresolved), 'unlinked_actions': len(unlinked),
            'native_export': result['native_export']}
