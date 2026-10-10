"""File-backed public PCB operations; a workspace contains one board/spec pair.

Native calls run in isolated tool subprocesses. Reports keep source hashes;
files and historical checks survive invalidation and are never implicit PASS.
"""
from __future__ import annotations
from pathlib import Path
import functools,inspect,json,math,os,shutil,tempfile,types,uuid
from typing import Literal,Optional,Union,get_args,get_origin,get_type_hints
from tools import design,bootstrap,validation as v,knowledge,pcb_editor as ed,kicad_cli as kc,routing,verify,postroute,analysis,task_contract

from tools.argument_schema import validate as _validate_arguments, InputError, error_observation, decode_arguments
from tools import observation_schema

PUBLIC={}

# Capability groups are a presentation/transport boundary over the same
# canonical registry.  A tool may belong to more than one group when it is a
# genuine hand-off between stages (for example, netlist parity is both a
# schematic and a verification operation).  Keep this table explicit: an
# unclassified public operation must fail the registry self-check rather than
# silently becoming available in every agent session.
TOOL_GROUPS = {
    'core': {
        'project_status','new_project','set_requirements','view_design',
        'inspect_tool','list_references','read_reference',
    },
    'components': {
        'lookup_component','prepare_component','bind_model','qualify_component',
        'install_component','configure_component','discover_artifacts',
        'fetch_artifact','read_datasheet','create_symbol','add_symbol_pin',
        'update_symbol_pin','remove_symbol_pin','create_footprint',
        'add_footprint_pad','update_footprint_pad','remove_footprint_pad',
        'search_components','inspect_symbol','check_component_datasheet',
    },
    'schematic': {
        'add_component','remove_component','add_net','remove_net','rename_net',
        'connect_pin','disconnect_pin','add_no_connect','set_component_value',
        'assign_footprint','add_power_symbol','set_net_rule','generate_schematic',
        'inspect_schematic','place_schematic_component','set_schematic_field',
        'set_schematic_wire','remove_schematic_item','set_schematic_label',
        'verify_schematic','update_pcb','export_netlist','add_sheet',
        'remove_sheet','assign_sheet','set_sheet_port','compare_schematic_pcb',
    },
    'pcb': {
        'generate_pcb','update_pcb','inspect_board','add_track','add_arc','add_via',
        'unroute','place_component','lock_component','remove_board_item',
        'set_track_width','set_pad_zone_connection','set_component_text',
        'set_board_outline','set_layer_count','add_zone','add_keepout',
        'add_text','fill_zones','route_differential_pair','tune_length',
        'route_board','checkout_board',
    },
    'verification': {
        'inspect_board','inspect_schematic','verify_schematic','run_drc',
        'run_erc','run_analysis','compare_schematic_pcb','check_power_tree',
        'check_interface','check_footprints','check_component_datasheet',
        'verify_design','project_status',
    },
    'delivery': {
        'export_project','save_project_copy','prepare_3d_model','export_3d',
        'finalize_claims',
    },
}


def _normalize_observation(name, result):
    """Add the shared observation envelope without discarding tool details."""
    if not isinstance(result, dict):
        return result
    out=dict(result)
    status=out.get('status')
    if status is None:
        if out.get('verification') == 'REQUIRED' or out.get('downstream') == 'REVALIDATE' or out.get('changed_files'):
            status='MODIFIED'
        elif 'images' in out:
            status='PASS'
        else:
            status='PASS'
        out['status']=status
    out.setdefault('scope', name)
    if name in ('inspect_board','inspect_schematic','project_status','inspect_tool','list_references','read_reference',
                'lookup_component','search_components','inspect_symbol','read_datasheet','discover_artifacts'):
        out.setdefault('verification_role','observation_only')
    out.setdefault('next_action', {
        'PASS':'Inspect the returned evidence and continue to the next applicable stage.',
        'UNCHANGED':'No design change occurred; keep current evidence and continue without regeneration.',
        'ALREADY_PRESENT':'The requested object already exists; consult check freshness before scheduling verification.',
        'NOT_APPLICABLE':'This check is outside the fixed requirements; continue with the remaining applicable checks.',
        'STAGED':'The library draft is staged; inspect and qualify it before using it as a sourced device.',
        'FOUND':'Inspect the retrieved candidates and choose an appropriate source artifact.',
        'NO_LINKS':'No downloadable candidates were found on this page; inspect another relevant source.',
        'GENERATED':'Inspect the generated files and run the affected native checks.',
        'COPIED':'The candidate copy is saved; copying does not change the source or grant verification.',
        'MODIFIED':'Run the affected ERC/DRC or verification stage before relying on this change.',
        'FAIL':'Inspect the reported objects and make the smallest local correction, then rerun this stage.',
        'UNKNOWN':'Inspect the saved report and supply the missing evidence before claiming PASS.',
        'STALE':'Refresh the current candidate and rerun this check.',
    }.get(status, 'Inspect the returned observation and follow its verification requirement.'))
    return out


def parse_tool_groups(groups=None):
    """Normalize a group selection; ``None``/empty means the legacy full set."""
    if groups is None or groups == '' or groups == []:
        return None
    if isinstance(groups, str):
        groups = [part.strip() for part in groups.split(',') if part.strip()]
    if not isinstance(groups, (list, tuple, set)) or not groups:
        raise ValueError('groups must be a comma-separated string or non-empty sequence')
    selected = set(groups)
    if any(not isinstance(group, str) or not group for group in selected):
        raise ValueError('groups must contain non-empty strings')
    unknown = sorted(selected - set(TOOL_GROUPS))
    if unknown:
        raise ValueError('Unknown tool group(s): '+', '.join(unknown)+'; choose from '+', '.join(sorted(TOOL_GROUPS)))
    return selected


def tool_groups():
    """Return the public capability map and an explicit coverage audit."""
    assigned = set().union(*TOOL_GROUPS.values()) if TOOL_GROUPS else set()
    public = set(PUBLIC)
    return {
        'groups': {name: sorted(names & public) for name, names in TOOL_GROUPS.items()},
        'unassigned': sorted(public - assigned),
        'unknown_entries': sorted(assigned - public),
        'group_count': len(TOOL_GROUPS),
        'tool_count': len(public),
        'selection_hint': 'PCB_TOOL_GROUPS=core,schematic,pcb,verification',
    }


def _tool(fn):
    """One registry for direct Python calls, model schemas and subprocess dispatch."""
    @functools.wraps(fn)
    def checked(*args,**kwargs):
        try:
            bound=inspect.signature(fn).bind_partial(*args,**kwargs) if args else None
            _validate_arguments(dict(bound.arguments) if bound else kwargs,_schema(fn))
        except InputError as error:
            error.operation_executed=False
            raise
        return _normalize_observation(fn.__name__,fn(*args,**kwargs))
    if fn.__name__ in PUBLIC:raise ValueError('Duplicate public operation')
    PUBLIC[fn.__name__]=checked
    return checked


def _write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False,allow_nan=False));tmp.replace(path)


def _spec():
    s=json.loads(Path('spec.json').read_text());design.validate(s);return s


def _verification_spec():
    return task_contract.effective_spec(_spec(),task_contract.load())


def _analysis_applicability():
    contract=task_contract.load()
    if not contract or task_contract.is_brief(contract):return None
    return contract.get('verification_policy',{}).get('analysis',contract['requirements'].get('analysis'))


def sources():
    names=('spec.json','board.kicad_sch','board.kicad_pcb','board.kicad_pro','board.kicad_dru','sym-lib-table','fp-lib-table')
    hashes={n:v.file_hash(n) for n in names if Path(n).is_file()}
    if Path('board.kicad_sch').is_file():
        from tools import hierarchy
        hashes.update({str(Path(p).relative_to(Path.cwd())):digest for p,digest in hierarchy.hashes('board.kicad_sch').items()})
    if Path('spec.json').is_file():
        hashes.update(analysis.input_hashes(_spec(),base_dir=Path.cwd()))
        fixed=analysis.input_hashes(_verification_spec(),base_dir=Path.cwd())
        hashes.update({k:value for k,value in fixed.items() if k!='spec'})
        # macOS commonly spells the same temporary directory as ``/var`` in
        # the contract and ``/private/var`` after Path.resolve(). Keep the
        # lexical public spelling as an alias in the observation receipt so a
        # declared external input is reported under the path the task gave us.
        for key,value in list(hashes.items()):
            if key.startswith('/private/'):
                lexical=key[len('/private'):]
                if Path(lexical).is_file():hashes.setdefault(lexical,value)
    for folder in ('.pcb/libraries','.pcb/cad-libraries','.pcb/models','.pcb/3dmodels','.pcb/authoring'):
        for path in Path(folder).rglob('*'):
            if path.is_file():hashes[str(path)]=v.file_hash(path)
    return hashes


def _record(stage,report):
    from tools import diagnostics
    report=dict(report,sources=sources(),**task_contract.binding())
    # Every public check has the same actionable envelope.  Native details
    # remain nested under ``report`` but the key facts are promoted so clients
    # do not need a different parser for ERC, DRC and analysis adapters.
    report.setdefault('scope', stage)
    status = report.get('status', 'UNKNOWN')
    if status == 'FAIL':
        action = {
            'erc':'Inspect failing UUIDs/pins on the named sheet; make a local correction, then rerun run_erc and netlist parity.',
            'drc':'Inspect failing UUIDs/layers and current geometry; make a local correction, then rerun run_drc.',
            'analysis':'Inspect the failed assertion, model and conditions; correct its cause and rerun the affected analysis.',
        }.get(stage, 'Inspect the listed failing checks and objects, correct their causes locally, then rerun the affected check.')
    else:
        action = {
            'PASS':'This declared check passed. Preserve its revision-bound evidence and continue; do not edit or rerun solely because of this result.',
            'NOT_APPLICABLE':'The fixed requirements exempt this check. Continue with applicable checks; this is not electrical qualification.',
            'UNKNOWN':'Read the listed missing evidence or unavailable backend; do not infer a CAD fault or change the circuit without a failing check.',
            'STALE':'Inspect the current revision and refresh this check before relying on its verdict.',
            'UNCHANGED':'No design change occurred. Existing checks remain usable if their source hashes are still current.',
        }.get(status, 'Inspect the execution result and current candidate before deciding the next action.')
    report.setdefault('next_action', action)
    native = report.get('report')
    if isinstance(native, dict):
        for key in ('available', 'report_valid', 'returncode', 'report_errors', 'violations', 'counts'):
            if key in native and key not in report:
                report[key] = native[key]
    tests=diagnostics.test_results(report)
    if tests:report['test_results']=tests
    path=Path('.pcb/checks')/(stage+'-'+uuid.uuid4().hex+'.json')
    report['feedback']=diagnostics.summarize(report,stage,path)
    _write(path,report)
    state=json.loads(Path('.pcb/state.json').read_text()) if Path('.pcb/state.json').exists() else {}
    state[stage]={'path':str(path),'sources':report['sources'],'status':report['status'],
                  'requirements_sha256':report.get('requirements_sha256')}
    _write('.pcb/state.json',state)
    return dict(report,report_path=str(path))


@_tool
def project_status(requirements_path: str = '', offset: int = 0, limit: int = 10, check_stage: str = '',
                   check_view: Literal['tests','findings'] = 'tests', item_id: str = '') -> dict:
    """Inspect public requirements or a fresh check's paged tests/findings. item_id filters findings by exact object UUID; stale object locations are withheld."""
    if check_stage:
        if requirements_path:raise ValueError('Select requirements_path or check_stage, not both')
        if item_id and check_view != 'findings':raise ValueError('item_id requires check_view=findings')
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('offset must be a nonnegative integer and limit must be 1..50')
        state=json.loads(Path('.pcb/state.json').read_text()) if Path('.pcb/state.json').exists() else {}
        if check_stage not in state:
            error=InputError('UNKNOWN_CHECK_STAGE','arguments.check_stage',check_stage,
                {'enum':list(state)},'Use a stage listed in project_status checks, or execute the required check first.')
            error.operation_executed=False;raise error
        entry=state[check_stage];path=Path(entry['path']).resolve()
        if not path.is_relative_to(Path('.pcb/checks').resolve()):raise ValueError('Check report must be inside .pcb/checks')
        report=json.loads(path.read_text())
        from tools import diagnostics
        fresh=entry['sources']==sources() and entry.get('requirements_sha256')==task_contract.binding().get('requirements_sha256')
        if check_view == 'findings':
            all_items=diagnostics.summarize(report,check_stage,path,limit=1000000)['findings'] if fresh else []
            if item_id:
                all_items=[item for item in all_items if any(
                    isinstance(obj,dict) and (obj.get('uuid')==item_id or obj.get('id')==item_id)
                    for obj in item.get('objects',[]))]
        else:all_items=diagnostics.test_results(report) if fresh else []
        items=all_items[offset:offset+limit]
        next_offset=offset+len(items) if offset+len(items)<len(all_items) else None
        query={'check_stage':check_stage,'check_view':check_view,'offset':next_offset,'limit':limit}
        if item_id:query['item_id']=item_id
        result={'check_stage':check_stage,'status':entry['status'] if fresh else 'STALE','fresh':fresh,
                'report_path':entry['path'],
                'check_view':check_view,'item_id':item_id or None,
                'total_items':len(all_items),'offset':offset,'has_more':next_offset is not None,
                'page_complete':True,'section_complete':offset==0 and len(items)==len(all_items),'next_offset':next_offset,
                'next_call':{'tool':'project_status','arguments':query} if next_offset is not None else None,
                'next_action':'Rerun this check before using old object locations.' if not fresh else
                    'Inspect these objects and make a local correction, then rerun the affected check.' if check_view=='findings' else
                    'Use test receipts only within their stated analysis scope.',
                'scope':'Test citations are not aggregate electrical qualification. Stale reports must be rerun before submission.'}
        result['findings' if check_view=='findings' else 'test_results']=items
        return result
    if check_view != 'tests' or item_id:
        raise ValueError('check_view and item_id require check_stage')
    if not requirements_path and (offset or limit!=10):
        error=InputError('MISSING_REQUIREMENT_PATH','arguments.requirements_path',requirements_path,
            {'type':'string','source':'section path from project_status public_requirements index'},
            'Pagination needs a requirements_path. Repeat the selected section path and set offset to its next_offset; limit is a page size, not a cursor. Call with no arguments to inspect overall state.')
        error.operation_executed=False
        raise error
    if requirements_path:
        return {'task':task_contract.binding(),
                'public_requirements':task_contract.observation(task_contract.load(),requirements_path,offset,limit)}
    now=sources();state=json.loads(Path('.pcb/state.json').read_text()) if Path('.pcb/state.json').exists() else {}
    for entry in state.values():
        entry['fresh']=entry['sources']==now and entry.get('requirements_sha256')==task_contract.binding().get('requirements_sha256')
        if not entry['fresh']:entry['status']='STALE'
        entry.pop('sources',None)
    repair=[]
    ordered=sorted(reversed(list(state.items())),key=lambda pair:(not pair[1]['fresh'],pair[1]['status']!='FAIL'))
    for stage,entry in ordered:
        path=Path(entry['path'])
        if not path.is_file():continue
        # Old reports remain useful evidence, but their object locations must
        # never be presented as current after a CAD or dependency edit.
        report=json.loads(path.read_text())
        feedback=report.get('feedback',{})
        if not feedback.get('findings'):continue
        repair.append({'stage':stage,'fresh':entry['fresh'],'report_path':str(path),
                       'findings':feedback['findings'][:4] if entry['fresh'] else [],
                       'next_action':report.get('next_action','Inspect the evidence within its declared scope') if entry['fresh'] else 'Rerun this check before using its old object locations'})
        if len(repair)==3:break
    return {'files':now,'checks':state,'repair_context':repair,'task':task_contract.binding(),
            'public_requirements':task_contract.observation(task_contract.load(),requirements_path,offset,limit),
            'functional_coverage':task_contract.functional_coverage(task_contract.load()),
            'analysis_readiness':analysis.backend_readiness(_verification_spec()) if 'spec.json' in now else None,
            'next_action':'Create intent' if 'spec.json' not in now else 'Generate/check schematic' if 'board.kicad_sch' not in now else 'Check schematic and generate PCB' if 'board.kicad_pcb' not in now else 'Inspect, route and verify actual board'}


@_tool
def new_project(width_mm: float, height_mm: float, layers: int = 2) -> dict:
    """Create design intent; prepared libraries are retained. Dimensions in mm, even copper layers."""
    # Component research/authoring can legitimately precede a design. Library
    # hashes are source dependencies, not proof that a project already exists.
    # lexists also blocks broken symlinks and directories without reading or
    # overwriting an existing (possibly malformed) design.
    occupied=[name for name in ('spec.json','board.kicad_sch','board.kicad_pcb',
                              'board.kicad_pro','board.kicad_dru') if os.path.lexists(name)]
    if occupied:raise FileExistsError('Project already exists ('+', '.join(occupied)+'); use atomic edits')
    return design.save_spec(design.new_design(width_mm,height_mm,layers),'spec.json')


def _edit(operation,**args):
    before=_spec(); after=getattr(design,operation)(before,**args)
    if design.fingerprint(before)==design.fingerprint(after):
        return dict(status='UNCHANGED',path='spec.json',sha256=design.fingerprint(before),changed_files=[],verification_required=False)
    return design.save_spec(after,'spec.json')


@_tool
def add_component(ref: str, symbol: str, value: str = '', footprint: str = '') -> dict:
    """Add one disconnected component using canonical library pins; connect_pin sets nets. Regenerate schematic, then update_pcb for an existing board."""
    return _edit('add_component',ref=ref,symbol=symbol,value=value,footprint=footprint)


@_tool
def remove_component(ref: str) -> dict:
    """Remove one component from intent; preserve requirements so omissions are detected."""
    return _edit('remove_component',ref=ref)


@_tool
def add_net(name: str, role: Literal['signal','power','ground','chassis'] = 'signal', properties: Optional[dict] = None) -> dict:
    """Declare one net with explicit electrical properties."""
    return _edit('add_net',name=name,role=role,**(properties or {}))


@_tool
def remove_net(name: str) -> dict:
    """Remove one unused net; disconnect its pins first."""
    return _edit('remove_net',name=name)


@_tool
def rename_net(old: str, new: str) -> dict:
    """Rename one net and typed references in intent; regenerate derived CAD."""
    return _edit('rename_net',old=old,new=new)


@_tool
def connect_pin(ref: str, pin: str, net: str) -> dict:
    """Connect one symbol pin to a declared net, clearing its NC marker."""
    return _edit('connect_pin',ref=ref,pin=pin,net=net)


@_tool
def disconnect_pin(ref: str, pin: str) -> dict:
    """Disconnect one pin; leaving it open still needs electrical review."""
    return _edit('disconnect_pin',ref=ref,pin=pin)


@_tool
def add_no_connect(ref: str, pin: str) -> dict:
    """Mark one disconnected pin intentionally NC; never silently disconnect it."""
    return _edit('add_no_connect',ref=ref,pin=pin)


@_tool
def set_component_value(ref: str, value: str) -> dict:
    """Change one component value in intent; rerun electrical checks."""
    return _edit('set_component_value',ref=ref,value=value)


@_tool
def assign_footprint(ref: str, footprint: str, pad_map: Optional[dict] = None) -> dict:
    """Assign one footprint with an optional explicit symbol-pin to pad mapping."""
    return _edit('assign_footprint',ref=ref,footprint=footprint,pad_map=pad_map)


@_tool
def add_power_symbol(ref: str, symbol: str, net: str) -> dict:
    """Add one schematic power symbol/PWR_FLAG to a verified supply path."""
    return _edit('add_power_symbol',ref=ref,symbol=symbol,net=net)


@_tool
def set_net_rule(net: str, rules: dict) -> dict:
    """Set one net's explicit geometry constraints in mm; regeneration applies them to CAD."""
    return _edit('set_net_rule',net=net,**rules)


@_tool
def set_requirements(section: str, value: dict) -> dict:
    """Set model intent/diagnostics for constraints, postroute, acquisition or analysis; power_tree/interfaces use {items:[...]}. Bound public task requirements stay authoritative."""
    if section not in ('constraints','postroute','acquisition','analysis','power_tree','interfaces'):raise ValueError('Unsupported requirement section')
    if section=='analysis':analysis.validate_plan(value,base_dir=Path.cwd())
    if section in ('power_tree','interfaces'):
        if set(value)!={'items'} or not isinstance(value['items'],list):raise ValueError('Expected {items:[...]}')
        value=value['items']
        problems=v.electrical_intent_issues(section,value)
        if problems:raise ValueError('; '.join(problems)+f'; inspect_tool("{section}") shows the contract; no edit was applied')
    s=_spec();s[section]=value;return design.save_spec(s,'spec.json')


def _backup(names):
    present=[Path(n) for n in names if Path(n).is_file()]
    if present:
        dest=Path('.pcb/history')/uuid.uuid4().hex;dest.mkdir(parents=True)
        for path in present:
            relative=path.resolve().relative_to(Path.cwd())
            target=dest/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
        return dest


@_tool
def generate_schematic(overwrite: bool = False) -> dict:
    """Generate board.kicad_sch and project tables from saved intent. Explicit overwrite archives old files and invalidates checks."""
    if Path('board.kicad_sch').exists() and not overwrite:raise FileExistsError('Schematic exists; inspect it or explicitly overwrite after editing spec')
    # Generate separately so a failure never destroys current work or board rules.
    with tempfile.TemporaryDirectory(dir='.') as tmp:
        from tools import schematic_ops
        result=bootstrap.build(tmp,'board',schematic_ops.inherit_layout(_spec(),'board.kicad_sch'),schematic_only=True)
        schematic_ops.preserve_ids('board.kicad_sch',Path(tmp)/'board.kicad_sch')
        from tools import library
        from tools import hierarchy
        _backup([*(hierarchy.dependencies('board.kicad_sch') if Path('board.kicad_sch').is_file() else []),'sym-lib-table','fp-lib-table'])
        library.copy_project_libraries(tmp,Path.cwd())
        for path in Path(tmp).iterdir():
            if path.is_file() and (path.name in ('board.kicad_sch','sym-lib-table','fp-lib-table') or not Path(path.name).exists()):shutil.copy2(path,path.name)
    return {'status':'GENERATED','files':sources(),'verification':'REQUIRED'}


@_tool
def view_design() -> dict:
    """Render current native schematic and top PCB views with source/image hashes; MCP delivers images only when the controller enables vision. Partial views are not acceptance."""
    from tools import model_vision
    _,records=model_vision.observe(Path.cwd(),Path.cwd()/'.pcb/views'/uuid.uuid4().hex)
    return dict(status='RENDERED',images=records,scope='First schematic sheet and front PCB layers only; use inspect_schematic for other sheets and export_3d for assembly')


@_tool
def inspect_schematic(sheet: str = 'board.kicad_sch', ref: Optional[str] = None, item_id: Optional[str] = None,
                      kind: str = '', offset: int = 0, limit: int = 15, expected_sha256: str = '') -> dict:
    """Read complete revision-bound sheet objects in mm. Follow next_call for remaining objects; ref/UUID/kind filters cover only their query, never the whole sheet."""
    from tools import schematic_observation
    return schematic_observation.inspect(sheet,ref,item_id,kind,offset,limit,expected_sha256)


def _edit_schematic(sheet, expected_sha256, edit, preserve_connectivity=False):
    from tools import schematic_ops, sexpr, hierarchy
    path=schematic_ops.source(sheet);before=v.file_hash(path)
    if expected_sha256 is not None and before!=expected_sha256:raise ValueError('STALE_SCHEMATIC: inspect this sheet before editing')
    tree=sexpr.parse(path.read_text());canonical=sexpr.dump(tree);edit(tree)
    if sexpr.dump(tree)==canonical:
        return dict(status='UNCHANGED',sheet=sheet,schematic_sha256=before,changed_files=[],verification_required=False)
    with tempfile.TemporaryDirectory(dir='.') as tmp:
        tmp=Path(tmp);hierarchy.copy_sheets('board.kicad_sch',tmp)
        candidate=tmp/path.relative_to(Path.cwd());candidate.write_text(sexpr.dump(tree)+'\n')
        if preserve_connectivity:
            a=v.export_netlist('board.kicad_sch',tmp/'before.xml');b=v.export_netlist(tmp/'board.kicad_sch',tmp/'after.xml')
            if a['status']!='PASS' or b['status']!='PASS' or a['data']['pins']!=b['data']['pins']:
                raise ValueError('Local edit would change connectivity or cannot be verified; no source was changed. Edit the affected wires explicitly.')
        _backup([path]);candidate.replace(path)
    return dict(status='MODIFIED',sheet=sheet,previous_sha256=before,schematic_sha256=v.file_hash(path),verification='REQUIRED')


@_tool
def place_schematic_component(ref: str, x_mm: float, y_mm: float, rotation: Optional[float] = None, unit: int = 1, sheet: str = 'board.kicad_sch', expected_sha256: Optional[str] = None) -> dict:
    """Move/rotate one existing schematic unit and its attached endpoints; preserve UUIDs and netlist or reject atomically. Board placement is separate."""
    from tools import schematic_ops
    return _edit_schematic(sheet,expected_sha256,lambda tree:schematic_ops.move(tree,ref,unit,x_mm,y_mm,rotation),True)


@_tool
def set_schematic_field(ref: str, field: Literal['Reference','Value','Footprint'], x_mm: float, y_mm: float, rotation: float = 0, visible: bool = True, unit: int = 1, sheet: str = 'board.kicad_sch', expected_sha256: Optional[str] = None) -> dict:
    """Set one schematic field's drawing pose/visibility without changing its value or connectivity."""
    from tools import schematic_ops,sexpr
    def edit(tree):
        obj=schematic_ops.instance(tree,ref,unit)
        prop=next(p for p in sexpr.children(obj,'property') if p[1]==field)
        sexpr.child(prop,'at')[1:4]=[x_mm,y_mm,rotation]
        effects=sexpr.child(prop,'effects')
        effects[:]=[x for x in effects if not (isinstance(x,list) and x[0]=='hide') and x!='hide']
        if not visible:effects.append(['hide','yes'])
    return _edit_schematic(sheet,expected_sha256,edit,True)


@_tool
def set_schematic_wire(points: list[list[float]], item_id: Optional[str] = None, sheet: str = 'board.kicad_sch', expected_sha256: Optional[str] = None) -> dict:
    """Create or replace one schematic wire segment with two endpoints in mm; rerun ERC and netlist parity after connectivity edits."""
    from tools import sexpr
    if len(points)!=2 or points[0]==points[1]:raise ValueError('One wire needs two distinct endpoints')
    identity=item_id or str(uuid.uuid4())
    def edit(tree):
        wire=['wire',['pts',*([ 'xy',*p] for p in points)],['stroke',['width',0],['type','default']],['uuid',sexpr.Quoted(identity)]]
        if item_id:
            matches=[n for n in sexpr.children(tree,'wire') if sexpr.child(n,'uuid')[1]==item_id]
            if len(matches)!=1:raise ValueError('Unknown wire UUID')
            tree[tree.index(matches[0])]=wire
        else:tree.append(wire)
    return dict(_edit_schematic(sheet,expected_sha256,edit),item_id=identity)


@_tool
def remove_schematic_item(item_id: str, sheet: str = 'board.kicad_sch', expected_sha256: Optional[str] = None) -> dict:
    """Remove one native wire/label/junction/NC marker by UUID; circuit intent is unchanged, so ERC and parity must be rerun."""
    from tools import sexpr
    def edit(tree):
        matches=[n for n in tree if isinstance(n,list) and n[0] in ('wire','label','global_label','junction','no_connect') and sexpr.child(n,'uuid',['uuid',''])[1]==item_id]
        if len(matches)!=1:raise ValueError('Unknown or unsupported schematic object UUID')
        tree.remove(matches[0])
    return _edit_schematic(sheet,expected_sha256,edit)


@_tool
def set_schematic_label(net: str, x_mm: float, y_mm: float, rotation: float = 0, kind: Literal['local','global'] = 'global', item_id: Optional[str] = None, sheet: str = 'board.kicad_sch', expected_sha256: Optional[str] = None) -> dict:
    """Create or replace one label for a declared net at an actual wire/pin anchor; rerun ERC and netlist parity."""
    from tools import sexpr
    if net not in _spec().get('nets',{}):raise ValueError('Declare the net with add_net first')
    if rotation%90:raise ValueError('Label rotation must be a multiple of 90 degrees')
    identity=item_id or str(uuid.uuid4())
    def edit(tree):
        label=['global_label' if kind=='global' else 'label',sexpr.Quoted(net),['at',x_mm,y_mm,rotation],
               ['effects',['font',['size',1.27,1.27]],['justify','left']],['uuid',sexpr.Quoted(identity)]]
        if kind=='global':label.append(['shape','bidirectional'])
        if item_id:
            matches=[n for n in tree if isinstance(n,list) and n[0] in ('label','global_label') and sexpr.child(n,'uuid',['uuid',''])[1]==item_id]
            if len(matches)!=1:raise ValueError('Unknown label UUID')
            tree[tree.index(matches[0])]=label
        else:tree.append(label)
    return dict(_edit_schematic(sheet,expected_sha256,edit),item_id=identity)


@_tool
def verify_schematic() -> dict:
    """Fresh ERC, exact netlist, footprints, structural rules and declared electrical checks; missing datasheets remain UNKNOWN."""
    s=_verification_spec();contract=task_contract.load();out=Path('.pcb/reports')/uuid.uuid4().hex;out.mkdir(parents=True)
    from tools import hierarchy
    net=v.export_netlist('board.kicad_sch',out/'netlist.xml')
    cad={'erc':run_erc() if contract is not None else v.run_erc('board.kicad_sch',out/'erc.json'),'netlist':net,
         'structural':knowledge.check_schematic_rules(s),'footprints':v.check_footprints(s),
         'hierarchy':hierarchy.check_structure(s,'board.kicad_sch')}
    if net['status']=='PASS':cad['intent']=v.compare_spec_netlist(s,net['data'])
    if contract is not None and net['status']=='PASS':
        from tools import requirements
        # Board dimensions here are declared intent. Final acceptance measures
        # the actual native board independently, under the same limits.
        cad['task_requirements']=(v.result('NOT_APPLICABLE','uncompiled brief predicates',
            ['Full brief coverage remains UNKNOWN; candidate checks are diagnostics']) if task_contract.is_brief(contract)
            else requirements.check(contract['requirements'],s,net['data'],{'board':s['board']}))
    electrical=analysis.electrical_checks(s,'board.kicad_sch',None,out/'electrical',stage='schematic',applicability=_analysis_applicability())
    if contract and contract.get('verification_policy'):
        from tools import verification_policy
        policy_check=verification_policy.evaluate({'checks':dict(cad,**electrical)},contract['verification_policy'],
            tests=s.get('analysis',{}).get('tests',[]),stage='schematic',
            schematic_sha256=v.file_hash('board.kicad_sch'),spec_sha256=design.fingerprint(s))
        return _record('schematic',dict(v.aggregate({'cad':v.aggregate(cad),'schematic_requirements':policy_check}),
            raw_electrical=electrical, electrical_status='UNKNOWN',
            qualification_scope='Schematic stage only; full electrical qualification requires verify_design on the complete candidate.',
            verification_role='fixed_policy_schematic_stage'))
    return _record('schematic',dict(v.aggregate({'cad':v.aggregate(cad),'schematic_requirements':v.aggregate(electrical)}),
        electrical_status='UNKNOWN',qualification_scope='Schematic-stage checks do not establish complete-board electrical qualification.'))


@_tool
def generate_pcb(cad_only_reason: str = '', overwrite: bool = False) -> dict:
    """Freshly gate schematic, then generate unrouted PCB and explicit project rules. Missing electrical evidence requires task-authorized CAD-only reason; FAIL always blocks. Overwrite archives old PCB."""
    if Path('board.kicad_pcb').exists() and not overwrite:raise FileExistsError('PCB exists; use track/move tools to preserve routing, or explicitly overwrite')
    checked=verify_schematic();checks=checked['checks']
    if checks['cad']['status']!='PASS' or checks['schematic_requirements']['status']=='FAIL':return dict(checked,blocked='SCHEMATIC_OR_ELECTRICAL_FAILURE')
    if checks['schematic_requirements']['status'] not in ('PASS','NOT_APPLICABLE') and not cad_only_reason.strip():return dict(checked,blocked='MISSING_ELECTRICAL_EVIDENCE')
    contract=task_contract.load()
    if (checks['schematic_requirements']['status'] not in ('PASS','NOT_APPLICABLE') and contract
            and not task_contract.is_brief(contract) and contract['scope']!='cad_prototype'):
        return dict(checked,blocked='TASK_REQUIRES_ELECTRICAL_EVIDENCE',
                    next_action='Supply the required electrical evidence; a free-text CAD-only reason cannot change task scope')
    s=_verification_spec()
    from tools.project_rules import apply
    with tempfile.TemporaryDirectory(dir='.') as tmp:
        built=bootstrap.build(tmp,'board',s);apply(Path(tmp)/'board.kicad_pcb',s)
        _backup(['board.kicad_pcb','board.kicad_pro'])
        for ext in ('.kicad_pcb','.kicad_pro'):shutil.copy2(Path(tmp)/('board'+ext),'board'+ext)
    from tools import mechanical
    mechanical.localize_available('board.kicad_pcb')
    parity=v.compare_schematic_pcb('board.kicad_sch','board.kicad_pcb',Path('.pcb/reports')/uuid.uuid4().hex,s)
    return _record('pcb_generation',dict(parity,placement=built.get('placement'),cad_only_reason=cad_only_reason,schematic_requirements_status=checks['schematic_requirements']['status'],electrical_status='UNKNOWN',next_action='Inspect actual pad geometry and place pending footprints inside the outline before routing; generation/parity is not layout or DRC acceptance'))


@_tool
def update_pcb(prune_affected: bool = False) -> dict:
    """Sync the schematic while retaining existing-net copper and footprint poses for local repair. True explicitly removes all tracks/vias on affected nets; orphan-net copper is always removed. Rerun DRC after ECO."""
    from tools import eco
    out=Path('.pcb/eco')/uuid.uuid4().hex
    result=eco.update('board.kicad_pcb','board.kicad_sch',_spec(),out,prune_affected)
    if result['status']=='PASS':
        _backup(['spec.json','board.kicad_pcb','board.kicad_pro'])
        design.save_spec(result['spec'],'spec.json')
        shutil.copy2(result['candidate'],'board.kicad_pcb')
        if (out/'board.kicad_pro').is_file():shutil.copy2(out/'board.kicad_pro','board.kicad_pro')
    result.pop('spec',None)
    return _record('eco',result)


@_tool
def inspect_board(include_copper: bool = False, include_geometry: bool = False, item_id: str = '', net_name: str = '',
                  collection: Literal['summary','copper','pads','parts','drawings'] = 'summary',
                  offset: int = 0, limit: int = 10, layer: str = '', bounds_mm: Optional[list[list[float]]] = None,
                  ref: str = '', expected_board_sha256: str = '') -> dict:
    """Read native board geometry. Use collection for complete revision-bound pages, bounds_mm=[[xmin,ymin],[xmax,ymax]] for envelope filtering, item_id for a UUID. inspect_tool documents type-specific output; geometry is not connectivity."""
    from tools import board_observation
    return board_observation.inspect(include_copper,include_geometry,item_id,net_name,collection,offset,limit,
                                     layer,bounds_mm,ref,expected_board_sha256)


def _edit_board(operations: list) -> dict:
    """Internal transaction shared by public board atoms; never a model action bag."""
    from tools import board_ops
    board=ed.load('board.kicad_pcb');before_spec=_spec()
    # Serialize before replacing either source. History permits recovery if the
    # process is interrupted between the two file replacements.
    with tempfile.TemporaryDirectory(dir='.') as tmp:
        baseline=Path(tmp)/'before.kicad_pcb';ed.save(board,baseline)
        s,changes=board_ops.apply(board,before_spec,operations)
        pcb=Path(tmp)/'board.kicad_pcb';ed.save(board,pcb);design.save_spec(s,Path(tmp)/'spec.json')
        if not any(c.get('modified',True) for c in changes):
            return {'status':'ALREADY_PRESENT','modified':False,'changes':changes,'verification':'REQUIRED'}
        if baseline.read_bytes()==pcb.read_bytes() and design.fingerprint(s)==design.fingerprint(before_spec):
            return dict(status='UNCHANGED',modified=False,changes=changes,changed_files=[],verification_required=False)
        _backup(['board.kicad_pcb','spec.json'])
        pcb.replace('board.kicad_pcb');(Path(tmp)/'spec.json').replace('spec.json')
    return {'status':'MODIFIED','changes':changes,'verification':'REQUIRED','sources':sources()}


@_tool
def configure_component(ref: str, properties: dict) -> dict:
    """Set explicit component properties; symbol changes preserve connected/NC pin numbers or reject, requiring renewed electrical review."""
    allowed={'symbol','mpn','operating','required_operating_checks','datasheet_facts','mechanical_pads'}
    if set(properties)-allowed:raise ValueError('Allowed component properties: '+', '.join(sorted(allowed)))
    s=_spec()
    if 'symbol' in properties:s=design.replace_symbol(s,ref,properties['symbol'])
    part=next(p for p in s['parts'] if p['ref']==ref);part.update(properties)
    return design.save_spec(s,'spec.json')


@_tool
def lookup_component(mpn: str) -> dict:
    """Read sourced exact-MPN facts; CAD name hits alone do not qualify a part."""
    facts=knowledge.component_facts(mpn)
    if not facts:return v.result('UNKNOWN','exact MPN',['No qualified facts found'])
    path=Path(facts.get('_package_root','/nonexistent'))/'models.json'
    if path.is_file():facts['models']={name:{key:entry[key] for key in ('scope','mapping_source')}
                                     for name,entry in json.loads(path.read_text()).items()}
    return facts


@_tool
def prepare_component(facts: dict, cad_sources: Optional[dict] = None, documents: Optional[list[dict]] = None, models: Optional[list[dict]] = None) -> dict:
    """Stage one MPN; facts.pins maps pin numbers to name/type. Omit installed CAD sources; drafts use only path/sha256/license. Read tools.md for nested evidence fields."""
    from tools import intake
    return intake.prepare_component({'facts':facts,'cad_sources':cad_sources or {},'documents':documents or [],'models':models or []},Path('.pcb/intake')/uuid.uuid4().hex)


@_tool
def bind_model(ref: str, model_id: str) -> dict:
    """Bind one exact-MPN component to an installed, requalified model; preserve bytes, terminal order and hashes."""
    from tools import models
    s=_spec();part=design._require(s,ref);binding=models.bind(part,model_id)
    s.setdefault('analysis',{}).setdefault('devices',{})[ref]=binding
    return design.save_spec(s,'spec.json')


def _staged_package(package):
    package=Path(package).resolve()
    if Path('.pcb/intake').resolve() not in package.parents:raise ValueError('Select a staged package in .pcb/intake')
    return package


@_tool
def qualify_component(package: str) -> dict:
    """Check one staged package's source evidence, reviewed pin/pad mapping and native CAD loading."""
    from tools import intake
    return intake.qualify_component(_staged_package(package))


@_tool
def install_component(package: str) -> dict:
    """Requalify and publish one staged package to this workspace's component library."""
    from tools import intake
    return intake.install_component(_staged_package(package),Path('.pcb/libraries'))


@_tool
def create_symbol(library_id: str, pins: list[dict], description: str = '') -> dict:
    """Stage a generic symbol with explicit pins; intake qualification is still required."""
    from tools import library
    return library.create_symbol(library_id, pins, description=description)


@_tool
def add_symbol_pin(library_id: str, pin: dict) -> dict:
    """Append one pin to a staged symbol, rejecting duplicate numbers and malformed data."""
    from tools import authoring
    return authoring.add_symbol_pin(library_id, pin)


@_tool
def create_footprint(library_id: str, pads: list[dict], description: str = '') -> dict:
    """Stage a generic footprint with explicit pad geometry; qualification remains mandatory."""
    from tools import library
    return library.create_footprint(library_id, pads, description=description)


@_tool
def add_footprint_pad(library_id: str, pad: dict) -> dict:
    """Append one pad to a staged footprint, rejecting duplicate pad numbers."""
    from tools import authoring
    return authoring.add_footprint_pad(library_id, pad)


@_tool
def update_symbol_pin(library_id: str, number: str, pin: dict) -> dict:
    """Replace one draft pin's complete definition while preserving its number; requalification required."""
    from tools import authoring
    return authoring.edit_pin(library_id,number,pin)


@_tool
def remove_symbol_pin(library_id: str, number: str) -> dict:
    """Remove one draft symbol pin by number; installed CAD and existing boards are not edited."""
    from tools import authoring
    return authoring.edit_pin(library_id,number)


@_tool
def update_footprint_pad(library_id: str, number: str, pad: dict) -> dict:
    """Replace one draft pad's complete geometry while preserving its number; requalification required."""
    from tools import authoring
    return authoring.edit_pad(library_id,number,pad)


@_tool
def remove_footprint_pad(library_id: str, number: str) -> dict:
    """Remove one unambiguous draft pad by number; installed CAD and existing boards are not edited."""
    from tools import authoring
    return authoring.edit_pad(library_id,number)


@_tool
def discover_artifacts(url: str) -> dict:
    """Inspect a controller-approved public landing page for datasheet/model/CAD links; retain source hash, never execute or choose a design for the model."""
    from tools import library
    return library.discover_artifacts(url,Path('downloads/discovery')/uuid.uuid4().hex)


@_tool
def fetch_artifact(url: str, destination: str, kind: Literal['symbol','footprint','datasheet','spice_model'], revision: str, license_url: str, expected_sha256: Optional[str] = None, archive_member: Optional[str] = None, archive_sha256: Optional[str] = None) -> dict:
    """Download one sourced file to downloads/<filename>; datasheet must be PDF. Retain provenance; never overwrite or auto-install."""
    from tools import library
    root=Path.cwd().resolve();target=Path(destination).resolve()
    if not target.is_relative_to(root/'downloads'):raise ValueError('Download destination must be inside workspace/downloads')
    return library.fetch_artifact(url,destination,kind,revision,license_url,expected_sha256,archive_member,archive_sha256)


@_tool
def read_datasheet(path: str, first_page: int = 1, max_pages: int = 3) -> dict:
    """Read bounded PDF pages from a downloaded or staged datasheet; return retained-file hash, not interpreted facts."""
    import subprocess
    root=Path.cwd().resolve();source=Path(path).resolve()
    if not any(source.is_relative_to(root/d) for d in ('downloads','.pcb/intake','.pcb/libraries')):
        raise ValueError('Read a retained datasheet under downloads or component packages')
    if not 1<=first_page or not 1<=max_pages<=10 or source.suffix.lower()!='.pdf':raise ValueError('PDF with positive page range, at most 10 pages required')
    if source.stat().st_size>64*1024*1024 or not source.read_bytes()[:5]==b'%PDF-':raise ValueError('Expected a bounded PDF')
    before=v.file_hash(source)
    result=subprocess.run(['pdftotext','-f',str(first_page),'-l',str(first_page+max_pages-1),'-layout',str(source),'-'],capture_output=True,text=True,timeout=30)
    if result.returncode:raise ValueError('PDF extraction failed: '+result.stderr[-1000:])
    if v.file_hash(source)!=before:raise ValueError('PDF changed during extraction')
    return dict(path=str(source.relative_to(root)),sha256=before,first_page=first_page,max_pages=max_pages,
                text=result.stdout[:20000],truncated=len(result.stdout)>20000,scope='Document text; diagrams may require visual review')


@_tool
def run_analysis(stage: Literal['schematic','final'] = 'final', basis: Literal['requirements','candidate'] = 'requirements') -> dict:
    """Run fixed requirement tests by default; basis=candidate runs optional design diagnostics without changing acceptance. stage=schematic defers PCB-bound analyses."""
    if stage not in ('schematic','final'):raise ValueError('stage must be schematic or final')
    task_contract.load()  # Validate the binding even for diagnostic execution.
    report=analysis.run_analyses(_spec() if basis=='candidate' else _verification_spec(),
                                'board.kicad_sch','board.kicad_pcb' if stage=='final' else None,
                                Path('.pcb/reports')/uuid.uuid4().hex,stage=stage,
                                applicability=_analysis_applicability() if basis=='requirements' else None)
    report['verification_role']='required_analysis' if basis=='requirements' and _analysis_applicability() is not None else 'candidate_diagnostic'
    return _record('analysis_diagnostic' if basis=='candidate' else 'analysis',report)


@_tool
def inspect_tool(name: str) -> dict:
    """Return a public tool contract or analysis/power_tree/interfaces schema without reading implementation source."""
    if name=='run_python':
        return {'name':'run_python',
                'description':'Sandboxed read-only candidate/report calculation; no CAD mutation, network, subprocess or evaluator access.',
                'parameters':{'type':'object','properties':{'code':{'type':'string'}},'required':['code'],'additionalProperties':False},
                'groups':['core'], 'output_schema':observation_schema.common_observation_schema(),
                'verification_role':'observation_only'}
    if name in ('power_tree','interfaces'):
        return dict(section=name,value_schema={'type':'object','properties':{'items':v.electrical_intent_schema(name)},'required':['items'],'additionalProperties':False},
                    units='V and A; pin identifiers are strings',scope='Declared DC budgets and interface pin/voltage predicates only; incomplete limits remain UNKNOWN',
                    reference='tools.md', groups=[group for group,names in TOOL_GROUPS.items() if (name in names or ('check_'+name) in names)],
                    output_schema=observation_schema.schema_for('check_power_tree' if name=='power_tree' else 'check_interface'))
    if name=='analysis':
        return {'required':['function','stability','protocol','physical','emc_precheck','emc'],'not_applicable':{'emc':'Explicit project-specific justification if only pre-compliance design is requested'},
                'reference':'/skills/pcb-design-e2e/references/analysis.md','devices':'Every physical ref: kind R/C/L/X/D/Q/M or exclude with reason; ordered symbol pins; model file/source/sha256',
                'tests':'id, category, backend (spice/geometry/external), assumptions, backend-specific parameters and assertions; SPICE terminals optionally map @aliases to real ref:pin endpoints so internal net names can vary; final protocol/RF/thermal/physical tests set bind_to_revision and retain evidence hashes',
                'output_schema':observation_schema.analysis_output_schema()}
    entry=next((d['function'] for d in definitions() if d['function']['name']==name),None)
    if entry is None:raise ValueError('Unknown tool: '+name+'; python3 -m tools.api lists canonical names')
    references={'run_analysis':['analysis.md','numerical.md','advanced-cad.md'],'verify_design':['verification.md','layout.md','advanced-cad.md'],
                'prepare_3d_model':['advanced-cad.md'],'export_3d':['advanced-cad.md'],'export_project':['advanced-cad.md','verification.md'],
                'place_component':['layout.md','tools.md'],'inspect_board':['layout.md','tools.md'],'route_board':['layout.md','advanced-cad.md'],
                'finalize_claims':['verification.md'],'route_differential_pair':['advanced-cad.md'],'tune_length':['advanced-cad.md']}
    result=dict(entry,references=references.get(name,['tools.md']))
    # ``inspect_board`` retains its richer geometry schema.  All other public
    # operations now advertise a stable observation contract as well.
    if name=='inspect_board':
        from tools import board_observation
        result['output_schema']=board_observation.output_schema()
    else:
        result['output_schema']=observation_schema.schema_for(name)
    result['groups']=[group for group,names in TOOL_GROUPS.items() if name in names]
    return result


def _reference_root() -> Path:
    configured=Path(os.environ.get('PCB_SKILLS','/skills'))/'pcb-design-e2e'/'references'
    if configured.is_dir():return configured.resolve()
    # This fallback is for source checkouts; production images use /skills.
    return (Path(__file__).resolve().parents[2]/'skills'/'pcb-design-e2e'/'references').resolve()


def _reference_path(name: str) -> Path:
    if not isinstance(name,str) or not name.strip():raise ValueError('Reference name is required')
    relative=Path(name)
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('Reference must stay under the PCB skill references directory')
    if relative.parts and relative.parts[0]=='references':relative=Path(*relative.parts[1:])
    root=_reference_root();candidate=(root/relative).resolve()
    if root not in candidate.parents or candidate.suffix!='.md' or not candidate.is_file():
        raise ValueError('Unknown reference; use list_references first')
    return candidate


@_tool
def list_references() -> dict:
    """List the allowlisted PCB skill references with byte sizes and SHA-256 hashes; no filesystem scan is needed."""
    root=_reference_root();items=[]
    for path in sorted(root.glob('*.md')):
        if path.is_symlink() or path.resolve().parent!=root:continue
        items.append({'name':path.name,'bytes':path.stat().st_size,'sha256':v.file_hash(path)})
    return {'root':str(root),'files':items}


@_tool
def read_reference(name: str, start_line: int = 1, max_lines: int = 180) -> dict:
    """Read a bounded allowlisted PCB skill reference; use this instead of recursive filesystem searches."""
    if isinstance(start_line,bool) or start_line<1:raise ValueError('start_line must be >= 1')
    if isinstance(max_lines,bool) or max_lines<1 or max_lines>400:raise ValueError('max_lines must be 1..400')
    path=_reference_path(name);lines=path.read_text().splitlines()
    start=start_line-1;selected=lines[start:start+max_lines]
    return {'name':path.name,'start_line':start_line,'end_line':start+len(selected),'total_lines':len(lines),
            'sha256':v.file_hash(path),'content':'\n'.join(selected)}


@_tool
def add_track(x1: Optional[float] = None, y1: Optional[float] = None, x2: Optional[float] = None, y2: Optional[float] = None, net_name: str = '', width_mm: Optional[float] = None, layer: str = 'F.Cu', points: Optional[list[list[float]]] = None) -> dict:
    """Add one continuous copper path in mm; supply points OR legacy x1/y1/x2/y2, plus net_name and width_mm. Exact duplicate segments are idempotent."""
    legacy=[x1,y1,x2,y2]
    if points is not None and any(x is not None for x in legacy):raise ValueError('Use points or endpoints, not both')
    if points is None:
        if any(x is None for x in legacy):raise ValueError('Supply points or all four endpoints')
        points=[[x1,y1],[x2,y2]]
    if not net_name or width_mm is None:raise ValueError('Explicit net_name and width_mm required')
    return _edit_board([dict(operation='track',points=points,net=net_name,width_mm=width_mm,layer=layer)])


@_tool
def add_arc(start: list[float], mid: list[float], end: list[float], net_name: str,
            width_mm: float, layer: str = 'F.Cu', expected_board_sha256: str = '') -> dict:
    """Add one native copper arc through three points; retain its UUID and rerun DRC."""
    if expected_board_sha256 and v.file_hash('board.kicad_pcb')!=expected_board_sha256:
        raise ValueError('STALE_BOARD: inspect the current board before adding an arc')
    return _edit_board([dict(operation='arc',start=start,mid=mid,end=end,net=net_name,
                              width_mm=width_mm,layer=layer)])


@_tool
def add_via(x: float, y: float, net_name: str, drill_mm: float, diameter_mm: float) -> dict:
    """Persist one through via, dimensions in mm; explicit diameter must exceed drill."""
    return _edit_board([dict(operation='via',at=[x,y],net=net_name,drill_mm=drill_mm,diameter_mm=diameter_mm)])


@_tool
def unroute(net_name: str = '') -> dict:
    """Remove tracks/vias for a named net, or all if empty. Archive board; copper zones remain."""
    board=ed.load('board.kicad_pcb')
    if net_name and net_name not in board.GetNetsByName():raise ValueError('Unknown PCB net: '+net_name)
    _backup(['board.kicad_pcb']);count=ed.unroute(board,net_name or None);ed.save(board)
    return {'removed':count,'verification':'REQUIRED'}


@_tool
def place_component(ref: str, x_mm: float, y_mm: float, rotation: Optional[float] = None, side: Optional[Literal['top','bottom']] = None) -> dict:
    """Set one component pose in mm/degrees; omitted rotation/side are preserved. Updates intent and existing PCB, retaining copper."""
    original=_spec();part=design._require(original,ref)
    board=ed.load('board.kicad_pcb') if Path('board.kicad_pcb').exists() else None
    native=next((fp for fp in board.GetFootprints() if fp.GetReference()==ref),None) if board else None
    if native is not None:
        operations=[]
        if side is not None:
            if rotation is None:
                rotation=native.GetOrientationDegrees()
            operations.append(dict(operation='side',ref=ref,side=side))
        move=dict(operation='move',ref=ref,at=[x_mm,y_mm])
        if rotation is not None:move['rotation']=rotation
        operations.append(move)
        result=_edit_board(operations)
    else:
        result=design.save_spec(design.place_component(original,ref,x_mm,y_mm,
            part.get('rot',0) if rotation is None else rotation,part.get('side','top') if side is None else side),'spec.json')
    return dict(result,affected_nets=sorted(set(part.get('nets',{}).values())),verification='REQUIRED')


@_tool
def lock_component(ref: str, locked: bool = True) -> dict:
    """Lock or unlock one component in intent and the existing PCB."""
    if Path('board.kicad_pcb').exists():
        board=ed.load('board.kicad_pcb')
        if any(fp.GetReference()==ref for fp in board.GetFootprints()):return _edit_board([dict(operation='lock',ref=ref,locked=locked)])
    return _edit('lock_component',ref=ref,locked=locked)


@_tool
def remove_board_item(item_id: str, expected_board_sha256: Optional[str] = None) -> dict:
    """Remove one track, via, zone, keepout or annotation by UUID; use set_board_outline for Edge.Cuts."""
    if expected_board_sha256 is not None and v.file_hash('board.kicad_pcb')!=expected_board_sha256:
        raise ValueError('STALE_BOARD: rerun inspect_board or the failing check before editing this UUID')
    return _edit_board([dict(operation='remove',id=item_id)])


@_tool
def set_track_width(item_id: str, width_mm: float) -> dict:
    """Change one track's width in mm using its UUID; vias and zones are rejected."""
    return _edit_board([dict(operation='track_width',id=item_id,width_mm=width_mm)])


@_tool
def set_pad_zone_connection(item_id: str, connection: Literal['inherited','none','thermal','solid','thermal_through_hole'], expected_board_sha256: str) -> dict:
    """Change one placed copper pad's zone connection by UUID on the observed board revision; refill zones and rerun DRC and affected analyses afterward."""
    if v.file_hash('board.kicad_pcb')!=expected_board_sha256:raise ValueError('Stale board revision; inspect_board before editing')
    return _edit_board([dict(operation='pad_zone_connection',id=item_id,connection=connection)])


@_tool
def set_component_text(ref: str, field: Literal['reference','value'], x_mm: float, y_mm: float, size_mm: float, rotation: float = 0, visible: bool = True) -> dict:
    """Position one PCB reference/value field in absolute mm, with text height/angle/visibility; preserve component placement and copper."""
    return _edit_board([dict(operation='field',ref=ref,field=field,at=[x_mm,y_mm],size_mm=size_mm,rotation=rotation,visible=visible)])


@_tool
def set_board_outline(points: list[list[float]]) -> dict:
    """Set one closed straight polygon in mm in intent and existing PCB; replaces all Edge.Cuts including cutouts."""
    if Path('board.kicad_pcb').exists():return _edit_board([dict(operation='outline',points=points)])
    from tools import board_ops
    points=board_ops._outline_points(points)
    s=_spec();s['outline']=points;xs,ys=zip(*points);s['board'].update(w=max(xs)-min(xs),h=max(ys)-min(ys))
    return design.save_spec(s,'spec.json')


@_tool
def set_layer_count(count: int) -> dict:
    """Set an even copper layer count 2..32 in intent and PCB; internal copper blocks layer reduction."""
    if Path('board.kicad_pcb').exists():return _edit_board([dict(operation='layers',count=count)])
    s=_spec();s['board']['layers']=count;return design.save_spec(s,'spec.json')


@_tool
def add_zone(net_name: str, layer: str, points: list[list[float]], clearance_mm: float, thermal_gap_mm: float, thermal_spoke_mm: float) -> dict:
    """Add one copper zone polygon in mm; fill_zones and verification are required."""
    return _edit_board([dict(operation='zone',net=net_name,layer=layer,points=points,clearance_mm=clearance_mm,thermal_gap_mm=thermal_gap_mm,thermal_spoke_mm=thermal_spoke_mm)])


@_tool
def add_keepout(layer: str, points: list[list[float]]) -> dict:
    """Add one copper-layer rule area forbidding tracks, vias and pours inside a polygon in mm."""
    return _edit_board([dict(operation='keepout',layer=layer,points=points)])


@_tool
def add_text(text: str, x_mm: float, y_mm: float, layer: str = 'F.SilkS', size_mm: float = 1) -> dict:
    """Add one annotation in mm on a non-copper, non-Edge.Cuts layer."""
    return _edit_board([dict(operation='text',text=text,at=[x_mm,y_mm],layer=layer,size_mm=size_mm)])


@_tool
def fill_zones() -> dict:
    """Fill existing copper-zone definitions using native isolated KiCad backend; then rerun DRC."""
    return kc.fill_zones_file('board.kicad_pcb')


@_tool
def route_differential_pair(p_net: str, n_net: str, centerline: list[list[float]], width_mm: float, gap_mm: float, layer: str, terminals: dict) -> dict:
    """Route one pair along an explicit corridor. terminals={p:[ref:pad,ref:pad],n:[ref:pad,ref:pad]}; offset endpoints must match native pad centers."""
    from tools import highspeed
    result=highspeed.differential('board.kicad_pcb',Path('.pcb/routes')/uuid.uuid4().hex,p_net,n_net,centerline,width_mm,gap_mm,layer,terminals)
    if result['status']=='PASS':_backup(['board.kicad_pcb']);shutil.copy2(result['candidate'],'board.kicad_pcb')
    return result


@_tool
def tune_length(item_id: str, target_length_mm: float, pitch_mm: float, max_amplitude_mm: float, side: Literal['left','right'] = 'left') -> dict:
    """Tune an unbranched two-pad net by replacing one straight segment. Target is total copper centerline length; pitch/amplitude in mm. Final DRC/analysis required."""
    from tools import highspeed
    result=highspeed.tune('board.kicad_pcb',Path('.pcb/routes')/uuid.uuid4().hex,item_id,target_length_mm,pitch_mm,max_amplitude_mm,side)
    if result['status']=='PASS':_backup(['board.kicad_pcb']);shutil.copy2(result['candidate'],'board.kicad_pcb')
    return result


@_tool
def route_board(timeout_s: int = 120, max_passes: int = 5) -> dict:
    """Run real Freerouting in a new attempt; adopt only a DRC-passing candidate and preserve source board history."""
    context=sources();attempt=Path('.pcb/routes')/uuid.uuid4().hex
    r=routing.route_board('board.kicad_pcb',attempt,timeout_s,max_passes)
    if r.get('candidate') and Path(r['candidate']).is_file():
        r.update(candidate_id=str(attempt/'board.kicad_pcb'),candidate_sha256=v.file_hash(r['candidate']))
        _write(attempt/'repair-context.json',dict(sources=context,candidate_sha256=r['candidate_sha256']))
    if r['status']=='PASS':
        _backup(['board.kicad_pcb']);shutil.copy2(r['candidate'],'board.kicad_pcb')
    return _record('routing',r)


@_tool
def checkout_board(candidate: str, expected_board_sha256: str, expected_candidate_sha256: str) -> dict:
    """Continue a retained route/history PCB as an unverified working version; exact hashes and unchanged non-board inputs required. Returns a rollback candidate."""
    import re
    root=Path.cwd().resolve();path=root/candidate
    if not re.fullmatch(r'\.pcb/(?:routes|history)/[0-9a-f]{32}/board\.kicad_pcb',candidate):
        raise ValueError('Select a retained route candidate or rollback_candidate returned by this tool')
    if path.is_symlink() or path.resolve()!=path:raise ValueError('Candidate cannot traverse symlinks')
    if v.file_hash('board.kicad_pcb')!=expected_board_sha256:raise ValueError('STALE_BOARD: inspect the current board first')
    if not path.is_file() or v.file_hash(path)!=expected_candidate_sha256:raise ValueError('Candidate hash mismatch')
    context=json.loads((path.parent/'repair-context.json').read_text())
    if context['candidate_sha256']!=expected_candidate_sha256:raise ValueError('Candidate differs from its retained repair context')
    now=sources()
    if {k:h for k,h in now.items() if k!='board.kicad_pcb'}!={k:h for k,h in context['sources'].items() if k!='board.kicad_pcb'}:
        raise ValueError('Candidate input dependencies changed; reroute/update the current circuit before checkout')
    # Only a copper/layout branch of the same physical circuit is eligible.
    if routing._snapshot(path)!=routing._snapshot('board.kicad_pcb'):
        raise ValueError('Candidate changes component placement, values, footprints or pad connectivity')
    backup=_backup(['board.kicad_pcb'])
    _write(backup/'repair-context.json',dict(sources=now,candidate_sha256=expected_board_sha256))
    staged=Path('.pcb')/('checkout-'+uuid.uuid4().hex+'.kicad_pcb')
    shutil.copy2(path,staged);staged.replace('board.kicad_pcb')
    result=dict(status='MODIFIED',verification='REQUIRED',candidate=candidate,
        previous_board_sha256=expected_board_sha256,board_sha256=v.file_hash('board.kicad_pcb'),
        rollback_candidate=str(backup/'board.kicad_pcb'),rollback_sha256=expected_board_sha256,
        next_action='Run DRC on this working version, locate its UUIDs, repair locally, then reverify')
    _write(Path('.pcb/branches')/(uuid.uuid4().hex+'.json'),result)
    return result


@_tool
def run_drc() -> dict:
    """Fresh native DRC including unconnected items and schematic parity; preserve full diagnostics."""
    out=Path('.pcb/reports')/uuid.uuid4().hex;out.mkdir(parents=True)
    contract=task_contract.load();source=Path('board.kicad_pcb')
    if contract is not None:
        from tools.project_rules import validation_copy
        source=validation_copy(Path.cwd(),out/'public-rules',_verification_spec(),custom_rules=contract.get('custom_rules'))/source.name
    return _record('drc',v.run_drc(source,out/'drc.json'))


@_tool
def run_erc() -> dict:
    """Run native schematic ERC and retain diagnostics; ERC alone does not verify function."""
    out=Path('.pcb/reports')/uuid.uuid4().hex;out.mkdir(parents=True)
    contract=task_contract.load();source=Path('board.kicad_sch')
    if contract is not None:
        from tools.project_rules import validation_copy
        source=validation_copy(Path.cwd(),out/'public-rules',_verification_spec(),custom_rules=contract.get('custom_rules'))/source.name
    return _record('erc',v.run_erc(source,out/'erc.json'))


@_tool
def export_netlist() -> dict:
    """Export the actual schematic connectivity to a new XML report and parse it."""
    out=Path('.pcb/reports')/uuid.uuid4().hex;out.mkdir(parents=True)
    return _record('netlist',dict(v.export_netlist('board.kicad_sch',out/'netlist.xml'),netlist_path=str(out/'netlist.xml')))


@_tool
def compare_schematic_pcb() -> dict:
    """Compare declared component/pin/pad/net membership. Does not detect physical copper shorts or unrouted connections; run DRC for those."""
    return _record('parity',v.compare_schematic_pcb('board.kicad_sch','board.kicad_pcb',Path('.pcb/reports')/uuid.uuid4().hex,_spec()))


@_tool
def check_component_datasheet(ref: str) -> dict:
    """Compare one component against sourced exact-MPN pin, footprint and rating evidence."""
    return _record('datasheet-'+ref,v.check_component_datasheet(_spec(),ref))


@_tool
def check_power_tree() -> dict:
    """Check declared rail voltage, DC load budget and linear-regulator headroom."""
    return _record('power_tree',v.check_power_tree(_verification_spec()))


@_tool
def check_interface() -> dict:
    """Check declared interface mapping, voltage intervals and supporting parts."""
    return _record('interfaces',v.check_interface(_verification_spec()))


@_tool
def check_footprints() -> dict:
    """Load actual footprints and check symbol-pin to pad coverage."""
    return _record('footprints',v.check_footprints(_spec()))


@_tool
def add_sheet(path: str) -> dict:
    """Add one child schematic sheet after its parent exists; root / is implicit."""
    s=_spec()
    if path in s.setdefault('sheets',{}):raise ValueError('Sheet already exists')
    s['sheets'][path]={'ports':{}};return design.save_spec(s,'spec.json')


@_tool
def remove_sheet(path: str) -> dict:
    """Remove one empty leaf sheet; first move its components and remove scoped nets."""
    s=_spec()
    if path not in s.get('sheets',{}):raise ValueError('Unknown sheet')
    if any(p.get('sheet','/')==path for p in s['parts']):raise ValueError('Sheet contains components')
    del s['sheets'][path];return design.save_spec(s,'spec.json')


@_tool
def assign_sheet(ref: str, path: str) -> dict:
    """Move one component's schematic units to a declared sheet; PCB placement is separate."""
    s=_spec();design._require(s,ref)['sheet']=path;return design.save_spec(s,'spec.json')


@_tool
def set_sheet_port(path: str, name: str, net: str, direction: Literal['input','output','bidirectional'] = 'bidirectional') -> dict:
    """Define one explicit parent/child hierarchical port for a declared global intent net."""
    s=_spec();s['sheets'][path].setdefault('ports',{})[name]={'net':net,'direction':direction}
    return design.save_spec(s,'spec.json')


@_tool
def verify_design() -> dict:
    """Fresh final ERC/DRC, exact parity, electrical facts and declared postroute checks; scoped UNKNOWN is not global PASS."""
    if task_contract.load() is not None:
        return _record('final',task_contract.assess(Path.cwd(),Path('.pcb/reports')/uuid.uuid4().hex))
    return _record('final',verify.verify('board.kicad_pcb','board.kicad_sch',spec=_spec(),out_dir=Path('.pcb/reports')/uuid.uuid4().hex))


@_tool
def export_project(destination: str, include_3d: bool = False) -> dict:
    """Export a fresh verified CAD/Gerber/drill/BOM/placement bundle to a new project directory; file export and physical production readiness remain separate."""
    from tools import release
    root=Path.cwd().resolve();target=(root/destination).resolve()
    if not target.is_relative_to(root) or target==root:raise ValueError('Export destination must be a new directory inside the project')
    return release.export_bundle('board.kicad_pcb','board.kicad_sch',_verification_spec(),target,include_3d=include_3d,task_document=task_contract.load())


@_tool
def save_project_copy(destination: str, project_name: str) -> dict:
    """Copy native CAD and portable dependencies under a requested project name; permits unfinished candidates, never asserts verification or manufacturing readiness."""
    from tools import project_copy
    return project_copy.save(destination, project_name)


@_tool
def prepare_3d_model(ref: str, sources: Optional[dict] = None) -> dict:
    """Acquire one footprint's assigned 3D bodies with provenance; standard KiCad paths use pinned upstream, custom sources need hashes. No dimensional qualification is inferred."""
    from tools import mechanical
    return mechanical.prepare('board.kicad_pcb',ref,sources)


@_tool
def export_3d(destination: str, format: Literal['step','glb','png'] = 'glb', view: Literal['top','bottom','isometric'] = 'isometric', width_px: int = 1600, height_px: int = 1000) -> dict:
    """Export one native KiCad assembly file or PNG view. View/size apply only to PNG; 64–4096 pixels. Missing bodies remain UNKNOWN; clearance/collision requires mechanical analysis."""
    from tools import mechanical
    root=Path.cwd().resolve();target=(root/destination).resolve()
    if not target.is_relative_to(root) or target==root:raise ValueError('3D destination must be a new file inside the project')
    return mechanical.export('board.kicad_pcb',target,format,view=view,width_px=width_px,height_px=height_px)


@_tool
def finalize_claims(cad_status: Literal['PASS','FAIL','UNKNOWN'], electrical_status: Literal['PASS','FAIL','UNKNOWN'], completed: bool, remaining_issues: list[str], scope: Literal['engineering','cad_prototype'] = 'engineering', test_claims: Optional[list[dict]] = None) -> dict:
    """Submit scoped statuses and optional test_id/conditions_sha256 citations to fresh passing tests. Free-text engineering statements remain unverified; fixed requirements are authoritative."""
    from tools import claims as cl
    requested=dict(cad_status=cad_status,electrical_status=electrical_status,completed=completed,remaining_issues=remaining_issues)
    if test_claims is not None:requested['test_claims']=test_claims
    try:
        verification=verify_design()
    except Exception as e:
        verification=v.result('UNKNOWN','final verifier',[f'{type(e).__name__}: {e}'])
    observed=cl.evidence(verification)
    contract=task_contract.load()
    authorized='cad_prototype' if contract and contract['scope']=='cad_prototype' else 'engineering'
    if contract and scope!=authorized:
        raise ValueError('Claims scope is fixed by public requirements: '+authorized)
    checked=cl.assess(requested,observed,scope)
    attempt=Path('.pcb/claim-attempts')/(uuid.uuid4().hex+'.json')
    _write(attempt,{'submitted':requested,'scope':scope,'assessment':checked,'report_path':verification.get('report_path')})
    if checked['status']=='PASS':
        _write('claims.json',requested)
    return dict(checked,submitted=requested,saved=checked['status']=='PASS',attempt_path=str(attempt),
                report_path=verification.get('report_path'),next_action='Report exact evidence scope' if checked['status']=='PASS' else 'Correct the submitted claim or supply missing evidence; the claim was not rewritten')


@_tool
def search_components(query: str, limit: int = 10, pins: Optional[int] = None, pads: Optional[int] = None) -> dict:
    """Search installed symbols and footprints; name hits are not datasheet qualification."""
    from tools import datasheet_search as ds
    if not 1 <= limit <= 100 or any(n is not None and n < 0 for n in (pins,pads)):raise ValueError('limit 1..100; nonnegative pin/pad counts')
    return {'symbols':ds.find_symbol(query,pins=pins,limit=limit),'footprints':ds.find_footprint(query,pads=pads,limit=limit)}


@_tool
def inspect_symbol(symbol: str) -> dict:
    """Resolve actual library pin declarations for a fully qualified symbol; no inferred MPN ratings."""
    return knowledge.symbol_facts(symbol)

def _type_schema(annotation):
    origin=get_origin(annotation);args=get_args(annotation)
    # ``types.UnionType`` was added in Python 3.10.  The tool schema is also
    # used by KiCad's bundled Python 3.9, where ``from __future__ import
    # annotations`` can still resolve nullable annotations to typing.Union.
    union_origins=(Union,)
    native_union=getattr(types,'UnionType',None)
    if native_union is not None: union_origins+=(native_union,)
    if origin in union_origins:
        choices=[_type_schema(t) for t in args if t is not type(None)]
        if len(choices)!=1:raise TypeError('Only nullable unions are supported')
        item=choices[0]
        if type(None) in args:
            item['type']=[item['type'],'null']
            if 'enum' in item:item['enum'].append(None)
        return item
    if origin is Literal:return {'type':'string','enum':list(args)}
    if origin is list:return {'type':'array','items':_type_schema(args[0])}
    if annotation in (bool,int,float,str,dict):
        return {'type':{bool:'boolean',int:'integer',float:'number',str:'string',dict:'object'}[annotation]}
    raise TypeError('Missing structured type: '+str(annotation))


def _schema(fn):
    props={};required=[]
    hints=get_type_hints(fn)
    for name,p in inspect.signature(fn).parameters.items():
        item=_type_schema(hints[name])
        if p.default is not p.empty:item['default']=p.default
        else:required.append(name)
        props[name]=item
    if fn.__name__ in ('project_status','inspect_board','inspect_schematic'):
        props['offset']['minimum']=0
        props['limit'].update(minimum=1,maximum=50)
    if fn.__name__=='inspect_board':
        props['bounds_mm'].update(minItems=2,maxItems=2,items={'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2})
        props['bounds_mm']['description']='Native board-space envelope [[xmin,ymin],[xmax,ymax]] in mm; intersection does not prove copper contact.'
        props['expected_board_sha256']['description']='Use the hash returned by the first page; required when offset > 0.'
        props['collection']['description']='summary is a broad overview; other collections return complete pages in items. parts omits child pads: query pads with ref.'
    if 'points' in props:
        props['points'].update(minItems=2 if fn.__name__ in ('add_track','set_schematic_wire') else 3,items={'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2})
        if fn.__name__=='set_schematic_wire':props['points']['maxItems']=2
    if fn.__name__=='new_project':
        props['width_mm']['exclusiveMinimum']=0
        props['height_mm']['exclusiveMinimum']=0
        props['layers']['enum']=list(range(2,33,2))
    if fn.__name__=='add_track':
        # Python defaults retain the legacy positional endpoint interface;
        # neither net nor width has an executable default for a copper path.
        required.extend(('net_name','width_mm'))
        props['net_name']={'type':'string','minLength':1,
                           'description':'Existing candidate net name; required for both path forms.'}
        props['width_mm']={'type':'number','exclusiveMinimum':0,
                           'description':'Explicit positive track width in mm, chosen under the public design rules.'}
        for coordinate in ('x1','y1','x2','y2'):
            props[coordinate]={'type':'number',
                'description':'Legacy endpoint coordinate in mm. Omit when using points; otherwise supply all four endpoint coordinates as JSON numbers.'}
    if fn.__name__=='set_net_rule':
        props['rules']={'type':'object','properties':{k:{'type':'number','exclusiveMinimum':0} for k in ('track_mm','clearance_mm','via_mm','drill_mm','dp_width_mm','dp_gap_mm')},'additionalProperties':False}
    if fn.__name__=='configure_component':
        props['properties']={'type':'object','properties':{'symbol':{'type':'string'},'mpn':{'type':'string'},'operating':{'type':'object'},'required_operating_checks':{'type':'array','items':{'type':'string'}},'datasheet_facts':{'type':'object'},'mechanical_pads':{'type':'array','items':{'type':'string'}}},'additionalProperties':False}
    if fn.__name__=='finalize_claims':
        props['test_claims']={'type':['array','null'],'default':None,'items':{'type':'object',
            'properties':{'test_id':{'type':'string'},'conditions_sha256':{'type':'string'}},
            'required':['test_id','conditions_sha256'],'additionalProperties':False}}
    if fn.__name__=='set_requirements':
        props['section']['enum']=['constraints','postroute','acquisition','analysis','power_tree','interfaces']
        props['value']['description']='analysis: inspect_tool(analysis). power_tree/interfaces: {items:[...]}; inspect_tool(power_tree) or inspect_tool(interfaces) gives nested fields and units. constraints: {board_rules:{clearance_mm,track_mm,via_mm,drill_mm,edge_mm,hole_mm}} in positive mm. postroute: {nets:{NET:{max_copper_length_mm,max_vias}},placements:[{ref,x_mm,y_mm,rotation_deg,side}],placement_regions:[{ref,x_fraction:[min,max],y_fraction:[min,max],side}],zones:[{net,layer}]}; region fractions refer to the native outline bounding box, from 0 to 1. {} selects no additional postroute constraints. See skill references for acquisition/fact schemas.'
    # Optional containers use omission for the None default. A single explicit
    # container type is interoperable with providers whose tool parser serializes
    # nullable unions as strings. Never decode/coerce those strings at dispatch.
    # Scalar nulls (e.g. preserving a pose) keep their existing semantics.
    for name,item in props.items():
        if name not in required and item.get('default','missing') is None and item.get('type') in (['array','null'],['object','null']):
            item['type']=item['type'][0];item.pop('default')
            item['description']=(item.get('description','')+' Optional: omit this argument to use the default; supply a native JSON '+item['type']+' when present.').strip()
    return {'type':'object','properties':props,'required':required,'additionalProperties':False}


def definitions(groups=None):
    """Canonical interface, optionally filtered by capability group.

    With no selection this is exactly the historical full registry.  A
    filtered registry carries its group metadata so a model can understand
    why a tool is available without seeing implementation details.
    """
    selected=parse_tool_groups(groups)
    if selected is None:
        names=list(PUBLIC)
    else:
        names=[name for name in PUBLIC if any(name in TOOL_GROUPS[group] for group in selected)]
    return [{'type':'function','function':{'name':name,
             'description':inspect.getdoc(PUBLIC[name]).split('\n')[0],
             'parameters':_schema(PUBLIC[name])}}
            for name in names]


def dispatch(name,args):
    if name not in PUBLIC:
        error=InputError('UNKNOWN_TOOL','name',name,{'source':'public tool registry'},'Select a name from the advertised tool definitions.')
        error.operation_executed=False
        raise error
    _validate_arguments(args,{'type':'object'})
    return PUBLIC[name](**args)


if __name__=='__main__':
    import sys
    data={}
    try:
        data=decode_arguments(sys.stdin.read())
        _validate_arguments(data,{'type':'object','properties':{'name':{'type':'string'},'arguments':{'type':'object'}},'required':['name','arguments'],'additionalProperties':False})
        print(json.dumps(dispatch(data['name'],data['arguments']),ensure_ascii=False,default=str))
    except Exception as e:
        print(json.dumps(error_observation(e,data.get('name')),ensure_ascii=False))
        raise SystemExit(1)
