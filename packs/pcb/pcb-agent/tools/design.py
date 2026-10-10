"""Atomic edits to the authoritative circuit spec; every edit returns a new value.

The input dict is never mutated. Persist with save_spec(), then rebuild derived
KiCad outputs. A changed fingerprint invalidates all prior verification evidence.
These functions do not silently edit already-routed board files.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from tools.argument_schema import InputError


def fingerprint(spec: dict) -> str:
    """Stable digest of electrical and physical design intent."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def new_design(width_mm: float, height_mm: float, layers: int = 2) -> dict:
    """Create a circuit spec with no implicit factory or electrical defaults."""
    s = {'schema_version': 1, 'board': {'w': width_mm, 'h': height_mm, 'layers': layers},
         'parts': [], 'nets': {}, 'interfaces': [], 'power_tree': []}
    validate(s)
    return s


def _part(spec, ref):
    return next((p for p in spec.get('parts', []) if p['ref'] == ref), None)


def _require(spec, ref):
    p = _part(spec, ref)
    if p is None:
        raise InputError('UNKNOWN_COMPONENT','arguments.ref',ref,
            {'existing_refs':[p['ref'] for p in spec.get('parts',[])][:40]},
            'Use an existing candidate reference, or add_component if a new part is intended.',f'Unknown component: {ref}')
    return p


def _pin(p, pin):
    pin = str(pin)
    if not pin:
        raise ValueError('Empty pin identifier')
    from tools.knowledge import symbol_facts
    pins = symbol_facts(p['symbol'])['pins']
    if pin not in pins:
        raise InputError('UNKNOWN_PIN','arguments.pin',pin,
            {'ref':p['ref'],'symbol':p['symbol'],'canonical_pin_numbers':list(pins)[:64]},
            'Use a canonical pin number; inspect_symbol gives the pin names and electrical roles.',
            f"Unknown pin {p['ref']}.{pin}; inspect_symbol returns the canonical pin numbers")
    return pin


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    if positive and value <= 0:
        raise ValueError(f'{name} must be positive')


def validate(spec: dict) -> None:
    """Reject malformed intent, duplicate references and connected NC pins."""
    b = spec.get('board', {})
    for key in ('w', 'h'):
        _number(b.get(key), f'board.{key}', True)
    layers = b.get('layers', 2)
    if isinstance(layers, bool) or not isinstance(layers, int) or layers < 2 or layers > 32 or layers % 2:
        raise ValueError('This backend supports an even copper layer count from 2 to 32')
    refs = set()
    for p in spec.get('parts', []):
        ref = p.get('ref')
        if not isinstance(ref, str) or not ref.strip() or '*' in ref or ref in refs:
            raise ValueError(f'Invalid or duplicate reference: {ref}')
        refs.add(ref)
        if not p.get('symbol') or ':' not in p['symbol']:
            raise ValueError(f'{ref}: fully qualified symbol required')
        for pin, net in p.get('nets', {}).items():
            if not isinstance(pin, str) or not pin:raise ValueError('Nonempty string pin number required')
            if not isinstance(net, str) or not net.strip():
                raise ValueError(f'{ref}.{pin}: empty net')
        for pin in p.get('no_connect', []):
            if not isinstance(pin, str) or not pin:raise ValueError('Nonempty string pin number required')
            if str(pin) in p.get('nets', {}):
                raise ValueError(f'{ref}.{pin}: both connected and NC')
        if p.get('at') is not None:
            if len(p['at']) != 2:
                raise ValueError('at must contain x and y in mm')
            for v in p['at']:
                _number(v, 'placement')
        if p.get('rot') is not None:
            _number(p['rot'], 'rotation')
        if p.get('side', 'top') not in ('top', 'bottom'):
            raise ValueError('side must be top or bottom')
    from tools import hierarchy
    hierarchy.validate_spec(spec)
    fingerprint(spec)  # also reject non-JSON data and NaN


def _finish(spec):
    validate(spec)
    return spec


def add_component(spec: dict, ref: str, symbol: str, value: str = '', footprint: str = '', pins: dict | None = None) -> dict:
    """Add a disconnected component. Legacy pin metadata cannot define connectivity."""
    s = copy.deepcopy(spec)
    if _part(s, ref):
        raise ValueError(f'Duplicate reference: {ref}')
    from tools.knowledge import symbol_facts
    canonical = symbol_facts(symbol)['pins']
    if pins is not None and (not isinstance(pins, dict) or any(
            key not in canonical or not isinstance(fact, dict) or
            any(field not in ('name','type') or canonical[key].get(field)!=value for field,value in fact.items())
            for key,fact in pins.items())):
        raise ValueError('pins is library metadata, not connections; use inspect_symbol then connect_pin')
    s.setdefault('parts', []).append({'ref': ref, 'symbol': symbol, 'value': value,
                                     'footprint': footprint, 'nets': {}, 'no_connect': []})
    return _finish(s)


def replace_symbol(spec: dict, ref: str, symbol: str) -> dict:
    """Revise one symbol without dropping its ref, placement, connections or NCs."""
    from tools.knowledge import symbol_facts
    s=copy.deepcopy(spec);p=_require(s,ref);pins=symbol_facts(symbol)['pins']
    used=set(p.get('nets',{}))|set(p.get('no_connect',[]))|set(p.get('pad_map',{}))
    if used-set(pins):raise ValueError('New symbol lacks used pins: '+', '.join(sorted(used-set(pins))))
    p['symbol']=symbol;p.pop('pins',None)
    return _finish(s)


def remove_component(spec: dict, ref: str) -> dict:
    """Remove a component; outstanding interface/power requirements stay visible."""
    s = copy.deepcopy(spec)
    _require(s, ref)
    s['parts'] = [p for p in s['parts'] if p['ref'] != ref]
    return _finish(s)


def add_net(spec: dict, name: str, role: str = 'signal', **properties) -> dict:
    """Declare a net and its electrical properties explicitly."""
    s = copy.deepcopy(spec)
    if not name or name in s.setdefault('nets', {}):
        raise ValueError(f'Empty or duplicate net: {name}')
    if role not in ('signal', 'power', 'ground', 'chassis'):
        raise ValueError('Unknown net role')
    s['nets'][name] = dict(properties, role=role)
    return _finish(s)


def remove_net(spec: dict, name: str) -> dict:
    """Remove an unused net declaration; connected pins must be disconnected first."""
    s=copy.deepcopy(spec)
    if name not in s.get('nets',{}):raise ValueError('Unknown net: '+name)
    if any(name in p.get('nets',{}).values() for p in s['parts']):raise ValueError('Disconnect all pins before removing net')
    del s['nets'][name]
    # Keep outstanding analysis/interface requirements; the verifier reports them.
    return _finish(s)


def rename_net(spec: dict, old: str, new: str) -> dict:
    """Rename a net and all typed references without changing connectivity."""
    s = copy.deepcopy(spec)
    used = set(s.get('nets', {})) | {n for p in s['parts'] for n in p.get('nets', {}).values()}
    if old not in used or not new or new in used:
        raise ValueError('Unknown source net or conflicting target name')
    meta = s.setdefault('nets', {}).pop(old, {})
    s['nets'][new] = meta
    for p in s['parts']:
        p['nets'] = {k: new if n == old else n for k, n in p.get('nets', {}).items()}
    # Only change typed net references; never arbitrary component text.
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k in ('net', 'input_net', 'output_net', 'reference_net', 'net_p', 'net_n') and v == old:
                    obj[k] = new
                elif k in ('nets', 'members') and isinstance(v, list):
                    obj[k] = [new if x == old else x for x in v]
                else:
                    walk(v)
        elif isinstance(obj, list):
            for v in obj: walk(v)
    for k in ('interfaces', 'power_tree', 'constraints','postroute'):
        walk(s.get(k, {}))
    post=s.get('postroute',{})
    if old in post.get('nets',{}):post['nets'][new]=post['nets'].pop(old)
    for test in s.get('analysis',{}).get('tests',[]):
        if test.get('ground')==old:test['ground']=new
        for source in test.get('sources',[]):
            for key in ('p','n'):
                if source.get(key)==old:source[key]=new
            if 'value' in source:source['value']=source['value'].replace('{'+old+'}','{'+new+'}')
        test['measures']={k:val.replace('{'+old+'}','{'+new+'}') for k,val in test.get('measures',{}).items()} if 'measures' in test else test.get('measures',{})
        geometry=test.get('geometry',{});walk(geometry)
        if old in geometry.get('nets',{}):geometry['nets'][new]=geometry['nets'].pop(old)
    return _finish(s)


def connect_pin(spec: dict, ref: str, pin: str, net: str) -> dict:
    """Connect a pin; remove its NC marker, reject unknown declared nets."""
    s = copy.deepcopy(spec); p = _require(s, ref); pin = _pin(p, pin)
    if net not in s.get('nets', {}):
        raise InputError('UNKNOWN_NET','arguments.net',net,{'declared_nets':list(s.get('nets',{}))[:40]},
            'Use a declared candidate net, or call add_net for the intended new network before connecting this pin.',
            f'Declare net {net} with add_net first')
    p.setdefault('nets', {})[pin] = net
    p['no_connect'] = [x for x in p.get('no_connect', []) if str(x) != pin]
    return _finish(s)


def disconnect_pin(spec: dict, ref: str, pin: str) -> dict:
    """Disconnect a pin without declaring that leaving it open is intentional."""
    s = copy.deepcopy(spec); p = _require(s, ref); pin = _pin(p, pin)
    p.setdefault('nets', {}).pop(pin, None)
    return _finish(s)


def add_no_connect(spec: dict, ref: str, pin: str) -> dict:
    """Mark an already disconnected pin intentionally NC; never cut a wire silently."""
    s = copy.deepcopy(spec); p = _require(s, ref); pin = _pin(p, pin)
    if pin in p.get('nets', {}):
        raise ValueError('Disconnect the pin explicitly before marking NC')
    p['no_connect'] = sorted(set(p.get('no_connect', [])) | {pin})
    return _finish(s)


def set_component_value(spec: dict, ref: str, value: str) -> dict:
    """Update value; datasheet/functional checks must be rerun."""
    s = copy.deepcopy(spec); _require(s, ref)['value'] = value
    return _finish(s)


def assign_footprint(spec: dict, ref: str, footprint: str, pad_map: dict | None = None) -> dict:
    """Assign a CAD footprint; optional pin-to-pad map must be explicit."""
    s = copy.deepcopy(spec); p = _require(s, ref)
    if ':' not in footprint: raise ValueError('Fully qualified footprint required')
    p['footprint'] = footprint
    p['pad_map'] = pad_map or {}
    return _finish(s)


def add_power_symbol(spec: dict, ref: str, symbol: str, net: str) -> dict:
    """Add a schematic-only power symbol/PWR_FLAG after verifying the supply path."""
    if not symbol.startswith('power:'):
        raise ValueError('Use a power library symbol')
    # KiCad power symbols use their Value as the global net label. The
    # candidate net name must therefore be written into the generated symbol.
    value = symbol.split(':', 1)[1] if symbol == 'power:PWR_FLAG' else net
    s = add_component(spec, ref, symbol, value=value)
    s = connect_pin(s, ref, '1', net)
    _require(s, ref)['schematic_only'] = True
    return s


def place_component(spec: dict, ref: str, x_mm: float, y_mm: float, rotation: float = 0, side: str = 'top') -> dict:
    """Set placement intent; regeneration invalidates old routing."""
    s = copy.deepcopy(spec); p = _require(s, ref)
    if p.get('locked'): raise ValueError(f'{ref} is locked')
    p.update(at=[x_mm, y_mm], rot=rotation, side=side)
    return _finish(s)


def lock_component(spec: dict, ref: str, locked: bool = True) -> dict:
    """Lock/unlock placement intent."""
    s = copy.deepcopy(spec); _require(s, ref)['locked'] = bool(locked)
    return _finish(s)


def set_net_rule(spec: dict, net: str, **rules) -> dict:
    """Set per-net geometry constraints; widths and spacings are in mm."""
    s = copy.deepcopy(spec)
    if net not in s.get('nets', {}): raise ValueError(f'Unknown net: {net}')
    allowed = {'track_mm', 'clearance_mm', 'via_mm', 'drill_mm', 'dp_width_mm', 'dp_gap_mm'}
    if set(rules) - allowed: raise ValueError('Unknown rule field')
    for k, v in rules.items(): _number(v, k, True)
    s['nets'][net].setdefault('rules', {}).update(rules)
    return _finish(s)


def save_spec(spec: dict, path: str | Path) -> dict:
    """Atomically save a validated spec and return its fingerprint."""
    validate(spec)
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try: same = json.loads(path.read_text()) == spec
        except (ValueError, UnicodeError): same = False
        if same:
            return {'status':'UNCHANGED','path':str(path),'sha256':fingerprint(spec),'changed_files':[], 'verification_required':False}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.spec-')
    try:
        with os.fdopen(fd, 'w') as f: json.dump(spec, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return {'path': str(path), 'sha256': fingerprint(spec), 'downstream': 'REVALIDATE'}
