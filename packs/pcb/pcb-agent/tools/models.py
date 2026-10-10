"""Sourced per-component simulation packages with explicit multi-unit bindings."""
from pathlib import Path
import copy,hashlib,json,shutil,tempfile
from tools import validation as v


def instances(binding):
    units=binding.get('instances')
    if units is None:return [binding]
    if not isinstance(units,list) or not units:raise ValueError('Model instances must be a nonempty list')
    common={k:value for k,value in binding.items() if k!='instances'}
    return [dict(common,**unit) for unit in units]


def dependencies(plan):
    bindings=[*plan.get('devices',{}).values(),*(r['binding'] for r in plan.get('device_rules',[]))]
    return sorted({unit['file'] for binding in bindings for unit in instances(binding) if unit.get('file')}
                  |{name for test in plan.get('tests',[]) for name in test.get('inputs',{})})


def coverage(part,binding):
    if binding.get('kind')=='exclude':
        if not binding.get('reason'):raise ValueError('Model exclusion needs a reason')
        return
    used=set()
    for unit in instances(binding):
        pins=unit.get('pins',[])
        if not isinstance(pins,list) or not pins or any(not isinstance(p,str) for p in pins) or len(set(pins))!=len(pins):
            raise ValueError('Each model instance requires distinct ordered symbol pins')
        used.update(pins)
    connected=set(part.get('nets',{}));known=connected|set(part.get('no_connect',[]))
    if not connected<=used or not used<=known:raise ValueError(part['ref']+': model pin coverage differs from connected/explicit NC pins')


def qualify(root,facts,definitions):
    """Execute each stored model testbench against a native single-DUT schematic."""
    from tools import analysis,schematic
    checks={}
    for name,model in definitions.items():
        try:
            if not model.get('mapping_source') or not model.get('scope'):raise ValueError('Model terminal mapping source and scope required')
            testbench=model['testbench'];binding=model['binding']
            for unit in instances(binding):
                path=(Path(root)/unit['file']).resolve()
                if Path(root).resolve() not in path.parents:raise ValueError('Qualified model must stay inside its component package')
                if not path.is_file() or v.file_hash(path)!=unit.get('sha256'):raise ValueError('Model hash mismatch')
            part=dict(ref='U1',symbol=facts['symbol'],footprint=facts['footprint'],value=facts['mpn'],
                      nets=testbench['nets'],no_connect=testbench.get('no_connect',[]),pad_map=facts.get('pad_map',{}))
            coverage(part,binding)
            # Every non-NC device pin is represented, including unused amplifiers.
            required={p for p,f in facts['pins'].items() if f['type']!='no_connect'}
            modeled={p for unit in instances(binding) for p in unit['pins']}
            if not required<=modeled:raise ValueError('Qualification must exercise all functional pins/units')
            with tempfile.TemporaryDirectory(prefix='model-qualification-') as tmp:
                tmp=Path(tmp);s=schematic.Schematic('model_test')
                old=schematic.SYMBOL_DIRS
                try:
                    schematic.SYMBOL_DIRS=[str(Path(root)/'symbols')]
                    s.add_part('U1',facts['symbol'],facts['footprint'],nets=part['nets'],value=facts['mpn'],no_connect=part['no_connect'],pad_map=part['pad_map'])
                    schematic.SYMBOL_DIRS=[str(Path(root)/'symbols'),*old]
                    # Regulators and other multi-terminal models require real
                    # external loads/feedback/storage, not just ideal sources.
                    # These parts are explicit qualification data, never inferred
                    # from the DUT name or injected into a candidate design.
                    auxiliaries=copy.deepcopy(testbench.get('parts',[]))
                    if not isinstance(auxiliaries,list):raise ValueError('Testbench parts must be a list')
                    refs={'U1'}
                    for auxiliary in auxiliaries:
                        ref=auxiliary['ref']
                        if ref in refs or auxiliary.get('schematic_only'):raise ValueError('Distinct physical testbench parts required')
                        refs.add(ref)
                        s.add_part(ref,auxiliary['symbol'],auxiliary['footprint'],
                                   nets=auxiliary['nets'],value=auxiliary['value'],
                                   no_connect=auxiliary.get('no_connect',[]),pad_map=auxiliary.get('pad_map',{}))
                    s.write(tmp/'model_test.kicad_sch')
                finally:schematic.SYMBOL_DIRS=old
                auxiliary_bindings=testbench.get('devices',{})
                if not isinstance(auxiliary_bindings,dict) or set(auxiliary_bindings)!=refs-{'U1'}:
                    raise ValueError('Every auxiliary part needs an explicit model; DUT binding cannot be replaced')
                plan=dict(required=['function'],devices=dict(auxiliary_bindings,U1=binding),tests=testbench['tests'])
                if not plan['tests'] or any(t.get('backend')!='spice' or t.get('category')!='function' for t in plan['tests']):
                    raise ValueError('Nonempty SPICE function qualification tests required')
                parts=[part,*auxiliaries]
                spec=dict(board={'w':30,'h':20,'layers':2},parts=parts,
                          nets={n:{} for p in parts for n in p['nets'].values()},analysis=plan)
                result=analysis.run_analyses(spec,tmp/'model_test.kicad_sch',None,tmp/'reports',base_dir=root)
                # Retain actual measurements and logs in the qualification report;
                # qualification also works against read-only installed packages.
                result['logs']={str(p.relative_to(tmp/'reports')):p.read_text() for p in (tmp/'reports').rglob('ngspice.log')}
                checks[name]=result
        except Exception as e:checks[name]=v.result('UNKNOWN','model qualification',[str(e)])
    return v.aggregate(checks)


def bind(part,model_id):
    """Copy an installed, requalified model into this workspace and return binding."""
    from tools import knowledge,intake
    facts=knowledge.component_facts(part.get('mpn',''))
    if not facts or not facts.get('_package_root'):raise ValueError('Exact MPN needs an installed component package')
    if (part['symbol'],part['footprint'],part.get('pad_map',{}))!=(facts['symbol'],facts['footprint'],facts.get('pad_map',{})):
        raise ValueError('Model package CAD identities/pad mapping differ from component')
    root=Path(facts['_package_root']);result=intake.qualify_component(root,persist=False)
    if result['status']!='PASS':raise ValueError('Component/model requalification failed: '+json.dumps(result))
    defs=json.loads((root/'models.json').read_text())
    if model_id not in defs:raise ValueError('Unknown model ID; available: '+', '.join(defs))
    binding=copy.deepcopy(defs[model_id]['binding']);units=instances(binding)
    for unit in units:
        source=(root/unit['file']).resolve()
        if root not in source.parents:raise ValueError('Model path escapes package')
        target=Path('.pcb/models')/v.file_hash(source)/source.name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and v.file_hash(target)!=unit['sha256']:raise ValueError('Existing model modified')
        if not target.exists():shutil.copy2(source,target)
        metadata=Path(str(source)+'.source.json')
        if metadata.is_file():shutil.copy2(metadata,str(target)+'.source.json')
        unit['file']=str(target)
    bound=units[0] if len(units)==1 else {'instances':units}
    coverage(part,bound)
    return bound
