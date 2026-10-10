"""Incremental schematic-to-PCB updates preserving unaffected native objects."""
from pathlib import Path
import copy,shutil
from tools import pcb_editor as ed,validation as v,library,design


def _members(board):
    result={}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname():result.setdefault(pad.GetNetname(),set()).add(fp.GetReference()+':'+pad.GetNumber())
    return result


def update(board,schematic,spec,out_dir,prune_affected=False):
    """Build a parity-checked candidate; source files stay intact until caller adoption."""
    design.validate(spec);source=Path(board).resolve();sch=Path(schematic).resolve();out=Path(out_dir).resolve()
    if out.exists():raise FileExistsError(out)
    out.mkdir(parents=True)
    before=v.file_hash(source);exported=v.export_netlist(sch,out/'schematic.xml')
    if exported['status']!='PASS':return exported
    expected=v.compare_spec_netlist(spec,exported['data'])
    if expected['status']!='PASS':return expected
    b=ed.load(source);old={fp.GetReference():fp for fp in b.GetFootprints()}
    target={p['ref']:copy.deepcopy(p) for p in spec['parts'] if not p.get('schematic_only')}
    for ref,part in target.items():
        for pin in part.get('no_connect',[]):
            key=ref+':'+str(part.get('pad_map',{}).get(pin,pin))
            net=exported['data']['pins'].get(key)
            if net and net.startswith('unconnected-'):part.setdefault('nets',{})[pin]=net
    old_members=_members(b);new_members={}
    for ref,p in target.items():
        for pin,net in p.get('nets',{}).items():new_members.setdefault(net,set()).add(ref+':'+str(p.get('pad_map',{}).get(pin,pin)))
    renames={}
    for name,members in old_members.items():
        if name not in new_members:
            candidates=[n for n,pins in new_members.items() if n not in old_members and pins==members]
            if len(candidates)==1:renames[name]=candidates[0]
    affected={name for name,members in old_members.items() if members!=new_members.get(renames.get(name,name),set())}
    replaced=[];added=[];removed=[];updated=copy.deepcopy(spec)
    ed.ensure_nets(b,sorted(new_members))
    nets=b.GetNetsByName()
    for ref,fp in list(old.items()):
        if ref not in target:
            removed.append(ref);b.Delete(fp)
        elif fp.GetFPIDAsString()!=target[ref]['footprint']:
            replacement=copy.deepcopy(target[ref])
            replacement.update(at=[ed.to_mm(fp.GetPosition().x),ed.to_mm(fp.GetPosition().y)],rot=fp.GetOrientationDegrees(),side='bottom' if fp.IsFlipped() else 'top',locked=fp.IsLocked())
            affected.update(pad.GetNetname() for pad in fp.Pads() if pad.GetNetname())
            # Replaced footprints get native new IDs; unchanged objects retain theirs.
            b.Delete(fp)
            new_fp=ed.add_part(b,replacement);old[ref]=new_fp;replaced.append(ref)
    for ref,part in target.items():
        if ref not in old:
            if part.get('at') is None:raise ValueError('Place the new component in intent before ECO: '+ref)
            old[ref]=ed.add_part(b,part);added.append(ref)
        fp=old[ref];fp.SetValue(str(part.get('value','')));ed.sync_schematic_attributes(fp)
        mapping={str(part.get('pad_map',{}).get(pin,pin)):net for pin,net in part.get('nets',{}).items()}
        for pad in fp.Pads():
            if pad.GetNumber() in mapping:pad.SetNet(nets[mapping[pad.GetNumber()]])
            else:pad.SetNetCode(0)
        saved=next(p for p in updated['parts'] if p['ref']==ref)
        saved.update(at=[ed.to_mm(fp.GetPosition().x),ed.to_mm(fp.GetPosition().y)],rot=fp.GetOrientationDegrees(),side='bottom' if fp.IsFlipped() else 'top',locked=fp.IsLocked())
    dropped=[];retained=[]
    for item in list(b.GetTracks()):
        original=item.GetNetname();name=renames.get(original,original)
        if name not in new_members or (prune_affected and original in affected):
            dropped.append(item.m_Uuid.AsString());b.Delete(item)
        else:
            if name!=original:item.SetNet(nets[name])
            retained.append(item.m_Uuid.AsString())
    zone_changes=[]
    for zone in list(b.Zones()):
        if zone.GetIsRuleArea():continue
        name=renames.get(zone.GetNetname(),zone.GetNetname())
        if name not in new_members:b.Delete(zone);zone_changes.append('removed orphan zone')
        else:
            zone.SetNet(nets[name])
            if added or removed or replaced or affected:zone.UnFill();zone_changes.append('refill required')
    for ext in ('.kicad_pro','.kicad_dru'):
        if source.with_suffix(ext).is_file():shutil.copy2(source.with_suffix(ext),out/source.with_suffix(ext).name)
    library.copy_project_libraries(source.parent,out)
    candidate=out/source.name;ed.save(b,candidate)
    from tools.project_rules import apply
    apply(candidate,updated)
    parity=v.compare_schematic_pcb(sch,candidate,out/'parity',updated)
    report=v.aggregate({'parity':parity,'revision':v.result('PASS' if v.file_hash(source)==before else 'UNKNOWN','ECO source unchanged')})
    report.update(candidate=str(candidate),spec=updated,added=added,removed=removed,replaced=replaced,renamed_nets=renames,
                  affected_nets=sorted(affected),retained_copper_ids=retained,removed_copper_ids=dropped,zones=zone_changes,
                  source_sha256=before,verification='ECO parity only; fresh DRC/electrical checks required')
    return report
