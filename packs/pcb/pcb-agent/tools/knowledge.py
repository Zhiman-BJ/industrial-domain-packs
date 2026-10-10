"""Versioned engineering facts shipped with the verifier, plus explicit local additions.

Use installed KiCad symbols for CAD pin facts. Exact-MPN rating records can be
added to a versioned JSON registry; unknown ratings remain unknown. The verifier
never equates a package match with an electrically equivalent component.
"""
from pathlib import Path
import json
from tools import schematic

DEFAULT = Path(__file__).parent/'data'/'electrical_rules.json'


def registry(path=None):
    data=json.loads(Path(path or DEFAULT).read_text())
    if data.get('schema_version')!=1:raise ValueError('Unsupported knowledge schema')
    for mpn,facts in data.get('components',{}).items():
        if facts.get('mpn')!=mpn or any(not facts.get(k) for k in ('source','retrieved_at','pins','footprint')):
            raise ValueError(f'Incomplete sourced MPN record: {mpn}')
    return data


def component_facts(mpn, path=None):
    """Return a versioned exact-MPN record; no fuzzy substitution."""
    data=registry(path);facts=data.get('components',{}).get(mpn)
    if facts: return dict(facts,registry_revision=data['revision'])
    if path: return None
    from tools import library
    found=[]
    for root in library.roots():
        p=root/'facts.json'
        if not p.is_file() or not (root/'qualification.json').is_file(): continue
        records=registry(p)
        if mpn in records.get('components',{}):
            found.append(dict(records['components'][mpn],registry_revision=records['revision'],_package_root=str(root)))
    if len(found)>1:
        raise ValueError(f'Multiple qualified records for {mpn}; select an explicit knowledge_registry')
    return found[0] if found else None


def symbol_facts(lib_id):
    """Read all unit/inherited pin facts from the installed KiCad library."""
    node=schematic.resolved_symbol(*lib_id.split(':',1));pins={}
    for unit,items in schematic.symbol_units(node).items():
        for number,name,x,y,kind in items:
            fact={'name':name,'type':kind}
            if number in pins and pins[number]!=fact:raise ValueError(f'Conflicting pin facts: {lib_id}.{number}')
            pins[number]=fact
    return {'symbol':lib_id,'pins':pins,'source':'installed KiCad symbol library','scope':'CAD pin declarations only'}


def check_schematic_rules(spec):
    """Run built-in pin existence, NC, input and output-contention rules."""
    from tools.validation import result
    problems=[];unknown=[];nets={};data=registry()
    for p in spec['parts']:
        try:facts=symbol_facts(p['symbol'])
        except Exception as e:unknown.append(f"{p['ref']}: symbol facts unavailable: {e}");continue
        pins=facts['pins'];connected=p.get('nets',{});nc=set(p.get('no_connect',[]))
        for pin in (set(connected)|nc)-set(pins):problems.append(f"{p['ref']}.{pin}: pin-exists violated")
        for pin in set(connected)&nc:problems.append(f"{p['ref']}.{pin}: nc-exclusive violated")
        for pin,fact in pins.items():
            kind=fact['type']
            if pin not in connected and kind=='power_in':problems.append(f"{p['ref']}.{pin}: power input has no supply connection")
            elif pin not in connected and pin not in nc and kind=='input':problems.append(f"{p['ref']}.{pin}: input neither connected nor explicitly NC")
            if pin in connected:nets.setdefault(connected[pin],[]).append((p['ref'],pin,kind))
    for net,pins in nets.items():
        outputs=[(r,p,t) for r,p,t in pins if t in ('output','power_out') and not any(p['ref']==r and p['symbol']=='power:PWR_FLAG' for p in spec['parts'])]
        if len(outputs)>1:problems.append(f'{net}: multiple actively driven outputs/supplies require explicit review: {outputs}')
    return result('FAIL' if problems else 'UNKNOWN' if unknown else 'PASS','built-in structural electrical rules',problems+unknown,rule_revision=data['revision'])
