"""Revision-bound engineering analyses from actual KiCad connectivity.

No task IDs, reference answers, inferred device models, or synthetic IC paths.
An analysis plan states the required categories, operating conditions and bounds.
Missing coverage is UNKNOWN. EMC geometry prechecks never imply EMC compliance.
"""
from pathlib import Path
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from tools import design, validation as v, postroute, models
from tools.evidence_binding import conditions_hash as _conditions_hash, binding_issues, is_sha256


def backend_readiness(spec):
    """Report backend availability before execution; never an engineering PASS.

    Registry entries are trusted runtime configuration, not candidate overrides.
    Only public engine names/kinds are returned, not executable argv or source.
    """
    registry=Path(os.environ.get('PCB_ANALYSIS_BACKENDS','/opt/pcb-analysis-backends.json'))
    try:config=json.loads(registry.read_text()) if registry.is_file() else {}
    except (OSError,ValueError):config={}
    rows=[]
    for test in spec.get('analysis',{}).get('tests',[]):
        backend=test.get('backend');engine=test.get('engine') if backend=='external' else backend
        entry=config.get(engine,{}) if backend=='external' else {}
        registered=backend in ('spice','geometry') or engine in config
        if backend=='spice':installed=bool(shutil.which('ngspice'));supported=True;kind='simulation'
        elif backend=='geometry':installed=True;supported=True;kind='geometry'
        elif backend=='external':
            argv=entry.get('argv',[])
            installed=bool(argv and shutil.which(argv[0]))
            supported=test.get('category') in entry.get('categories',[])
            kind=entry.get('evidence_kind','retained_capture' if entry.get('capture_catalog') else 'unavailable')
        else:installed=False;supported=False;kind='unavailable'
        ready=installed and supported
        availability=('NOT_REGISTERED' if not registered else 'EXECUTABLE_MISSING' if not installed
                      else 'UNSUPPORTED_CATEGORY' if not supported else 'AVAILABLE')
        issues=[]
        if entry.get('qualification_catalog'):
            from tools.cosimulation_backend import prerequisite_issues
            try:catalog=json.loads(Path(entry['qualification_catalog']).read_text())
            except (OSError,ValueError):catalog={}
            issues=prerequisite_issues(spec,test,catalog)
        if entry.get('capture_catalog'):
            from tools.capture_backend import prerequisite_issues
            try:catalog=json.loads(Path(entry['capture_catalog']).read_text())
            except (OSError,ValueError):catalog={}
            issues.extend(prerequisite_issues(test,catalog))
        rows.append(dict(test_id=test.get('id'),category=test.get('category'),engine=engine,
            backend_available=ready,backend_registered=registered,executable_available=installed,
            category_supported=supported,availability=availability,evidence_kind=kind,
            input_readiness='MISSING_INPUTS' if issues else 'NOT_QUALIFIED',input_issues=issues,
            reason={'AVAILABLE':'Execution, model qualification and assertions still required',
                    'NOT_REGISTERED':'Required backend is not registered',
                    'EXECUTABLE_MISSING':'Registered backend executable is unavailable',
                    'UNSUPPORTED_CATEGORY':'Installed backend does not implement the requested category'}[availability]))
    return dict(scope='Backend discovery only; does not certify models, test coverage or solver results',
        available_engines=sorted(config),tests=rows,
        missing_engines=sorted({str(t['engine']) for t in rows if not t['executable_available']}),
        unsupported_categories=[dict(test_id=t['test_id'],engine=t['engine'],category=t['category'])
                                for t in rows if t['availability']=='UNSUPPORTED_CATEGORY'])

# Categories describe evidence classes, rather than a particular task family.
# ``protocol`` covers a transaction/capture witness (USB, CAN, UART, SPI,
# SWD, ...); ``physical`` covers an operator-reviewed physical witness.  They
# deliberately remain separate from a numerical simulation and from a CAD
# geometry precheck.
# Public evidence classes.  ``dynamic_simulation`` and ``rf`` are explicit
# aliases rather than being hidden inside a task-specific solver name.  The
# older ``function``/``stability`` and ``si``/``emc`` classes remain valid so
# frozen projects continue to load, while new contracts can require the more
# precise board-level evidence they actually need.
CATEGORIES = ('function', 'stability', 'dynamic_simulation', 'emc_precheck',
              'emc', 'rf', 'thermal', 'si', 'pi', 'mechanical', 'protocol',
              'physical')


def analysis_timeout(test, default):
    """Positive, bounded per-test solver budget; never an infinite wait."""
    timeout=test.get('timeout_s',default)
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=86400:
        raise ValueError('timeout_s must be finite and in (0, 86400] seconds')
    return timeout


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value):
        raise ValueError('Identifier must contain letters, digits and underscores: '+str(value))
    return value


def _model_name(value):
    """SPICE library names may contain hyphens/dots, unlike report IDs."""
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9.\-]*',value):
        raise ValueError('Model name must be one SPICE identifier without whitespace or directives')
    return value


def _line(value):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in '\n\r;'):
        raise ValueError('A nonempty single SPICE expression is required')
    return value


def _bounds(assertions, values):
    return v.check_measurements(assertions,values)


INPUT_STATE_DIRS = frozenset({'session','.config','independent-validation','reports','artifacts','__pycache__'})


def declared_input_paths(plan, base_dir=None):
    """Validate declared source dependencies before mutation and during capture.

    Missing source files remain readiness gaps; private state and escaped paths
    are invalid inputs. Resolve existing symlinks when a workspace is available.
    """
    base=Path(base_dir).resolve() if base_dir is not None else None
    paths={}
    for test in plan.get('tests', []):
        if not isinstance(test,dict):raise ValueError('Each analysis test must be an object')
        params=test.get('parameters',{})
        if not isinstance(params,dict):raise ValueError('Analysis parameters must be an object')
        electrical=params.get('electrical',{})
        if not isinstance(electrical,dict):raise ValueError('Electrical test must be an object')
        for entry in (test,electrical):
            inputs=entry.get('inputs',{})
            if not isinstance(inputs,dict):raise ValueError('Analysis inputs must map relative paths to SHA-256 hashes')
            for name,digest in inputs.items():
                if not isinstance(name,str) or not name or '\x00' in name:
                    raise ValueError('Analysis input requires a nonempty relative file path')
                relative=Path(name)
                if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                    raise ValueError('Analysis input escapes workspace or is not a relative file path')
                if any(part in INPUT_STATE_DIRS for part in relative.parts):
                    raise ValueError('Analysis input refers to controller/report state')
                if not is_sha256(digest):raise ValueError('Analysis input requires a SHA-256 hash')
                path=base/relative if base is not None else relative
                if base is not None:
                    path=path.resolve()
                    if not path.is_relative_to(base):raise ValueError('Analysis input escapes workspace')
                    if any(part in INPUT_STATE_DIRS for part in path.relative_to(base).parts):
                        raise ValueError('Analysis input refers to controller/report state')
                    if path.exists() and not path.is_file():raise ValueError('Analysis input must be a file')
                paths[name]=path
    return paths


def validate_plan(plan, base_dir=None):
    """Validate plan shape; absent required analyses are reported by run_analyses."""
    if not isinstance(plan, dict) or set(plan)-{'required', 'not_applicable', 'devices', 'device_rules', 'tests'}:
        raise ValueError('analysis accepts required, not_applicable, devices, device_rules, tests')
    required=plan.get('required', [])
    if not isinstance(required, list) or any(x not in CATEGORIES for x in required) or len(set(required))!=len(required):
        raise ValueError('Invalid or duplicate required analysis category')
    exemptions=plan.get('not_applicable', {})
    if not isinstance(exemptions, dict) or any(k not in CATEGORIES or not isinstance(reason,str) or not reason.strip() for k,reason in exemptions.items()):
        raise ValueError('Not-applicable categories require explicit justification')
    if set(required)&set(exemptions):raise ValueError('Required analyses cannot also be not applicable')
    if not isinstance(plan.get('devices', {}), dict) or not isinstance(plan.get('tests', []), list):raise ValueError('devices must be an object; tests a list')
    declared_input_paths(plan,base_dir)
    if not isinstance(plan.get('device_rules',[]),list):raise ValueError('device_rules must be a list')
    for rule in plan.get('device_rules',[]):
        if set(rule)!={'symbols','binding'} or not isinstance(rule['symbols'],list) or not rule['symbols'] or any(not isinstance(x,str) or ':' not in x for x in rule['symbols']):
            raise ValueError('Each device rule requires exact CAD symbols and a binding')
        if not isinstance(rule['binding'],dict):raise ValueError('Device rule binding must be an object')
    ids=set()
    for test in plan.get('tests', []):
        ident=_token(test['id'])
        if ident in ids:raise ValueError('Duplicate analysis ID')
        ids.add(ident)
        if test.get('category') not in CATEGORIES:raise ValueError('Unknown analysis category')
        if test['category'] in exemptions:raise ValueError('An executed test category cannot also be not applicable')
        if not isinstance(test.get('assumptions'), str) or not test['assumptions'].strip():raise ValueError('State model scope and operating assumptions')
        backend=test.get('backend')
        if 'pcb_parasitics' in test:
            if backend != 'spice':raise ValueError('pcb_parasitics applies to SPICE tests')
            from tools import copper_resistance
            copper_resistance.validate(test['pcb_parasitics'])
        if 'tolerances' in test:
            tolerances=test['tolerances']
            if backend!='spice' or not isinstance(tolerances,dict) or not tolerances or any(
                    k not in ('R','C','L') or type(x) not in (int,float) or not math.isfinite(x) or not 0<x<1
                    for k,x in tolerances.items()):
                raise ValueError('tolerances requires SPICE and fractional ideal R/C/L limits between 0 and 1')
        if backend not in ('spice', 'geometry', 'external'):raise ValueError('Unknown analysis backend')
        if backend=='spice' and test['category']=='dynamic_simulation' and not str(test.get('command','')).lower().startswith('.tran '):raise ValueError('Dynamic simulation requires a transient test')
        if backend=='spice' and test['category'] not in ('function','stability','dynamic_simulation'):raise ValueError('SPICE circuit tests support function/stability/dynamic_simulation, not board EMC certification')
        if 'circuit_requirements' in test:
            from tools import requirements
            rules=test['circuit_requirements']
            if (backend!='spice' or not isinstance(rules,dict) or not rules
                    or set(rules)-{'components','component_groups','connections','connected_pins','separate_nets'}):
                raise ValueError('SPICE circuit_requirements accepts explicit component and connectivity predicates')
            checked=requirements.validate(rules)
            if checked['status']!='PASS':raise ValueError('; '.join(checked['issues']))
        terminals=test.get('terminals',{})
        if not isinstance(terminals,dict) or any(not isinstance(alias,str) or not alias.startswith('@') or
                not isinstance(pin,str) or ':' not in pin or not all(pin.rsplit(':',1)) for alias,pin in terminals.items()):
            raise ValueError('SPICE terminals map @aliases to actual ref:pin endpoints')
        if terminals and backend!='spice':raise ValueError('Terminal aliases apply to SPICE tests')
        if backend=='geometry' and test['category']!='emc_precheck':raise ValueError('Geometry backend is EMC precheck only')
        if test.get('bind_to_revision', False) is not False and not isinstance(test.get('bind_to_revision'), bool):
            raise ValueError('bind_to_revision must be a boolean')
        if test.get('require_evidence', False) is not False and not isinstance(test.get('require_evidence'), bool):
            raise ValueError('require_evidence must be a boolean')
        if test.get('evidence_kind') is not None and test['evidence_kind'] not in ('simulation','measurement','geometry','protocol','physical'):
            raise ValueError('Unsupported evidence_kind')
        if test['category'] in exemptions:raise ValueError('Cannot test an exempted category')
        analysis_timeout(test,1800 if backend=='spice' else 3600)


def input_hashes(spec, schematic=None, board=None, base_dir=None, include_backends=True):
    """Hash design intent, actual CAD and explicitly declared analysis dependencies."""
    base=Path(base_dir or (Path(schematic).parent if schematic else '.')).resolve()
    hashes={'spec': design.fingerprint(spec)}
    for path in (schematic, board, Path(board).with_suffix('.kicad_pro') if board else None):
        if path and Path(path).is_file():hashes[str(Path(path).resolve())]=v.file_hash(path)
    if schematic and Path(schematic).is_file():
        from tools import hierarchy
        hashes.update(hierarchy.hashes(schematic))
    plan=spec.get('analysis', {})
    if any(t.get('category')=='mechanical' for t in plan.get('tests',[])):
        from tools import mechanical
        candidate=Path(board) if board else base/'board.kicad_pcb'
        for path in mechanical.dependencies(candidate):
            hashes[str(path)]=v.file_hash(path) if path.is_file() else 'MISSING'
    for binding in [*plan.get('devices', {}).values(),*(r['binding'] for r in plan.get('device_rules',[]) if isinstance(r,dict) and isinstance(r.get('binding'),dict))]:
        for unit in models.instances(binding):
            if unit.get('file'):
                p=(base/unit['file']).resolve()
                if not p.is_relative_to(base):raise ValueError('Model input must be inside the candidate workspace')
                hashes[str(p)]=v.file_hash(p) if p.is_file() else 'MISSING'
    for p in declared_input_paths(plan,base).values():
        hashes[str(p)]=v.file_hash(p) if p.is_file() else 'MISSING'
    registry=Path(os.environ.get('PCB_ANALYSIS_BACKENDS','/opt/pcb-analysis-backends.json'))
    if include_backends and any(t.get('backend')=='external' for t in plan.get('tests', [])):
        hashes[str(registry.resolve())]=v.file_hash(registry) if registry.is_file() else 'MISSING'
        config=json.loads(registry.read_text()) if registry.is_file() else {}
        for test in plan.get('tests',[]):
            backend=config.get(test.get('engine'),{})
            for name in backend.get('argv',[])+backend.get('inputs',[]):
                p=Path(name)
                if p.is_absolute():hashes[str(p)]=v.file_hash(p) if p.is_file() else 'MISSING'
            if backend.get('capture_catalog'):
                from tools.capture_backend import catalog_inputs
                for p in catalog_inputs(backend['capture_catalog']):
                    hashes[str(p)]=v.file_hash(p) if p.is_file() else 'MISSING'
            if backend.get('qualification_catalog'):
                from tools.cosimulation_backend import catalog_inputs
                for p in catalog_inputs(backend['qualification_catalog']):
                    hashes[str(p)]=v.file_hash(p) if p.is_file() else 'MISSING'
    return hashes


def _spice(spec, sch, board, test, out, base):
    tolerances=test.get('tolerances')
    if not tolerances:return _spice_single(spec,sch,board,test,out,base)
    import itertools
    selected={}
    for part in spec['parts']:
        if part.get('schematic_only'):continue
        binding=spec['analysis'].get('devices',{}).get(part['ref'])
        matches=[r['binding'] for r in spec['analysis'].get('device_rules',[]) if part['symbol'] in r['symbols']]
        if binding is None and len(matches)==1:binding=matches[0]
        if binding and binding.get('kind') in tolerances:selected[part['ref']]=tolerances[binding['kind']]
    if not selected or len(selected)>8:
        return v.result('UNKNOWN','passive tolerance corners',['Require 1..8 explicitly modeled ideal passives; larger exhaustive sweeps need a separate qualified backend'])
    trials=[('nominal',{})]
    for i,signs in enumerate(itertools.product((-1,1),repeat=len(selected))):
        trials.append((f'corner_{i:03d}',{ref:1+sign*t for (ref,t),sign in zip(selected.items(),signs)}))
    results={};deadline=time.monotonic()+analysis_timeout(test,1800)
    for name,scales in trials:
        remaining=deadline-time.monotonic()
        if remaining<1:
            results['coverage']=v.result('UNKNOWN','passive tolerance coverage',['Total test timeout reached before all corners completed'])
            break
        folder=out/name;folder.mkdir()
        item=_spice_single(spec,sch,board,dict(test,timeout_s=int(remaining)),folder,base,value_scales=scales)
        item['value_scales']=scales;results[name]=item
        (folder/'result.json').write_text(json.dumps(item,indent=2))
    result=v.aggregate(results)
    result.update(scope='Nominal and all independent ideal passive tolerance extremes; no temperature coefficients or statistical yield inferred',
                  tolerances=tolerances,corners=len(trials)-1,evidence_kind='simulation',
                  simulation_source='schematic_netlist_with_pcb_dc_resistance' if test.get('pcb_parasitics') else 'schematic_netlist',
                  pcb_copper_extracted=bool(test.get('pcb_parasitics')) and all(r.get('pcb_copper_extracted') for r in results.values()),
                  conditions_sha256=_conditions_hash(test),spec_sha256=design.fingerprint(spec),
                  schematic_sha256=v.file_hash(sch),board_sha256=v.file_hash(board) if board and Path(board).is_file() else None,
                  evidence_sha256={str(out/name/'result.json'):v.file_hash(out/name/'result.json') for name in results if (out/name/'result.json').is_file()})
    return result


def _spice_single(spec, sch, board, test, out, base, value_scales=None):
    exported=v.export_netlist(sch,out/'schematic.xml')
    if exported['status']!='PASS':return exported
    actual=exported['data'];parity=v.compare_spec_netlist(spec,actual)
    if parity['status']!='PASS':return parity
    prerequisite_hashes={}
    for name,digest in test.get('inputs',{}).items():
        path=(base/name).resolve()
        if not path.is_file() or v.file_hash(path)!=digest:
            return v.result('UNKNOWN','SPICE prerequisite evidence',['Missing or changed input: '+name],
                            evidence_kind='model_validity',simulation_executed=False)
        prerequisite_hashes[str(path)]=digest
    if test.get('circuit_requirements'):
        from tools import requirements
        # The model's application limits apply to the values actually sent to
        # the solver, including each tolerance corner, not just nominal CAD.
        effective=copy.deepcopy(actual)
        if value_scales:
            from tools.values import numeric,passive_kind
            for ref,scale in value_scales.items():
                component=effective['components'][ref]
                component['value']=format(numeric(component['value'],passive_kind(component['symbol']))*scale,'.17g')
        prerequisite=requirements.check(test['circuit_requirements'],spec,effective,{})
        if prerequisite['status']!='PASS':
            return dict(prerequisite,scope='Declared circuit/model operating prerequisites from actual KiCad netlist',
                        evidence_kind='model_validity',simulation_executed=False,
                        schematic_sha256=exported['source_sha256'],
                        board_sha256=v.file_hash(board) if board and Path(board).is_file() else None,
                        spec_sha256=design.fingerprint(spec),conditions_sha256=_conditions_hash(test),
                        assumptions=test['assumptions'],prerequisite_value_scales=value_scales or {},
                        evidence_sha256={str(out/'schematic.xml'):v.file_hash(out/'schematic.xml'),**prerequisite_hashes})
    nodes={name:'n'+str(i) for i,name in enumerate(sorted(set(actual['pins'].values())))}
    parasitics=None
    if test.get('pcb_parasitics'):
        if not board or not Path(board).is_file():
            return v.result('UNKNOWN','PCB copper extraction',['A routed PCB is required for the declared copper resistance simulation'])
        from tools import copper_resistance
        try:parasitics=copper_resistance.extract(board,actual,test['pcb_parasitics'])
        except ValueError as error:
            report_path=out/'pcb-resistance.json'
            details=error.evidence if isinstance(error,copper_resistance.ExtractionError) else dict(
                status='UNKNOWN',issues=[str(error)],parameters=test['pcb_parasitics'])
            report_path.write_text(json.dumps(details,indent=2,allow_nan=False))
            return v.result('UNKNOWN','PCB resistance extraction',[str(error)],
                            simulation_executed=False,pcb_copper_extracted=False,
                            evidence_sha256={str(report_path):v.file_hash(report_path)},
                            board_sha256=v.file_hash(board),schematic_sha256=exported['source_sha256'],
                            conditions_sha256=_conditions_hash(test),spec_sha256=design.fingerprint(spec))
    pin_nodes=parasitics['pin_nodes'] if parasitics else {}
    terminals={}
    for alias,pin in test.get('terminals',{}).items():
        if pin not in actual['pins']:return v.result('UNKNOWN','SPICE terminal binding',['Missing endpoint: '+pin])
        if alias in nodes:return v.result('UNKNOWN','SPICE terminal binding',['Alias conflicts with a native net name: '+alias])
        terminals[alias]=actual['pins'][pin]
    def node(name):
        if name in test.get('terminals',{}):
            pin=test['terminals'][name]
            if pin in pin_nodes:return pin_nodes[pin]
        net=terminals.get(name,name)
        if parasitics and net not in parasitics['ideal_nets']:
            raise ValueError('Extracted networks require a physical ref:pin terminal alias: '+str(name))
        return nodes[net]
    ground=terminals.get(test.get('ground'),test.get('ground'))
    if ground not in nodes:return v.result('UNKNOWN','SPICE grounding',['Explicit ground net must exist'])
    if parasitics and ground not in parasitics['ideal_nets']:
        anchor=test.get('terminals',{}).get(test.get('ground'))
        if anchor not in pin_nodes:raise ValueError('Extracted ground requires a physical terminal alias')
        old=pin_nodes[anchor];pin_nodes[anchor]='0'
        for resistor in parasitics['resistors']:
            for k in ('a','b'):
                if resistor[k]==old:resistor[k]='0'
    else:nodes[ground]='0'
    if parasitics:(out/'pcb-resistance.json').write_text(json.dumps(parasitics,indent=2,allow_nan=False))
    def expression(text):
        return re.sub(r'\{([^{}]+)\}',lambda m:node(m[1]),_line(text))
    bindings=dict(spec['analysis'].get('devices', {}))
    components={p['ref']:p for p in spec['parts'] if not p.get('schematic_only')}
    # Bind by actual exported CAD symbol, independent of reference names or
    # component count. No model-selected exclusions are inferred from prefixes.
    for ref,part in components.items():
        matches=[r['binding'] for r in spec['analysis'].get('device_rules',[]) if actual['components'].get(ref,{}).get('symbol') in r['symbols']]
        if ref in bindings and matches or len(matches)>1:
            return v.result('UNKNOWN','simulation binding ambiguity',[ref+': choose one explicit binding or one matching symbol rule'])
        if matches:bindings[ref]=matches[0]
    if set(bindings)!=set(components):
        return v.result('UNKNOWN','simulation device coverage', ['Every physical component needs an explicit model or justified exclusion'],
                        missing=sorted(set(components)-set(bindings)),extra=sorted(set(bindings)-set(components)))
    lines=['* Generated from current KiCad netlist; scope: '+test['category']]
    if 'temperature_c' in test:
        temp=test['temperature_c']
        if type(temp) not in (float,int) or not math.isfinite(temp) or temp<=-273.15:raise ValueError('Finite temperature above absolute zero required')
        lines.append('.temp '+str(temp))
    options=test.get('spice_options',{})
    if set(options)-{'rshunt','gmin','reltol','abstol','vntol','itl1'} or any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in options.values()):
        raise ValueError('Unsupported or invalid SPICE numerical options')
    if options:lines.append('.options '+' '.join(k+'='+str(x) for k,x in options.items()))
    model_hashes=dict(prerequisite_hashes);coverage={};included=set();compatibilities=set()
    if parasitics:
        for index,resistor in enumerate(parasitics['resistors']):
            lines.append('Rpcb'+str(index)+' '+resistor['a']+' '+resistor['b']+' '+format(resistor['ohm'],'.17g'))
        model_hashes[str(out/'pcb-resistance.json')]=v.file_hash(out/'pcb-resistance.json')
    for ref,part in components.items():models.coverage(part,bindings[ref])
    expanded=[(ref,part,unit) for ref,part in components.items() for unit in models.instances(bindings[ref])]
    for index,(ref,part,binding) in enumerate(expanded):
        supported=binding.get('supported_analyses')
        if supported is not None and test['command'].split()[0][1:].lower() not in supported:
            return v.result('UNKNOWN','qualified model analysis coverage',[ref+': requested SPICE analysis has not been qualified for this model'])
        kind=binding.get('kind');coverage.setdefault(ref,[]).append({'kind':kind,'pins':binding.get('pins',[]),
            'scope':'excluded' if kind=='exclude' else 'ideal_passive' if kind in ('R','C','L') else 'declared_device_model',
            **({'reason':binding.get('reason')} if kind=='exclude' else {})})
        if kind=='exclude':
            if not binding.get('reason'):raise ValueError(ref+': exclusion requires a reason')
            continue
        pins=binding.get('pins', [])
        if not isinstance(pins,list) or not pins or len(set(pins))!=len(pins):raise ValueError(ref+': explicit ordered symbol pins required')
        # Netlist has symbol pin IDs, not guessed coordinates or net names.
        connections=[]
        for pin in pins:
            if pin not in part.get('nets',{}):connections.append('nc_'+ref+'_'+pin);continue
            key=ref+':'+str(part.get('pad_map',{}).get(pin,pin));net=actual['pins'][key]
            if parasitics and net not in parasitics['ideal_nets'] and key not in pin_nodes:
                raise ValueError('Modeled device pin is absent from extracted PCB: '+key)
            connections.append(pin_nodes.get(key,nodes[net]))
        if kind in ('R','C','L'):
            if len(pins)!=2:raise ValueError('R/C/L require two pins')
            value=actual['components'][ref]['value']
            from tools.values import numeric
            try:parsed=numeric(value,kind)
            except ValueError as error:return v.result('UNKNOWN','passive value',[ref+': '+str(error)])
            if parsed<0 or kind in ('C','L') and parsed==0:return v.result('UNKNOWN','passive value',[ref+': nonnegative R and positive C/L values required'])
            parsed*= (value_scales or {}).get(ref,1)
            lines.append(f'{kind}{index} '+ ' '.join(connections)+' '+format(parsed,'.12g'))
        elif kind in ('X','D','Q','M'):
            path=(base/binding['file']).resolve()
            if not path.is_file() or not binding.get('source') or v.file_hash(path)!=binding.get('sha256'):
                return v.result('UNKNOWN','device model',[ref+': missing model, provenance or matching SHA-256'])
            text=path.read_text()
            # Flat SPICE model packages only. Includes must be resolved and hashed
            # during intake, never silently loaded outside the evidence manifest.
            if re.search(r'^\s*\.(?:control|include|inc|lib|shell|exec|quit|end)\b',text,re.I|re.M):
                raise ValueError('Model must be a flat .model/.subckt package without commands/includes')
            name=_model_name(binding['model']);model_hashes[str(path)]=v.file_hash(path)
            compatibilities.add(binding.get('compatibility','native'))
            if path not in included:lines.append(text);included.add(path)
            lines.append(f'{kind}{index} '+' '.join(connections)+' '+name)
        else:return v.result('UNKNOWN','device model',[ref+': unsupported or missing explicit model'])
    if not test.get('sources') or not test.get('measures') or not test.get('assertions'):
        return v.result('UNKNOWN','SPICE testbench',['Sources, measurements and assertions required'])
    for i,source in enumerate(test['sources']):
        if source['kind'] not in ('V','I'):raise ValueError('Explicit voltage/current source required')
        lines.append(f'{source["kind"]}stim{i} {node(source["p"])} {node(source["n"])} '+expression(source['value']))
    command=_line(test['command'])
    if not re.match(r'^\.(tran|ac|dc)\s',command,re.I):raise ValueError('Supported analyses: .tran, .ac, .dc')
    analysis_kind=command.split()[0][1:].lower()
    saved=test.get('save_vectors')
    if saved is None:save_line='.save all'
    else:
        if not isinstance(saved,list) or not saved or any(not isinstance(x,str) for x in saved):
            raise ValueError('save_vectors requires a nonempty list of voltage/current vectors')
        vectors=[expression(x) for x in saved]
        if any(not re.fullmatch(r'[vi]\([A-Za-z0-9_:.#\-]+(?:,[A-Za-z0-9_:.#\-]+)?\)',x,re.I) for x in vectors):
            raise ValueError('save_vectors accepts only single v(node), v(node,node) or i(branch) expressions')
        save_line='.save '+' '.join(dict.fromkeys(vectors))
    lines.extend([save_line,command])
    printed=test.get('print_vectors',{})
    if printed:
        if analysis_kind!='tran' or not isinstance(printed,dict) or len(printed)>32:
            raise ValueError('print_vectors requires transient analysis and at most 32 named vectors')
        for name,vector in printed.items():
            _token(name);vector=expression(vector)
            if not re.fullmatch(r'[vi]\([A-Za-z0-9_:.#\-]+(?:,[A-Za-z0-9_:.#\-]+)?\)',vector,re.I):
                raise ValueError('print_vectors accepts only voltage/current vectors')
    measures=test['measures']
    if set(measures)!=set(test['assertions']):raise ValueError('Every measurement requires an assertion')
    for name,measure in measures.items():
        lines.append(f'.meas {analysis_kind} {_token(name)} '+expression(measure))
    if printed:
        # Native ASCII raw output preserves time precision at adaptive breakpoints.
        # .print tables round timestamps and cannot safely drive protocol decoding.
        lines.extend(['.control','run','set filetype=ascii',
                      'write waveforms.raw '+' '.join(dict.fromkeys(expression(x) for x in printed.values())),
                      'quit','.endc'])
    lines.append('.end')
    deck=out/'circuit.cir';deck.write_text('\n'.join(lines)+'\n')
    if len(compatibilities)>1:raise ValueError('Mixed simulator compatibility modes require a qualified unified model package')
    result=v.simulate_subcircuit(deck,out,test['assertions'],timeout=analysis_timeout(test,1800),compatibility=next(iter(compatibilities),'native'))
    if printed and result['status'] in ('PASS','FAIL'):
        from tools import simulation_waveforms
        data=simulation_waveforms.parse_raw((out/'waveforms.raw').read_text(),
                                            {k:expression(x) for k,x in printed.items()})
        waveform=out/'waveforms.csv';simulation_waveforms.write_csv(waveform,data)
        model_hashes[str(waveform)]=v.file_hash(waveform)
        result['waveform_path']=str(waveform.resolve())
    result.update(coverage=coverage,model_hashes=model_hashes,node_map=nodes,terminal_nets=terminals,assumptions=test['assumptions'],
                  simulation_source='schematic_netlist_with_pcb_dc_resistance' if parasitics else 'schematic_netlist',
                  pcb_copper_extracted=bool(parasitics),
                  extracted_effects=['dc_resistance'] if parasitics else [],
                  pcb_terminal_nodes=pin_nodes,
                  connectivity_scope=parasitics['scope'] if parasitics else 'Actual schematic netlist only; board hash binds the revision but does not simulate PCB shorts, opens or parasitics. Require separate native DRC and schematic/PCB parity.',
                  qualification_scope='Declared models and test conditions only; commercial model qualification, power balance, unmodeled corners and hardware compliance are not established by this verdict',
                  schematic_sha256=exported['source_sha256'],exported_netlist_sha256=exported['netlist_sha256'],
                  conditions_sha256=_conditions_hash(test),spec_sha256=design.fingerprint(spec),
                  analysis_type=analysis_kind,evidence_sha256={str(deck):v.file_hash(deck),str(out/'schematic.xml'):v.file_hash(out/'schematic.xml'),**model_hashes})
    if board and Path(board).is_file():
        result.update(board_sha256=v.file_hash(board),bind_to_revision=True)
    if test.get('bind_to_revision'):
        # Schematic-stage SPICE is allowed to run before a PCB exists; the
        # final stage must bind the same test to a concrete board revision.
        if not board or not Path(board).is_file():
            result.update(bind_to_revision=True, revision_binding='deferred_until_board_stage')
        else:
            result['board_sha256']=v.file_hash(board)
            result['bind_to_revision']=True
    return result


def _geometry(board,test,out):
    if not board or not Path(board).is_file():return v.result('UNKNOWN','EMC geometry precheck',['PCB required'])
    geometry=test.get('geometry',{});distances=test.get('pad_distances',[])
    if not geometry and not distances:return v.result('UNKNOWN','EMC geometry precheck',['Explicit limits required'])
    measured=postroute.measure_board(board);checks={}
    if geometry:checks['geometry']=postroute.check_constraints(measured,geometry)
    if distances:
        from tools import pcb_editor as ed
        b=ed.load(board);pads={}
        for fp in b.GetFootprints():
            for pad in fp.Pads():
                pads.setdefault(fp.GetReference()+':'+pad.GetNumber(),[]).append(pad)
        for i,rule in enumerate(distances):
            a,b=pads.get(rule['a'],[]),pads.get(rule['b'],[])
            if len(a)!=1 or len(b)!=1:
                checks[f'distance:{i}']=v.result('UNKNOWN','pad distance',['Unique pad anchors required']);continue
            pa,pb=a[0].GetPosition(),b[0].GetPosition()
            distance=math.hypot(ed.to_mm(pa.x-pb.x),ed.to_mm(pa.y-pb.y))
            checks[f'distance:{i}']=_bounds({'distance_mm':{'max':rule['max_mm']}},{'distance_mm':distance})
    result=v.aggregate(checks);result['scope']='declared EMC layout prechecks; not emissions/immunity or return-current simulation'
    result['assumptions']=test['assumptions'];result['board_sha256']=v.file_hash(board);result['bind_to_revision']=True
    return result


def _external(spec,sch,board,test,out,base):
    """Execute a host-configured solver, never a model-supplied shell command."""
    registry=Path(os.environ.get('PCB_ANALYSIS_BACKENDS','/opt/pcb-analysis-backends.json'))
    config=json.loads(registry.read_text()) if registry.is_file() else {}
    backend=config.get(test.get('engine'))
    if not backend:return v.result('UNKNOWN','external '+test['category'],['Trusted analysis backend is not installed/configured'])
    for name,digest in test.get('inputs',{}).items():
        path=(base/name).resolve()
        if not path.is_file() or v.file_hash(path)!=digest:return v.result('UNKNOWN','external inputs',['Missing or changed solver input: '+name])
    argv=backend.get('argv');category=test['category']
    if not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv) or category not in backend.get('categories',[]):
        return v.result('UNKNOWN','external analysis',['Backend does not support requested category'])
    board_sha256=v.file_hash(board) if board and Path(board).is_file() else None
    # A board analysis must carry the actual revision even when an older
    # test plan omitted the optional binding flag. Never drop verified
    # solver identities on the way to the independent policy gate.
    bind_revision=bool(board_sha256) or bool(test.get('bind_to_revision'))
    require_evidence=bind_revision or bool(test.get('require_evidence'))
    schematic_sha256=v.file_hash(sch) if sch and Path(sch).is_file() else None
    spec_sha256=design.fingerprint(spec)
    request={'schema_version':1,'category':category,'sources':input_hashes(spec,sch,board,base),
             'design_sources':input_hashes(spec,sch,board,base,include_backends=False),
             'spec':spec,'schematic':str(Path(sch).resolve()) if sch else None,
             'board':str(Path(board).resolve()) if board else None,'parameters':test.get('parameters',{}),
             'assumptions':test['assumptions'],'assertions':test.get('assertions',{}),
             'bind_to_revision':bind_revision,
             'board_sha256':board_sha256,'schematic_sha256':schematic_sha256,
             'spec_sha256':spec_sha256,'conditions_sha256':_conditions_hash(test)}
    request_hash=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    request['request_sha256']=request_hash
    (out/'request.json').write_text(json.dumps(request,indent=2))
    report=out/'response.json'
    env={k:val for k,val in os.environ.items() if not any(x in k.upper() for x in ('KEY','TOKEN','SECRET','PASSWORD'))}
    command=[*argv,'--request',str((out/'request.json').resolve()),'--output',str(report.resolve())]
    timeout=analysis_timeout(test,3600)
    try:
        process=subprocess.run(command,cwd=base,env=env,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired as error:
        def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
        (out/'engine.log').write_text(decoded(error.stdout)+'\n'+decoded(error.stderr))
        return v.result('UNKNOWN','external analysis',['Solver exceeded its time budget; inspect retained engine.log before retrying'],
                        timed_out=True,timeout_s=timeout,request_sha256=request_hash,log_path=str(out/'engine.log'))
    (out/'engine.log').write_text(process.stdout+'\n'+process.stderr)
    if process.returncode or not report.is_file():return v.result('UNKNOWN','external analysis',['Solver failed or produced no report'])
    evidence=json.loads(report.read_text())
    if (not isinstance(evidence,dict) or evidence.get('request_sha256')!=request_hash
            or not isinstance(evidence.get('method'),str) or not evidence['method'].strip()
            or evidence.get('status') not in ('PASS','FAIL','UNKNOWN')):
        return v.result('UNKNOWN','external analysis',['Solver report requires matching request hash, method and explicit PASS/FAIL/UNKNOWN status'])
    evidence_kind=evidence.get('evidence_kind','unknown') if backend.get('capture_catalog') else backend.get('evidence_kind','unknown')
    if evidence_kind not in ('simulation','measurement','geometry','protocol','physical'):evidence_kind='unknown'
    if category=='protocol' and evidence_kind not in ('protocol','measurement','simulation'):
        return v.result('UNKNOWN','protocol evidence kind',['Protocol analysis requires a protocol capture or qualified simulation'],request_sha256=request_hash,evidence_kind=evidence_kind,evidence=evidence)
    if category=='physical' and evidence_kind not in ('physical','measurement'):
        return v.result('UNKNOWN','physical evidence kind',['Physical analysis requires an operator-reviewed measurement/capture'],request_sha256=request_hash,evidence_kind=evidence_kind,evidence=evidence)
    if bind_revision:
        # A solver may only sign off the exact candidate revision it received.
        # This check is intentionally independent of the request hash so a
        # backend cannot claim a different board while replaying the request.
        if not board_sha256:
            return v.result('UNKNOWN','external revision binding',['bind_to_revision requires an existing board artifact'],request_sha256=request_hash)
        problems=binding_issues(evidence,request,require_evidence=require_evidence)
        if problems:
            return v.result('UNKNOWN','external revision/evidence binding',problems,request_sha256=request_hash,evidence=evidence,evidence_kind=evidence_kind)
    if require_evidence:
        hashes=evidence.get('evidence_sha256')
        if not isinstance(hashes,dict) or not hashes or any(not isinstance(k,str) or not k or not is_sha256(h) for k,h in hashes.items()):
            return v.result('UNKNOWN','external evidence provenance',['Retained evidence paths and SHA-256 digests required'],request_sha256=request_hash)
        for name,digest in hashes.items():
            path=Path(name) if Path(name).is_absolute() else out/name
            if not path.is_file() or v.file_hash(path)!=digest:
                return v.result('UNKNOWN','external evidence provenance',['Missing or changed retained evidence: '+name],request_sha256=request_hash)
    identity={k:request[k] for k in ('board_sha256','schematic_sha256','spec_sha256','conditions_sha256')} if bind_revision else {}
    if evidence['status']!='PASS':
        status='FAIL' if evidence.get('status')=='FAIL' else 'UNKNOWN'
        return v.result(status,'external solver coverage/convergence',evidence.get('issues') or ['Backend did not establish a complete converged result'],evidence=evidence,evidence_kind=evidence_kind,**identity)
    result=_bounds(test.get('assertions',{}),evidence.get('measurements',{}))
    result.update(engine=test['engine'],method=evidence['method'],request_sha256=request_hash,report_sha256=v.file_hash(report),
                  backend_registry_sha256=v.file_hash(registry),assumptions=test['assumptions'],solver_scope=evidence.get('scope'),convergence=evidence.get('convergence'),evidence_sha256=evidence.get('evidence_sha256'),evidence_kind=evidence_kind)
    if bind_revision:
        result.update(board_sha256=board_sha256, schematic_sha256=schematic_sha256, spec_sha256=spec_sha256,
                      conditions_sha256=request['conditions_sha256'], bind_to_revision=True)
    return result


def run_analyses(spec, schematic, board, out_dir, base_dir=None, stage='final', applicability=None):
    """Run required analyses and produce concrete repair blockers by category.

stage=schematic runs function/stability only; board analyses are explicitly
deferred until final verification. No category is inferred from a task name.
"""
    out=Path(out_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    base=Path(base_dir or (Path(schematic).resolve().parent if schematic else '.')).resolve()
    if stage not in ('schematic','final'):raise ValueError('Unknown analysis stage')
    missing_plan=spec.get('analysis') is None
    plan={} if missing_plan else spec['analysis']
    try:validate_plan(plan)
    except (ValueError,KeyError,TypeError) as e:return v.result('UNKNOWN','analysis plan',[str(e)])
    before=input_hashes(spec,schematic,board,base);tests={};groups={}
    for test in plan.get('tests',[]):
        category=test['category']
        if stage=='schematic' and category not in ('function','stability','dynamic_simulation'):continue
        dest=out/test['id'];dest.mkdir(exist_ok=False)
        started=time.monotonic()
        try:
            if test['backend']=='spice':r=_spice(spec,schematic,board,test,dest,base)
            elif test['backend']=='geometry':r=_geometry(board,test,dest)
            else:r=_external(spec,schematic,board,test,dest,base)
        except Exception as e:r=v.result('UNKNOWN',category,[f'{type(e).__name__}: {e}'])
        if test['backend']=='geometry':
            # Retain the actual backend result before adding shared provenance.
            raw=dest/'backend-result.json';raw.write_text(json.dumps(r,indent=2))
            r.update(schematic_sha256=v.file_hash(schematic) if schematic and Path(schematic).is_file() else None,
                     spec_sha256=design.fingerprint(spec),conditions_sha256=_conditions_hash(test),
                     evidence_sha256={str(raw):v.file_hash(raw)})
        r.update(category=category,test_id=test['id'],backend=test['backend'],elapsed_s=time.monotonic()-started,
                 conditions=copy.deepcopy(test))
        if test['backend'] in ('spice','geometry'):r.setdefault('evidence_kind','simulation' if test['backend']=='spice' else 'geometry')
        tests[test['id']]=r
        groups.setdefault(category,{})[test['id']]=r
        (dest/'result.json').write_text(json.dumps(r,indent=2))
    categories={k:v.aggregate(checks) for k,checks in groups.items()}
    required=sorted(set(plan.get('required',[]))|set((applicability or {}).get('required',[])))
    if stage=='schematic':required=[k for k in required if k in ('function','stability','dynamic_simulation')]
    for category in required:
        if category not in categories:categories[category]=v.result('UNKNOWN',category,['Required category has no executable tests'])
    exemptions=dict(plan.get('not_applicable',{}))
    exemptions.update((applicability or {}).get('not_applicable',{}))
    for category,reason in exemptions.items():
        if stage!='schematic' or category in ('function','stability','dynamic_simulation'):
            approved=(applicability or {}).get('not_applicable',{}).get(category)
            # Never erase executed evidence with an exemption, including when
            # a direct diagnostic caller supplies conflicting applicability.
            if category in groups:
                if approved:
                    categories[category]=v.aggregate({'executed':categories[category],
                        'applicability':v.result('UNKNOWN',category,['Executed tests conflict with the external exemption'])})
                continue
            categories[category]=v.result('NOT_APPLICABLE' if approved and category not in required else 'UNKNOWN',category,
                [approved] if approved and category not in required else ['Candidate non-applicability is unapproved; missing models, backends or measurements are evidence gaps',reason],
                applicability_basis='immutable_public_requirements' if approved and category not in required else 'candidate_diagnostic')
    if missing_plan:
        expected=set(CATEGORIES if stage=='final' else ('function','stability','dynamic_simulation'))
        if expected-set(categories):
            categories['coverage']=v.result('UNKNOWN','engineering analysis plan',
                ['No analysis plan; unresolved applicability: '+', '.join(sorted(expected-set(categories)))])
    report=v.aggregate(categories) if categories else v.result('NOT_APPLICABLE' if stage=='schematic' else 'UNKNOWN','analysis coverage',
                                                               ['Board analyses deferred until final verification'] if stage=='schematic' else ['No analysis requirements declared'])
    if categories and all(item['status']=='NOT_APPLICABLE' for item in categories.values()):
        report=v.result('NOT_APPLICABLE','analysis coverage',
            ['No analyses required at this stage by the immutable public requirements; no simulation PASS is claimed'],checks=categories)
    after=input_hashes(spec,schematic,board,base)
    if before!=after:report=v.aggregate({'analyses':report,'revision':v.result('UNKNOWN','analysis revision',['Inputs changed during analysis'])})
    report.update(categories=categories,sources=after,stage=stage,tests_executed=len(tests),
                  applicability_basis='immutable_public_requirements' if applicability is not None else 'candidate_diagnostic',
                  blockers=[{'test':key,'category':r['category'],'status':r['status'],'issues':r['issues']} for key,r in tests.items() if r['status'] not in ('PASS','NOT_APPLICABLE')],
                  scope='only explicitly modeled behavior and declared layout/solver checks')
    for category in required:
        if category not in groups:report['blockers'].append({'category':category,'status':'UNKNOWN','issues':['Add required test, model, stimulus and acceptance bounds']})
    (out/'analysis.json').write_text(json.dumps(report,indent=2))
    return report


def electrical_checks(spec, schematic=None, board=None, out_dir=None, stage='final', applicability=None):
    """Single shared electrical entry point for schematic, build loop and final check."""
    out=Path(out_dir or 'electrical');out.mkdir(parents=True,exist_ok=True)
    checks={'datasheets':v.aggregate({p['ref']:v.check_component_datasheet(spec,p['ref']) for p in spec['parts']}),
            'power_tree':v.check_power_tree(spec),'interfaces':v.check_interface(spec),
            'operating_limits':v.check_operating_limits(spec),
            'analyses':run_analyses(spec,schematic,board,out/'analyses',stage=stage,applicability=applicability)}
    # Legacy standalone decks remain diagnostic, never counted as a required
    # design-bound function/stability category.
    for i,test in enumerate(spec.get('simulations',[])):
        checks[f'simulation:{i}']=v.simulate_subcircuit(test['netlist'],out/f'simulation-{i}',test['assertions'])
    return checks
