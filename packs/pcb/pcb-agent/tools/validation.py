"""Independent, evidence-scoped checks. PASS, FAIL, UNKNOWN and NOT_APPLICABLE.

No name-only schematic checks and no missing-tool-is-success behavior. Supplied
datasheet facts and circuit requirements are inputs, not facts invented by these
checks. General CAD checks do not claim functional, EMC or thermal sign-off.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from tools import design, kicad_cli as kc
from tools.values import equivalent


def result(status, scope, issues=None, **evidence):
    if status not in {'PASS','FAIL','UNKNOWN','NOT_APPLICABLE'}:
        raise ValueError('Invalid check status')
    return dict(status=status, ok=status=='PASS', scope=scope, issues=issues or [], **evidence)


def aggregate(checks: dict) -> dict:
    """No evidence and unknown checks cannot become a successful verdict."""
    statuses = [c['status'] for c in checks.values()]
    status = 'FAIL' if 'FAIL' in statuses else 'UNKNOWN' if not statuses or 'UNKNOWN' in statuses or all(x == 'NOT_APPLICABLE' for x in statuses) else 'PASS'
    return result(status, 'aggregate of the listed checks only', checks=checks)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _official(kind, source, out):
    source = Path(source)
    if not source.is_file(): return result('UNKNOWN', kind, ['Input file missing'], available=False, report_valid=False)
    before = file_hash(source)
    from tools import hierarchy
    dependencies=hierarchy.hashes(source) if kind=='erc' else {}
    if 'MISSING' in dependencies.values():return result('UNKNOWN',kind,['Hierarchical sheet missing'],sources=dependencies,available=False,report_valid=False)
    r = getattr(kc, kind)(source, out)
    if not source.is_file() or file_hash(source) != before or (kind=='erc' and dependencies!=hierarchy.hashes(source)): return result('UNKNOWN', kind, ['Input changed during check'],available=bool(r.get('available')),report_valid=False)
    if not r.get('available') or not r.get('report_valid'):
        issues=r.get('report_errors') or ['Check unavailable or invalid report']
        if not r.get('available'):issues=issues+[f"Native check failed to complete (returncode={r.get('returncode', 'unknown')})"]
        if r.get('raw_stderr','').strip():issues=issues+['Native diagnostic: '+r['raw_stderr'].strip()[-1500:]]
        return result('UNKNOWN', kind, issues, report=r, source_sha256=before,source_path=str(source))
    errors = [v for v in r['violations'] if v.get('severity') == 'error' or v.get('group') in ('unconnected_items', 'schematic_parity')]
    warnings=[entry for entry in r['violations'] if entry.get('severity')=='warning']
    return result('FAIL' if errors else 'PASS', kind, errors, warnings=warnings,
                  check_kind='native_'+kind,
                  does_not_establish=['functional behavior','device qualification','hardware compliance']+( ['physical copper connectivity'] if kind=='erc' else []),
                  report=r, source_sha256=before,source_path=str(source))


def run_erc(schematic, report_path):
    """Run official ERC; return UNKNOWN when no fresh valid report was produced."""
    return _official('erc', schematic, report_path)


def run_drc(board, report_path):
    """Run full official DRC including all unconnected and parity errors."""
    return _official('drc', board, report_path)


def export_netlist(schematic, path):
    """Export and parse KiCad XML netlist, with source fingerprint."""
    if not Path(schematic).is_file(): return result('UNKNOWN','netlist',['Schematic missing'])
    before = file_hash(schematic)
    from tools import hierarchy
    dependencies=hierarchy.hashes(schematic)
    if 'MISSING' in dependencies.values():return result('UNKNOWN','netlist',['Hierarchical sheet missing'],sources=dependencies)
    r = kc.export_netlist(schematic, path)
    if not r.ok or not Path(path).is_file():
        return result('UNKNOWN','netlist',['KiCad netlist export failed'], stderr=r.stderr)
    try:
        data = parse_netlist(path)
    except (ValueError, ET.ParseError) as e:
        return result('UNKNOWN','netlist',[str(e)])
    if before != file_hash(schematic) or dependencies!=hierarchy.hashes(schematic): return result('UNKNOWN','netlist',['Schematic changed during export'])
    return result('PASS','netlist export', data=data, source_sha256=before, netlist_sha256=file_hash(path),schematic_sources=dependencies)


def parse_netlist(path):
    """Parse actual KiCad XML connectivity; duplicate/conflicting pin nodes fail."""
    root = ET.parse(path).getroot()
    if root.tag != 'export' or root.find('nets') is None:
        raise ValueError('Not a KiCad XML netlist')
    comps = {e.attrib['ref']: {'value':e.findtext('value',''), 'footprint':e.findtext('footprint','')} for e in root.findall('./components/comp')}
    for element in root.findall('./components/comp'):
        source=element.find('libsource')
        if source is not None:comps[element.attrib['ref']]['symbol']=source.get('lib','')+':'+source.get('part','')
    # KiCad XML represents an empty Value field as '~'; the native PCB keeps ''.
    for comp in comps.values():
        if comp['value']=='~':comp['value']=''
    pins = {}
    for net in root.findall('./nets/net'):
        name = _net(net.attrib.get('name',''))
        if not name: raise ValueError('Unnamed network')
        for node in net.findall('node'):
            key = f"{node.attrib['ref']}:{node.attrib['pin']}"
            if key in pins and pins[key] != name: raise ValueError(f'Pin on multiple nets: {key}')
            pins[key] = name
    return {'components':comps, 'pins':pins}


def _net(name):
    # Root sheet prefix is irrelevant; nested sheet paths remain distinct.
    return name[1:] if name.startswith('/') else name


def intended_connections(spec):
    return {f"{p['ref']}:{p.get('pad_map',{}).get(pin,pin)}": n for p in spec['parts']
            if not p.get('schematic_only') for pin,n in p.get('nets',{}).items()}


def compare_spec_netlist(spec, data):
    """Compare component/pin/net membership, nominal values and footprint choices."""
    want = intended_connections(spec); got = data['pins']; issues=[]
    physical = {p['ref']:p for p in spec['parts'] if not p.get('schematic_only')}
    for ref,p in physical.items():
        comp=data['components'].get(ref)
        if comp is None: issues.append(f'Missing schematic component {ref}'); continue
        if p.get('value') and not equivalent(comp.get('value'), p['value'], comp.get('symbol', p.get('symbol'))):
            issues.append(f'{ref}: value differs from spec')
        if p.get('footprint') and p['footprint'] != comp.get('footprint'):
            issues.append(f'{ref}: footprint differs from spec')
    for ref in set(data['components'])-set(physical):
        if not any(p['ref']==ref and p.get('schematic_only') for p in spec['parts']):
            issues.append(f'Unexpected schematic component {ref}')
    for key,net in want.items():
        if _net(got.get(key,'')) != _net(net): issues.append(f'{key}: expected {net}, got {got.get(key)}')
    for key,net in got.items():
        ref=key.rsplit(':',1)[0]
        if ref not in physical or key in want: continue
        p=physical[ref]
        nc={str(p.get('pad_map',{}).get(k,k)) for k in p.get('no_connect',[])}
        # Only explicit NC pins may be absent from intended connections.
        if not (key.rsplit(':',1)[1] in nc and ('unconnected-' in net)):
            issues.append(f'Unexpected schematic connection {key}: {net}')
    return result('FAIL' if issues else 'PASS','spec ↔ schematic pin membership',issues)


def compare_schematic_pcb(schematic, board, report_dir, spec=None):
    """Export the schematic, compare every physical pin/pad/net and component."""
    from tools import pcb_editor as ed
    out=Path(report_dir);out.mkdir(parents=True,exist_ok=True)
    exp=export_netlist(schematic,out/'schematic.xml')
    if exp['status']!='PASS':return exp
    try:
        b=ed.load(board) if isinstance(board,(str,Path)) else board
        got={}; refs={}
        for fp in b.GetFootprints():
            ref=str(fp.GetReference())
            if ref in refs:raise ValueError(f'Duplicate PCB component {ref}')
            refs[ref]={'value':str(fp.GetValue()),'footprint':str(fp.GetFPIDAsString())}
            for pad in fp.Pads():
                pin=str(pad.GetNumber()); net=str(pad.GetNetname());key=f'{ref}:{pin}'
                if not pin:continue
                if key in got and got[key]!=net:raise ValueError(f'Conflicting same-number pads: {key}')
                got[key]=net
    except Exception as e:return result('UNKNOWN','schematic ↔ PCB',[str(e)])
    data=exp['data'];issues=[]
    expected=data['pins']
    schematic_only={p['ref'] for p in (spec or {}).get('parts',[]) if p.get('schematic_only')}
    allowed_mechanical={p['ref'] for p in (spec or {}).get('parts',[]) if p.get('board_only')}
    for key,net in expected.items():
        ref=key.rsplit(':',1)[0]
        if ref in schematic_only or ref.startswith('#'):continue
        actual=got.get(key)
        if 'unconnected-' in net and not actual:continue
        if actual is None or _net(actual)!=_net(net):issues.append(f'{key}: schematic={net}, PCB={actual}')
    for key,net in got.items():
        if net and key not in expected:issues.append(f'Unexpected connected PCB pad {key}: {net}')
    wanted_refs=set(data['components'])-schematic_only
    for ref in wanted_refs-set(refs):issues.append(f'Missing PCB component {ref}')
    for ref in set(refs)-wanted_refs-allowed_mechanical:issues.append(f'Unexpected PCB component {ref}')
    for ref in wanted_refs & set(refs):
        for k in ('value','footprint'):
            if data['components'][ref].get(k) and data['components'][ref][k]!=refs[ref].get(k):issues.append(f'{ref}: {k} differs')
    if spec:
        chk=compare_spec_netlist(spec,data)
        issues.extend(chk['issues'])
    return result('FAIL' if issues else 'PASS','schematic ↔ PCB declared pin/pad/net membership',issues,
                  check_kind='logical_schematic_pcb_membership',
                  does_not_establish=['physical copper continuity','absence of copper shorts','functional behavior'],
                  board_sha256=file_hash(board) if isinstance(board,(str,Path)) else None,
                  schematic_sha256=exp['source_sha256'])


def check_component_datasheet(spec, ref, facts=None):
    """Compare declared MPN/pins/package to supplied, source-backed datasheet facts.

    This does not download/interpret a PDF or authenticate supplied facts.
    """
    p=next(p for p in spec['parts'] if p['ref']==ref)
    if p.get('schematic_only'):return result('NOT_APPLICABLE','datasheet',['Schematic-only symbol'])
    from tools import knowledge
    supplied=facts or p.get('datasheet_facts')
    facts=dict(supplied) if supplied else knowledge.component_facts(p.get('mpn',''),spec.get('knowledge_registry'))
    # Caller-authored metadata cannot select a privileged evidence package.
    if supplied: facts.pop('_package_root',None)
    required=('source','retrieved_at','mpn','pins','footprint')
    if not facts or any(not facts.get(k) for k in required) or not p.get('mpn'):
        return result('UNKNOWN','datasheet facts',[f'{ref}: source-backed MPN, pins and footprint facts required'])
    if facts.get('_package_root'):
        from tools import intake
        root=Path(facts['_package_root'])
        manifest=json.loads((root/'package.json').read_text())
        if intake._files(root)!=manifest['files']:
            return result('FAIL','datasheet evidence',['Installed component package changed'])
        evidence=intake.check_evidence(facts,root)
        if evidence['status']!='PASS':return evidence
    issues=[]
    if p['mpn']!=facts['mpn']:issues.append(f'{ref}: MPN mismatch')
    if p.get('footprint')!=facts['footprint']:issues.append(f'{ref}: footprint mismatch')
    if not p.get('pins'):
        try: p=dict(p,pins=knowledge.symbol_facts(p['symbol'])['pins'])
        except Exception as e: return result('UNKNOWN','datasheet facts',[str(e)])
    for pin,v in p['pins'].items():
        if pin not in facts['pins']:issues.append(f'{ref}.{pin}: absent in datasheet facts');continue
        if v!=facts['pins'][pin]:issues.append(f'{ref}.{pin}: pin facts mismatch')
    if set(p['pins'])!=set(facts['pins']):issues.append(f'{ref}: pin set mismatch')
    trusted_package=bool(facts.get('_package_root'))
    if not issues and not trusted_package:
        return result('UNKNOWN','unqualified datasheet facts',
                      [f'{ref}: declarations are consistent but no source-qualified package backs them'],
                      consistency_status='PASS',source=facts['source'])
    return result('FAIL' if issues else 'PASS','source-reviewed package facts consistency; semantic review is external',issues,source=facts['source'],retrieved_at=facts['retrieved_at'])


def electrical_intent_schema(section):
    """Structural contract only; complete values/limits are checked electrically."""
    text={'type':'string','minLength':1};number={'type':'number'}
    metadata={name:{'type':'string'} for name in ('name','source','notes','description')}
    def records(properties,required=()):
        return {'type':'array','items':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}
    if section=='power_tree':
        loads=records(dict(ref=text,current_a=number,voltage_min_v=number,voltage_max_v=number,**metadata),['ref'])
        return records(dict(net=text,voltage_v=number,current_limit_a=number,loads=loads,input_net=text,
                            kind=text,dropout_v=number,**metadata),['net'])
    if section=='interfaces':
        connections=records(dict(ref=text,pin=text,net=text,voltage_v=number,voltage_min_v=number,voltage_max_v=number),['ref','pin','net'])
        components=records(dict(ref=text,value={'type':'string'},nets={'type':'object'}),['ref'])
        return records(dict(connections=connections,required_components=components,**metadata))
    raise ValueError('Expected power_tree or interfaces')


def electrical_intent_issues(section,items):
    from tools.argument_schema import validate
    try:validate(items,electrical_intent_schema(section),section)
    except (ValueError,TypeError,OverflowError) as error:return [str(error)]
    return []


def check_power_tree(spec):
    """Check explicit rail voltages, load ranges, current budgets and dropout."""
    rails=spec.get('power_tree',[])
    malformed=electrical_intent_issues('power_tree',rails)
    if malformed:return result('UNKNOWN','power tree declaration schema',malformed,
                               next_action='Inspect power_tree schema and correct the named declaration; CAD need not be rebuilt')
    declared={n for n,v in spec.get('nets',{}).items() if v.get('role')=='power'}
    # Editable net roles cannot waive the budgets of actual active supply pins.
    # Ground/chassis nets are reference domains, not positive/negative supplies.
    from tools import knowledge
    for part in spec.get('parts',[]):
        if part.get('schematic_only') or not part.get('symbol'):continue
        try:pins=knowledge.symbol_facts(part['symbol'])['pins']
        except (ValueError,FileNotFoundError,KeyError):continue  # CAD qualification handles invalid symbols.
        for number,pin in pins.items():
            net=part.get('nets',{}).get(number)
            if net and pin['type'] in ('power_in','power_out') and spec.get('nets',{}).get(net,{}).get('role') not in ('ground','chassis'):
                declared.add(net)
    if not rails:return result('UNKNOWN' if declared or not spec.get('nets') else 'NOT_APPLICABLE','power tree',['No rail/load specification'])
    issues=[];unknown=[];by={r['net']:r for r in rails}
    if len(by)!=len(rails):issues.append('Duplicate power rail')
    for n in declared-set(by):unknown.append(f'Missing rail budget {n}')
    refs={p['ref'] for p in spec['parts']}
    for r in rails:
        if any(k not in r for k in ('voltage_v','current_limit_a','loads')):
            unknown.append(f"{r['net']}: incomplete voltage/current/load budget");continue
        v=r['voltage_v'];limit=r['current_limit_a'];total=0
        if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (v,limit)) or limit<0:
            issues.append(f"{r['net']}: invalid voltage/current");continue
        for load in r['loads']:
            if load.get('ref') not in refs:issues.append(f"Unknown load {load.get('ref')}")
            if any(k not in load for k in ('current_a','voltage_min_v','voltage_max_v')):
                unknown.append(f"{r['net']}: incomplete load");continue
            vals=[load[k] for k in ('current_a','voltage_min_v','voltage_max_v')]
            if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in vals) or vals[0]<0:
                issues.append('Invalid load range/current');continue
            total+=load['current_a']
            if not load['voltage_min_v']<=v<=load['voltage_max_v']:issues.append(f"{load.get('ref')}: rail voltage outside load range")
        if total>limit:issues.append(f"{r['net']}: {total}A load exceeds {limit}A supply")
        if r.get('input_net'):
            up=by.get(r['input_net'])
            if not up or 'voltage_v' not in up:unknown.append(f"{r['net']}: upstream rail unknown")
            elif r.get('kind')=='linear':
                if 'dropout_v' not in r:unknown.append(f"{r['net']}: dropout not specified")
                elif up['voltage_v']-v<r['dropout_v']:issues.append(f"{r['net']}: insufficient linear-regulator headroom")
    return result('FAIL' if issues else 'UNKNOWN' if unknown else 'PASS','declared DC power budgets only',issues+unknown)


def check_operating_limits(spec):
    """Compare project operating points with source-reviewed recommended ranges.

Operating points are declared per part; supply voltage can be derived from an
explicit power-tree rail and the fact's supply pin. Absolute maximum ratings
are never silently used as recommended operating conditions.
"""
    from tools import knowledge
    malformed=electrical_intent_issues('power_tree',spec.get('power_tree',[]))
    if malformed:return result('UNKNOWN','operating limits require valid rail declarations',malformed)
    checks={};rails={r['net']:r.get('voltage_v') for r in spec.get('power_tree',[])}
    for part in spec['parts']:
        if part.get('schematic_only'):continue
        facts=part.get('datasheet_facts') or knowledge.component_facts(part.get('mpn',''),spec.get('knowledge_registry'))
        ratings=(facts or {}).get('ratings',{});points=dict(part.get('operating',{}));bad=[];missing=[]
        for metric,rule in ratings.items():
            if metric=='supply_voltage_v' and rule.get('pin') and metric not in points:
                voltage=rails.get(part.get('nets',{}).get(rule['pin']))
                if voltage is not None:points[metric]=voltage
        for metric,value in points.items():
            rule=ratings.get(metric)
            if not rule or rule.get('kind')!='recommended':missing.append(metric+': source-reviewed recommended range missing');continue
            if not isinstance(value,(int,float)) or not math.isfinite(value):bad.append(metric+': invalid operating point');continue
            if not all(isinstance(rule.get(k),(int,float)) and math.isfinite(rule[k]) for k in ('min','max')):
                missing.append(metric+': incomplete source range');continue
            if not rule['min']<=value<=rule['max']:bad.append(metric+': outside recommended range')
        for metric in part.get('required_operating_checks',[]):
            if metric not in points:missing.append(metric+': operating point missing')
        checks[part['ref']]=result('FAIL' if bad else 'UNKNOWN' if missing else 'PASS' if points else 'NOT_APPLICABLE',
                                  'declared operating points only',bad+missing,operating_points=points)
    if all(x['status']=='NOT_APPLICABLE' for x in checks.values()):return result('NOT_APPLICABLE','operating limits',['No operating-point checks declared'])
    return aggregate(checks)


def check_interface(spec):
    """Check exact interface pin mapping and supplied voltage ranges/requirements."""
    interfaces=spec.get('interfaces')
    if interfaces is None:return result('UNKNOWN','interfaces',['Interface specification missing'])
    malformed=electrical_intent_issues('interfaces',interfaces)
    if malformed:return result('UNKNOWN','interface declaration schema',malformed,
                               next_action='Inspect interfaces schema and correct the named declaration; CAD need not be rebuilt')
    if not interfaces:return result('NOT_APPLICABLE','interfaces',['No interfaces declared'])
    issues=[];unknown=[];parts={p['ref']:p for p in spec['parts']}
    for interface in interfaces:
        if not interface.get('connections'):unknown.append('Interface has no pin specification')
        for c in interface.get('connections',[]):
            p=parts.get(c.get('ref'),{});net=p.get('nets',{}).get(str(c.get('pin')))
            if net!=c.get('net') or not net:issues.append(f"{c.get('ref')}.{c.get('pin')}: interface mapping mismatch")
            if any(k in c for k in ('voltage_v','voltage_min_v','voltage_max_v')):
                if not all(k in c for k in ('voltage_v','voltage_min_v','voltage_max_v')):unknown.append('Incomplete interface voltage range')
                elif not c['voltage_min_v']<=c['voltage_v']<=c['voltage_max_v']:issues.append('Interface voltage incompatible')
        for req in interface.get('required_components',[]):
            p=parts.get(req['ref'])
            if not p:issues.append(f"Required interface component missing: {req['ref']}");continue
            if req.get('value') and not equivalent(p.get('value'),req['value'],p.get('symbol')):issues.append(f"{req['ref']}: required value differs")
            if req.get('nets') and p.get('nets')!=req['nets']:issues.append(f"{req['ref']}: required connection differs")
    return result('FAIL' if issues else 'UNKNOWN' if unknown else 'PASS','declared interface constraints; not protocol compliance',issues+unknown)


def check_footprints(spec):
    """Load symbols/footprints and validate explicit pin-to-pad assignments."""
    from tools import schematic,pcb_editor as ed,datasheet_search as ds
    issues=[];unknown=[]
    for p in spec['parts']:
        if p.get('schematic_only'):continue
        try:
            node=schematic.resolved_symbol(*p['symbol'].split(':',1))
            pins={n for u in schematic.symbol_units(node).values() for n,*_ in u}
            mapped={str(p.get('pad_map',{}).get(n,n)) for n in pins}
            if len(mapped)!=len(pins):issues.append(f"{p['ref']}: multiple electrical pins mapped to one pad identifier")
            if set(p.get('pad_map',{}))-pins:issues.append(f"{p['ref']}: mapping contains unknown pins")
            if not p.get('footprint'):issues.append(f"{p['ref']}: footprint missing");continue
            ed._need();lib,name=p['footprint'].split(':',1);directory=ds.footprint_dir(lib,name)
            if directory is None:issues.append(f"{p['ref']}: footprint library missing");continue
            fp=ed.pcbnew.FootprintLoad(directory,name)
            if fp is None:issues.append(f"{p['ref']}: footprint cannot load");continue
            pads={str(x.GetNumber()) for x in fp.Pads() if str(x.GetNumber())}
            allowed=set(p.get('mechanical_pads',[]))
            if mapped-pads:issues.append(f"{p['ref']}: pins without pads {sorted(mapped-pads)}")
            if pads-mapped-allowed:issues.append(f"{p['ref']}: extra pads need explicit mechanical/electrical mapping {sorted(pads-mapped-allowed)}")
        except (FileNotFoundError,LookupError,ValueError) as e:issues.append(f"{p['ref']}: {e}")
        except Exception as e:unknown.append(f"{p['ref']}: {e}")
    return result('FAIL' if issues else 'UNKNOWN' if unknown else 'PASS','CAD pin/pad identifiers; geometry still needs datasheet review',issues+unknown)


def validate_measurement_bounds(assertions):
    """Validate inclusive/strict numerical limits shared by solver adapters."""
    if not isinstance(assertions,dict):raise ValueError('Assertions must be an object')
    allowed={'min','max','min_exclusive','max_exclusive'}
    for name,limits in assertions.items():
        if (not isinstance(name,str) or not name or not isinstance(limits,dict)
                or not limits or set(limits)-allowed
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in limits.values())):
            raise ValueError('Assertions require named finite inclusive or exclusive bounds')
        low=max(limits.get('min',-math.inf),limits.get('min_exclusive',-math.inf))
        high=min(limits.get('max',math.inf),limits.get('max_exclusive',math.inf))
        if low>high or low==high and (limits.get('min_exclusive')==low or limits.get('max_exclusive')==high):
            raise ValueError('Empty or inverted measurement bounds: '+name)


def finite_measurements(values):
    """Preserve numeric results without leaking NaN/Infinity into JSON receipts."""
    return {name:value if type(value) in (int,float) and math.isfinite(value) else None
            for name,value in values.items()}


def check_measurements(assertions,values,scope='declared analysis measurements only'):
    validate_measurement_bounds(assertions)
    if not assertions:return result('UNKNOWN',scope,['No acceptance bounds'])
    bad=[];missing=[];measurements={}
    for name,limits in assertions.items():
        value=values.get(name)
        if type(value) not in (int,float) or not math.isfinite(value):
            missing.append('Missing measurement '+name)
            measurements[name]=dict(status='UNKNOWN',observed=None,expected=dict(limits))
            continue
        failed=(value<limits.get('min',-math.inf) or value>limits.get('max',math.inf)
                or value<=limits.get('min_exclusive',-math.inf)
                or value>=limits.get('max_exclusive',math.inf))
        measurements[name]=dict(status='FAIL' if failed else 'PASS',observed=value,expected=dict(limits))
        if failed:
            bad.append(f'{name}: measured {value}, required {limits}')
    return result('FAIL' if bad else 'UNKNOWN' if missing else 'PASS',scope,bad+missing,
                  values=finite_measurements(values),measurements=measurements)


def simulate_subcircuit(netlist, report_dir, assertions, timeout=60, compatibility='native'):
    """Run a supplied ngspice deck with named .meas assertions, not a fabricated circuit.

    assertions: {measurement: {min/max/min_exclusive/max_exclusive: number}}. A deck/model is
    executable input; only run trusted local decks. Caller supplies operating
    conditions and models. Missing tools/models/measurements return UNKNOWN.
    """
    path=Path(netlist).resolve();out=Path(report_dir);out.mkdir(parents=True,exist_ok=True)
    if not assertions:return result('UNKNOWN','subcircuit',['No measurement assertions'])
    validate_measurement_bounds(assertions)
    ng=shutil.which('ngspice')
    if not ng or not path.is_file():return result('UNKNOWN','subcircuit',['ngspice or netlist unavailable'])
    before=file_hash(path)
    if compatibility not in ('native','pspice'):raise ValueError('Supported ngspice modes: native, pspice')
    # psa applies PSpice translation to the whole generated deck, including
    # the retained model text. ps alone only translates included files.
    mode=['-D','ngbehavior=psa'] if compatibility=='pspice' else []
    try:
        p=subprocess.run([ng,'-n',*mode,'-b',str(path)],cwd=path.parent,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired as e:
        def decoded(value):
            return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else value or ''
        # Retain convergence/progress diagnostics even when the simulator is
        # killed at its deadline. Timeout cannot be treated as a failed circuit
        # or a passing partial measurement.
        (out/'ngspice.log').write_text(decoded(e.stdout)+'\n'+decoded(e.stderr))
        return result('UNKNOWN','subcircuit',[str(e)],timed_out=True,timeout_s=timeout,
                      log_path=str((out/'ngspice.log').resolve()),netlist_sha256=before)
    except OSError as e:return result('UNKNOWN','subcircuit',[str(e)])
    text=p.stdout+'\n'+p.stderr;(out/'ngspice.log').write_text(text)
    if p.returncode or file_hash(path)!=before:return result('UNKNOWN','subcircuit',['Simulation failed or deck changed'],returncode=p.returncode)
    values={m[0].lower():float(m[1]) for m in re.findall(r'^\s*([\w.]+)\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)',text,re.M)}
    checked=check_measurements(assertions,{name:values.get(name.lower()) for name in assertions},'supplied subcircuit measurements only')
    checked.update(values=finite_measurements(values),netlist_sha256=before)
    return checked
