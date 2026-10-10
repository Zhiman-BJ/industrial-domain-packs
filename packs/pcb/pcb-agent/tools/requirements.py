"""Independent declarative design requirements; allows different valid circuit implementations."""
import math
from tools import validation as v
from tools.values import equivalent, numeric as numeric_value, passive_kind


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _strings(value):
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)


def _endpoints(value, minimum=1, maximum=None):
    if not _strings(value) or len(value) < minimum or (maximum is not None and len(value) > maximum):
        return False
    return len(set(value)) == len(value) and all(':' in pin and all(part.strip() for part in pin.rsplit(':', 1)) for pin in value)


def _bounds(value, equality=False):
    if not isinstance(value, dict) or not value or set(value) - ({'min', 'max', 'equals'} if equality else {'min', 'max'}):
        return False
    if not all(_finite(bound) for bound in value.values()):
        return False
    lower, upper = value.get('min', -math.inf), value.get('max', math.inf)
    return lower <= upper and ('equals' not in value or lower <= value['equals'] <= upper)


def validate(requirements):
    """Reject malformed external predicates before inspecting or grading CAD."""
    kinds = {'board': dict, 'board_rules': dict, 'components': list, 'component_groups': list,
             'connections': dict, 'connected_pins': list, 'separate_nets': list,
             'postroute': dict, 'analysis': dict, 'power_tree': list, 'interfaces': list, 'hierarchy': dict}
    issues = []
    if not isinstance(requirements, dict):
        issues.append('Requirements must be a mapping')
    else:
        for field, value in requirements.items():
            if field not in kinds:
                issues.append('Unsupported requirement field: ' + str(field))
            elif not isinstance(value, kinds[field]):
                issues.append(field + ': expected ' + kinds[field].__name__)
    if issues:
        return v.result('UNKNOWN', 'declarative requirement schema', issues)
    for section in ('power_tree','interfaces'):
        if section in requirements:issues.extend(v.electrical_intent_issues(section,requirements[section]))
    for field, limits in requirements.get('board', {}).items():
        if not isinstance(field, str) or not _bounds(limits, equality=True):
            issues.append('board.' + str(field) + ': expected finite, consistent numeric bounds')
    hierarchy=requirements.get('hierarchy',{})
    if set(hierarchy)-{'min_sheets','min_depth'} or any(type(n) is not int or n<0 for n in hierarchy.values()):
        issues.append('hierarchy: nonnegative min_sheets/min_depth required')
    for index, group in enumerate(requirements.get('component_groups', [])):
        if (not isinstance(group, dict) or set(group) - {'symbols', 'min_count', 'max_count'} or
                not _strings(group.get('symbols')) or
                any(type(group[key]) is not int or group[key] < 0 for key in ('min_count', 'max_count') if key in group) or
                group.get('min_count', 0) > group.get('max_count', math.inf)):
            issues.append(f'component_groups[{index}]: expected symbol choices and consistent nonnegative integer counts')
    for index, part in enumerate(requirements.get('components', [])):
        label = f'components[{index}]'
        if (not isinstance(part, dict) or set(part) - {'ref', 'symbols', 'mpns', 'footprints', 'value', 'value_range'} or
                not isinstance(part.get('ref'), str) or not part['ref'].strip()):
            issues.append(label + ': expected a component reference and supported predicates'); continue
        for field in ('symbols', 'mpns', 'footprints'):
            if field in part and not _strings(part[field]):
                issues.append(label + '.' + field + ': expected a nonempty list of strings')
        if 'value' in part and not isinstance(part['value'], str):
            issues.append(label + '.value: expected a string')
        if 'value_range' in part and not _bounds(part['value_range']):
            issues.append(label + '.value_range: expected finite, consistent numeric bounds')
    for net, pins in requirements.get('connections', {}).items():
        if not isinstance(net, str) or not net.strip() or not _endpoints(pins):
            issues.append('connections.' + str(net) + ': expected a net and distinct ref:pin endpoints')
    for index, group in enumerate(requirements.get('connected_pins', [])):
        if not _endpoints(group, minimum=2):
            issues.append(f'connected_pins[{index}]: expected at least two distinct ref:pin endpoints')
    for index, pair in enumerate(requirements.get('separate_nets', [])):
        if not _endpoints(pair, minimum=2, maximum=2):
            issues.append(f'separate_nets[{index}]: expected two distinct ref:pin endpoints')
    return v.result('UNKNOWN' if issues else 'PASS', 'declarative requirement schema', issues)


def check(requirements, spec, actual, measured):
    """Check visible component/net/interface constraints using exported native artifacts.

    Unknown operators fail closed. Requirements specify outcomes, not an entire
    reference design; extra valid components, topology and placement can vary.
    """
    schema = validate(requirements)
    if schema['status'] != 'PASS':
        return schema
    problems, unknown = [], []
    for field, limits in requirements.get('board', {}).items():
        value = measured.get('board', {}).get(field)
        if not _finite(value):
            unknown.append('Missing or non-finite native board metric: ' + field); continue
        tolerance = 0 if field == 'layers' else .02
        if 'equals' in limits and abs(value - limits['equals']) > tolerance:
            problems.append('board.' + field + ' differs')
        if value < limits.get('min', -math.inf) - tolerance or value > limits.get('max', math.inf) + tolerance:
            problems.append('board.' + field + ' outside requirements')
    parts = {p['ref']: p for p in spec['parts']}
    hierarchy=requirements.get('hierarchy',{});sheets=spec.get('sheets',{})
    if len(sheets)<hierarchy.get('min_sheets',0):problems.append('Too few schematic sheets')
    depth=max((len(p.strip('/').split('/')) for p in sheets),default=0)
    if depth<hierarchy.get('min_depth',0):problems.append('Schematic hierarchy is too shallow')
    for group in requirements.get('component_groups',[]):
        count=sum(p.get('symbol') in group['symbols'] for p in actual.get('components',{}).values())
        if not group.get('min_count',0)<=count<=group.get('max_count',math.inf):
            problems.append('Component group count outside allowed range: '+str(group['symbols']))
    for required in requirements.get('components', []):
        ref = required['ref']; part = parts.get(ref); native = actual.get('components', {}).get(ref)
        if not part or not native:
            problems.append('Required component missing: ' + ref); continue
        for field, allowed in [('symbol', 'symbols'), ('mpn', 'mpns'), ('footprint', 'footprints')]:
            value = native.get(field, part.get(field))
            if allowed in required and value not in required[allowed]:
                problems.append(ref + ': ' + field + ' outside allowed choices')
        symbol = native.get('symbol')
        if 'value' in required and not equivalent(native.get('value'), required['value'], symbol):
            problems.append(ref + ': value differs (expected ' + repr(required['value']) +
                            ', native ' + repr(native.get('value')) + ')')
        if 'value_range' in required:
            try:
                value = numeric_value(native.get('value', ''), passive_kind(symbol))
                bounds = required['value_range']
                if not bounds.get('min', -math.inf) <= value <= bounds.get('max', math.inf):
                    problems.append(ref + ': component value outside allowed range; native '+repr(native.get('value'))+
                                    ', parsed SI value '+str(value)+', required '+str(bounds))
            except ValueError as error:
                unknown.append(str(error))
    pins = actual.get('pins', {})
    for group in requirements.get('connected_pins', []):
        missing = [pin for pin in group if not pins.get(pin)]
        if missing:
            problems.append('Required connected endpoints missing: ' + str(missing))
        elif len({pins[pin] for pin in group}) != 1:
            problems.append('Required endpoints are not connected: ' + str(group))
    for net, required_pins in requirements.get('connections', {}).items():
        for pin in required_pins:
            if pins.get(pin, '').lstrip('/') != net.lstrip('/'):
                problems.append(pin + ': required connection to ' + net)
    for pair in requirements.get('separate_nets', []):
        if len(pair) != 2 or any(p not in pins for p in pair):
            problems.append('Required independent endpoints missing: ' + str(pair))
        elif pins[pair[0]] == pins[pair[1]]:
            problems.append('Endpoints must not be shorted: ' + str(pair))
    return v.result('FAIL' if problems else 'UNKNOWN' if unknown else 'PASS', 'Original declarative requirements against native CAD', problems + unknown)
