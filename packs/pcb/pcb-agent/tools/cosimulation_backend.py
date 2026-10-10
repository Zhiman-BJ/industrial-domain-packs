"""Native-netlist electrical -> protocol/steady thermal co-analysis.

No task branches, replacement ICs, arbitrary commands, or measured-data claims.
Digital stimulus is expressed as SPICE sources; DUT outputs come from ngspice.
Thermal coupling is one-way and steady-state, not a temperature-feedback model.
Host-reviewed model domains and thermal fixtures live outside candidate storage.
"""
import argparse
import copy
import json
import math
from pathlib import Path
from tools import analysis, capture_backend as capture, design, models
from tools import protocol_capture, simulation_waveforms, validation as v


def prerequisite_issues(spec,test,catalog):
    """Public missing-input diagnostics, without exposing private catalog data."""
    p=test.get('parameters') or {};issues=[]
    if not isinstance(p,dict):return ['Co-simulation parameters must be an object']
    if test.get('category') not in ('protocol','thermal','function','dynamic_simulation'):
        issues.append('Use an applicable solver or approved_capture; co-simulation cannot provide physical/RF/EMC evidence')
    for key in ('electrical','purpose'):
        if not p.get(key):issues.append('Missing executable '+key)
    for key in ('model_records','operating_conditions'):
        if not isinstance(p.get(key),dict):issues.append('Missing object '+key)
    selected=p.get('model_records') if isinstance(p.get('model_records'),dict) else {}
    for ref,binding in spec.get('analysis',{}).get('devices',{}).items():
        if binding.get('kind') in ('R','C','L','exclude'):continue
        key=selected.get(ref)
        if key not in catalog.get('models',{}):issues.append('Missing reviewed model for '+ref)
        elif p.get('electrical'):
            record=catalog['models'][key]
            if analysis._conditions_hash(p['electrical']) not in record.get('qualified_testbenches',{}):
                issues.append('Electrical fixture lacks scoped qualification for '+ref)
            if p.get('thermal_network') and analysis._conditions_hash(p['electrical']) not in record.get('qualified_power_testbenches',{}):
                issues.append('Device dissipation lacks fixture-specific model qualification for '+ref)
    parts={part['ref'] for part in spec.get('parts',[]) if not part.get('schematic_only')}
    for ref in sorted(parts-set(spec.get('analysis',{}).get('devices',{}))):issues.append('Missing explicit device binding: '+ref)
    if test.get('category')=='protocol' and not p.get('decoders'):issues.append('Missing DUT output waveform decoder')
    if test.get('category')=='thermal' and p.get('thermal_network') not in catalog.get('thermal_networks',{}):
        issues.append('Missing reviewed board-bound thermal network')
    if not test.get('assertions'):issues.append('Missing independent measurement bounds')
    return issues


def catalog_inputs(path):
    path=Path(path).resolve();paths={path}
    if not path.is_file():return sorted(paths)
    try:
        catalog=json.loads(path.read_text())
        for group in ('models','thermal_networks'):
            for record in catalog[group].values():
                for item in record['evidence']:
                    p=(path.parent/item['path']).resolve()
                    if not p.is_relative_to(path.parent):raise ValueError('Escaping qualification evidence')
                    paths.add(p)
    except (KeyError,ValueError,TypeError):pass
    return sorted(paths)


def reviewed(record,root,dependencies):
    for key in ('reviewer','reviewed_at','scope'):
        if not isinstance(record.get(key),str) or not record[key].strip():raise ValueError('Missing model review '+key)
    if not record.get('evidence'):raise ValueError('Retained qualification evidence required')
    for item in record['evidence']:
        p=capture.retained(root,item);dependencies[str(p)]=v.file_hash(p)


def qualify_bindings(spec,parameters,catalog,root,base,dependencies):
    """Exact devices, pins, model bytes, omissions and operating-domain review."""
    bindings=spec['analysis'].get('devices',{})
    if spec['analysis'].get('device_rules'):raise ValueError('Co-analysis requires explicit device bindings')
    parts={p['ref']:p for p in spec['parts'] if not p.get('schematic_only')}
    if set(bindings)!=set(parts):raise ValueError('Every physical component requires a binding')
    selected=parameters.get('model_records',{});excluded=parameters.get('excluded_devices',{})
    required=set();omitted=set()
    for ref,part in parts.items():
        binding=bindings[ref];models.coverage(part,binding)
        if binding.get('kind') in ('R','C','L'):
            if part['symbol']!='Device:'+binding['kind']:raise ValueError('Ideal passive binding requires the matching passive symbol')
            continue
        if binding.get('kind')=='exclude':
            omitted.add(ref)
            if excluded.get(ref)!=binding.get('reason') or not excluded.get(ref):
                raise ValueError('Every omitted device requires an explicit matching scope exclusion: '+ref)
            # Circuit-bearing ICs cannot disappear behind connector exclusions.
            if not part['symbol'].startswith(('Connector:', 'Connector_Generic:', 'Mechanical:', 'Connector_Generic_MountingPin:')):
                raise ValueError('Active/passive DUT exclusions are not supported: '+ref)
            continue
        required.add(ref)
        if ref not in selected:raise ValueError('Missing host-qualified model record for '+ref)
        record=catalog['models'][selected[ref]];reviewed(record,root,dependencies)
        if (record['mpn']!=part.get('mpn',part.get('value')) or record['symbol']!=part['symbol']
                or record['footprint']!=part['footprint']):raise ValueError('Qualified model CAD/MPN identity mismatch: '+ref)
        expected=record['binding'];actual=copy.deepcopy(binding)
        # Workspace relocation is allowed; bytes, terminals, and subcircuits are fixed.
        actual.pop('file',None);expected={k:x for k,x in expected.items() if k!='file'}
        if actual!=expected:raise ValueError('Qualified model terminal/byte binding mismatch: '+ref)
        for unit in models.instances(binding):
            p=(base/unit['file']).resolve()
            if not p.is_relative_to(base) or v.file_hash(p)!=unit['sha256']:raise ValueError('Model file changed: '+ref)
            dependencies[str(p)]=v.file_hash(p)
        conditions=parameters.get('operating_conditions',{})
        domains=record['operating_domain']
        if not domains or set(domains)!=set(conditions):raise ValueError('All qualified operating dimensions must be supplied')
        if v.check_measurements(domains,conditions)['status']!='PASS':raise ValueError('Outside qualified model operating domain: '+ref)
        if parameters['electrical']['temperature_c']!=conditions.get('temperature_c'):
            raise ValueError('Electrical temperature differs from qualified operating conditions')
        if parameters.get('purpose') not in record['purposes']:raise ValueError('Model not qualified for requested behavior: '+ref)
        # A caller's operating_conditions are claims, not applied stimuli.
        # Only reviewed electrical fixtures can establish the model's domain;
        # this also prevents driving a desired DUT response with an ideal source.
        fixtures=record.get('qualified_testbenches',{})
        fixture=fixtures.get(analysis._conditions_hash(parameters['electrical']))
        if not isinstance(fixture,dict) or fixture.get('operating_conditions')!=conditions or fixture.get('purpose')!=parameters['purpose']:
            raise ValueError('Electrical fixture/operating conditions lack matching model qualification: '+ref)
        if parameters.get('thermal_network'):
            # Voltage/load-transient qualification does not establish supply
            # current or dissipation of a behavioral model. Loss qualification
            # must name reviewed power observables for this exact fixture.
            power=record.get('qualified_power_testbenches',{}).get(analysis._conditions_hash(parameters['electrical']))
            quantities=power.get('power_measurements') if isinstance(power,dict) else None
            if (not isinstance(quantities,list) or not quantities
                    or any(not isinstance(k,str) or k not in parameters['electrical'].get('measures',{}) for k in quantities)
                    or len(set(quantities))!=len(quantities)):
                raise ValueError('Device dissipation lacks fixture-specific model qualification: '+ref)
            thermal=catalog.get('thermal_networks',{}).get(parameters['thermal_network'],{})
            if not set(quantities)<=set(thermal.get('power_measurements',{}).values()):
                raise ValueError('Qualified device dissipation is missing from thermal network: '+ref)
    if set(selected)!=required or set(excluded)!=omitted:raise ValueError('Unexpected model/exclusion declarations')


def thermal_network(record,values,ambient):
    """Solve a reviewed resistor network with explicit electrical power inputs."""
    import numpy as np
    ambient=capture.number(ambient)
    if ambient<=-273.15:raise ValueError('Thermal ambient below absolute zero')
    nodes=record['nodes'];edges=record['edges'];power=record['power_measurements']
    if not nodes or len(nodes)>256 or len(set(nodes))!=len(nodes) or 'ambient' in nodes or set(power)!=set(nodes):
        raise ValueError('Unique thermal nodes and explicit power input for every node required')
    index={n:i for i,n in enumerate(nodes)};matrix=np.zeros((len(nodes),len(nodes)))
    rhs=np.array([capture.number(values[power[n]]) for n in nodes])
    if np.any(rhs<0) or not np.any(rhs>0):raise ValueError('Nonnegative dissipations and a positive heat load required')
    links=[]
    for edge in edges:
        a,b=edge['a'],edge['b'];resistance=capture.number(edge['kelvin_per_watt'])
        if a==b or a not in index or (b not in index and b!='ambient') or resistance<=0:
            raise ValueError('Invalid thermal resistance edge')
        g=1/resistance;matrix[index[a],index[a]]+=g
        if b in index:
            matrix[index[b],index[b]]+=g;matrix[index[a],index[b]]-=g;matrix[index[b],index[a]]-=g
        else:links.append((index[a],g))
    try:rise=np.linalg.solve(matrix,rhs)
    except np.linalg.LinAlgError as e:raise ValueError('Thermal network has no complete ambient path') from e
    residual=float(np.max(np.abs(matrix@rise-rhs)))
    rejected=sum(float(rise[i])*g for i,g in links);total=float(rhs.sum())
    if not np.all(np.isfinite(rise)) or residual>1e-8*max(total,1) or abs(rejected-total)>1e-8*max(total,1):
        raise ValueError('Thermal solver power balance failed')
    return {**{n+'_c':ambient+float(rise[i]) for n,i in index.items()},
            'power_balance_w':abs(rejected-total),'dissipation_w':total}


def evaluate(request,catalog_path,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    if request['category'] not in ('protocol','thermal','function','dynamic_simulation'):
        raise ValueError('This solver supports electrical/protocol/steady thermal simulation; physical/RF/EMC needs a separate backend')
    p=request['parameters']
    if 'electrical' not in p:
        raise ValueError('Missing executable electrical testbench, model_records, operating_conditions and decoders/thermal_network; legacy missing[] declarations are not executable')
    if set(p)-{'electrical','model_records','operating_conditions','excluded_devices','purpose','decoders','logic_channels','max_interval_s','thermal_network','ambient_c'}:
        raise ValueError('Unsupported co-analysis parameter')
    if not isinstance(p.get('purpose'),str) or not p['purpose'].strip():raise ValueError('Explicit simulation purpose required')
    spec=request['spec'];base=Path(request['schematic']).resolve().parent
    for key in ('board','schematic'):
        if v.file_hash(request[key])!=request[key+'_sha256']:raise ValueError('Candidate revision changed')
    if design.fingerprint(spec)!=request['spec_sha256']:raise ValueError('Candidate specification changed')
    catalog_path=Path(catalog_path).resolve();catalog=json.loads(catalog_path.read_text())
    if catalog.get('schema_version')!=1:raise ValueError('Unsupported co-analysis catalog')
    dependencies={str(catalog_path):v.file_hash(catalog_path)}
    qualify_bindings(spec,p,catalog,catalog_path.parent,base,dependencies)
    electrical=copy.deepcopy(p['electrical'])
    analysis.validate_plan({'required':['dynamic_simulation'],'tests':[electrical]})
    if electrical['backend']!='spice' or electrical['category']!='dynamic_simulation' or electrical.get('tolerances'):
        raise ValueError('Co-analysis requires one transient corner; declare separate tests for each corner')
    folder=out/'electrical';folder.mkdir()
    result=analysis._spice(spec,request['schematic'],request['board'],electrical,folder,base)
    (folder/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    values={};checks={'electrical':result}
    # A native application-limit/parity failure has no waveform to decode.
    # Preserve that actionable FAIL instead of masking it with a missing-value
    # exception and returning UNKNOWN from the external adapter.
    if result['status']!='UNKNOWN' and result.get('values'):
        values.update({'electrical_'+k:x for k,x in result.get('values',{}).items() if k in electrical['assertions']})
        decoders=p.get('decoders',{})
        if request['category']=='protocol' and not decoders:raise ValueError('Protocol simulation requires output waveform decoding')
        if decoders:
            vectors=electrical['print_vectors']
            units={'time_s':'s',**{k:('V' if x.lower().startswith('v(') else 'A') for k,x in vectors.items()}}
            data=capture.table(Path(result['waveform_path']),units)
            channels=p.get('logic_channels',{})
            if any(units.get(k)!='V' for k in channels):raise ValueError('Logic conversion requires voltage vectors')
            digital=simulation_waveforms.logic(data,channels,p['max_interval_s']) if channels else None
            for name,decoder in decoders.items():
                analysis._token(name);kind=decoder['kind']
                if kind=='waveform':measured={'value':capture.waveform(data,decoder,units)}
                elif kind in ('uart','spi','sd_spi','swd','can_classic'):
                    if digital is None:raise ValueError('Protocol decoder requires explicit logic thresholds')
                    fn=getattr(capture,kind) if kind in ('uart','spi') else getattr(protocol_capture,kind)
                    # Use the explicit electrical-to-logic sampling contract
                    # unless the decoder asks for a stricter independent one.
                    m=dict(decoder)
                    m.setdefault('max_interval_s',p['max_interval_s'])
                    measured=fn(digital,m,{k:'s' if k=='time_s' else 'logic' for k in digital})
                else:raise ValueError('Unsupported executable decoder: '+str(kind))
                values.update({name+'_'+k:x for k,x in measured.items()})
        if request['category']=='thermal' and not p.get('thermal_network'):raise ValueError('Reviewed thermal network required')
        if p.get('thermal_network'):
            record=catalog['thermal_networks'][p['thermal_network']]
            reviewed(record,catalog_path.parent,dependencies)
            if record['board_sha256']!=request['board_sha256']:raise ValueError('Thermal fixture is for a different physical board')
            if record['electrical_sha256']!=analysis._conditions_hash(electrical):raise ValueError('Thermal loss expressions/conditions changed')
            if v.check_measurements({'ambient_c':record['ambient_c']},{'ambient_c':p['ambient_c']})['status']!='PASS':
                raise ValueError('Ambient outside qualified thermal domain')
            values.update(thermal_network(record,result['values'],p['ambient_c']))
        if set(values)!=set(request['assertions']):raise ValueError('Every coupled output needs explicit bounds; extra/missing metrics are not accepted')
        checks['coupled']=v.check_measurements(request['assertions'],values)
    status=v.aggregate(checks)
    status['issues']=[name+': '+str(issue) for name,check in checks.items() for issue in check.get('issues',[])]
    if any(v.file_hash(path)!=h for path,h in dependencies.items()):raise ValueError('Model qualification changed during simulation')
    if any(v.file_hash(request[k])!=request[k+'_sha256'] for k in ('board','schematic')):raise ValueError('CAD changed during simulation')
    dependencies.update({str(f.resolve()):v.file_hash(f) for f in out.rglob('*') if f.is_file()})
    status.update(method='KiCad netlist -> ngspice transient -> waveform decode / reviewed thermal conductance solve',
        measurements=values,evidence_sha256=dependencies,evidence_kind='simulation',
        simulation_executed=result.get('simulation_executed',bool(result.get('values'))),
        scope='Explicit device models, declared electrical corner, sampled protocol signals and one-way steady thermal network only; no hardware/EMC or thermal feedback qualification')
    return status


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--catalog',required=True)
    parser.add_argument('--request',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    request=json.loads(Path(args.request).read_text());out=Path(args.output)
    try:result=evaluate(request,args.catalog,out.parent/'coupled')
    except (OSError,ValueError,KeyError,TypeError,IndexError) as e:
        result=v.result('UNKNOWN','co-analysis prerequisites',[str(e)])
        result.update(method='native-netlist co-analysis',evidence_kind='simulation',measurements={})
    result.update({k:request.get(k) for k in ('request_sha256','board_sha256','schematic_sha256','spec_sha256','conditions_sha256')})
    result.setdefault('evidence_sha256',{})[str(Path(args.request).resolve())]=v.file_hash(args.request)
    out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
