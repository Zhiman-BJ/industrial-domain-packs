"""Quarantined, evidence-backed component acquisition and atomic publication.

An agent supplies exact CAD identities and a source manifest after researching
the manufacturer. Downloads are not qualification: review, pin/pad checks and
actual KiCad loading gate publication. No fuzzy MPN substitution is performed.
"""
from __future__ import annotations
from pathlib import Path
import copy
import datetime
import hashlib
import json
import os
import shutil
import tempfile
from tools import library, schematic, validation as v
from tools.sexpr import dump, Quoted


def _name(value):
    if not isinstance(value, str) or not value or any(c in value for c in '/\\:') or value in ('.', '..'):
        raise ValueError('Expected a simple library/file name')
    return value


def _files(root):
    return {str(p.relative_to(root)): v.file_hash(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.name not in ('qualification.json', 'package.json')}


def prepare_component(request, destination):
    """Stage exact symbol, footprint, source PDFs and reviewed facts; never publish."""
    dest = Path(destination).resolve()
    if dest.exists():
        raise FileExistsError(dest)
    facts = copy.deepcopy(request['facts'])
    for key in ('mpn', 'symbol', 'footprint', 'pins', 'source', 'retrieved_at'):
        if not facts.get(key):
            raise ValueError('Missing facts field: ' + key)
    if not isinstance(facts['pins'],dict) or any(not isinstance(k,str) or not k or
            not isinstance(p,dict) or not {'name','type'}<=set(p) for k,p in facts['pins'].items()):
        raise ValueError('facts.pins must map pin-number strings to {name,type}; a pin array is not accepted')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.intake-', dir=dest.parent) as tmp:
        stage = Path(tmp) / 'package'; stage.mkdir()
        provenance = []
        for kind, ext in (('symbol', '.kicad_sym'), ('footprint', '.kicad_mod')):
            lib, name = facts[kind].split(':', 1); _name(lib); _name(name)
            target = stage / ('symbols' if kind == 'symbol' else 'footprints')
            target = target / (lib + ext) if kind == 'symbol' else target / (lib + '.pretty') / (name + ext)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = request.get('cad_sources', {}).get(kind)
            if source and 'path' in source:
                # Author-created CAD remains separate from manufacturer facts.
                from tools import authoring
                expected,_ = authoring._path(facts[kind],kind)
                original=Path(source['path']).resolve()
                if set(source)!={'path','sha256','license'} or original!=expected or not source['license']:
                    raise ValueError(f'cad_sources.{kind} must contain only path, sha256, license; '
                                     f'path must be {expected.relative_to(Path.cwd())}. '
                                     'Omit the entry to reuse installed CAD; system-library paths are not authored drafts.')
                if v.file_hash(original)!=source['sha256']:raise ValueError('Authored CAD changed')
                shutil.copy2(original,target)
                record={'origin':'authored_draft','source_url':'workspace:'+str(original.relative_to(Path.cwd())),
                        'sha256':source['sha256'],'license':source['license'],'validation':{'facts':'UNKNOWN'}}
            elif source:
                if set(source)-{'url','revision','license_url','expected_sha256','archive_member','archive_sha256'}:
                    raise ValueError(f'cad_sources.{kind}: use HTTPS url/revision/license_url, or authored path/sha256/license')
                record = library.fetch_artifact(destination=target, kind=kind, **source)
            else:
                dirs = library.symbol_dirs() if kind == 'symbol' else library.footprint_dirs()
                relative = Path(lib + ext) if kind == 'symbol' else Path(lib + '.pretty') / (name + ext)
                # Portable project caches contain extracted CAD, not upstream
                # license manifests. Resolve intake against a provenance-bearing
                # library instead of letting an unqualified cache shadow it.
                candidates=[(Path(d),Path(d)/relative) for d in dirs if (Path(d)/relative).is_file()]
                original=next((p for directory,p in candidates if
                    (directory/'library-manifest.json').is_file() or (directory.parent/'package.json').is_file()),None)
                if original is None:
                    raise FileNotFoundError(f'{facts[kind]} lacks installed CAD provenance; supply cad_sources.{kind} HTTPS source or an authored draft')
                root = next(Path(d) for d in dirs if (Path(d) / relative) == original)
                manifest = root / 'library-manifest.json'
                qualified=root.parent/'package.json'
                if manifest.is_file():
                    record=json.loads(manifest.read_text())
                    license_sources=[root/p for p in record['license_files']]
                elif qualified.is_file() and qualify_component(root.parent,persist=False)['status']=='PASS':
                    # Reuse individually qualified CAD without pretending it is
                    # a full upstream archive; retain its original source/license.
                    package=json.loads(qualified.read_text())
                    record=next(p['source'] for p in package['provenance'] if p['kind']==kind)
                    license_sources=[p for p in (root.parent/'licenses'/kind).rglob('*') if p.is_file()]
                else:
                    raise ValueError('Local CAD needs a provenance manifest, qualified package or explicit upstream source')
                if kind == 'symbol':
                    node = schematic.resolved_symbol(lib, name, [str(root)])
                    # Resolve inheritance so the installed component is self-contained.
                    target.write_text(dump(['kicad_symbol_lib', ['version', '20241209'],
                                            ['generator', Quoted('pcb-agent')], node]))
                else:
                    shutil.copy2(original, target)
                licenses = stage / 'licenses' / kind; licenses.mkdir(parents=True)
                for i, license_file in enumerate(license_sources):
                    shutil.copy2(license_file, licenses / (str(i) + '-' + license_file.name))
                record = dict(record, original_cad_sha256=v.file_hash(original))
            provenance.append(dict(kind=kind, path=str(target.relative_to(stage)), source=record))
        for evidence in request.get('documents', []):
            name = _name(evidence['name'])
            record = library.fetch_artifact(destination=stage / 'documents' / name, kind='datasheet', **evidence['download'])
            provenance.append(dict(kind='datasheet', path='documents/' + name, source=record))
        definitions={}
        for model in request.get('models',[]):
            name=_name(model['id']);filename=_name(model['name'])
            if name in definitions:raise ValueError('Duplicate model ID')
            record=library.fetch_artifact(destination=stage/'models'/filename,kind='spice_model',**model['download'])
            provenance.append(dict(kind='spice_model',path='models/'+filename,source=record))
            definition=copy.deepcopy(model['definition'])
            if any(set(unit)&{'file','source','sha256'} for unit in definition['binding'].get('instances',[])):
                raise ValueError('A model definition binds one acquired file; instances may select subcircuits/pins, not replace provenance')
            definition['binding'].update(file='models/'+filename,sha256=record['sha256'],source=record['source_url'])
            definitions[name]=definition
        if definitions:(stage/'models.json').write_text(json.dumps(definitions,indent=2))
        (stage / 'facts.json').write_text(json.dumps({'schema_version': 1, 'revision': facts['retrieved_at'],
                                                    'components': {facts['mpn']: facts}}, indent=2))
        package = {'schema_version': 1, 'mpn': facts['mpn'], 'provenance': provenance,
                   'files': _files(stage), 'state': 'QUARANTINED'}
        (stage / 'package.json').write_text(json.dumps(package, indent=2))
        os.replace(stage, dest)
    return v.result('PASS', 'acquisition only; not qualified', package=str(dest), mpn=facts['mpn'])


def check_evidence(facts, root):
    """Check retained document hashes and field-specific review provenance."""
    root = Path(root).resolve(); issues = []; missing = []
    review = facts.get('review', {})
    if not review.get('reviewer') or not review.get('reviewed_at'):
        missing.append('Manufacturer pinout/package review identity and date required')
    citations = facts.get('citations', {})
    for field in ('mpn', 'pins', 'footprint', *facts.get('ratings', {}).keys()):
        cite = citations.get(field)
        if not cite or not all(cite.get(k) for k in ('document', 'sha256', 'locator')):
            missing.append('Missing document/hash/page or section citation: ' + field); continue
        p = (root / cite['document']).resolve()
        if root not in p.parents:
            issues.append('Evidence path escapes package'); continue
        if not p.is_file():
            missing.append('Missing evidence document: ' + str(p)); continue
        if v.file_hash(p) != cite['sha256']:
            issues.append('Evidence document changed: ' + field)
    return v.result('FAIL' if issues else 'UNKNOWN' if missing else 'PASS',
                    'retained evidence and recorded review; not automatic PDF interpretation', issues + missing)


def qualify_component(package, persist=True):
    """Validate immutable artifacts, source review, resolved pins and real CAD loading."""
    root = Path(package).resolve(); manifest = json.loads((root / 'package.json').read_text())
    data = json.loads((root / 'facts.json').read_text()); facts = data['components'][manifest['mpn']]
    checks = {'integrity': v.result('PASS' if _files(root) == manifest['files'] else 'FAIL', 'package hashes'),
              'evidence': check_evidence(facts, root)}
    try:
        node = schematic.resolved_symbol(*facts['symbol'].split(':', 1), [str(root / 'symbols')])
        pins = {n: {'name': name, 'type': kind} for unit in schematic.symbol_units(node).values()
                for n, name, x, y, kind in unit}
        checks['pins'] = v.result('PASS' if pins == facts['pins'] else 'FAIL', 'reviewed pin facts ↔ CAD pin declarations')
        from tools import pcb_editor as ed
        ed._need(); lib, name = facts['footprint'].split(':', 1)
        fp = ed.pcbnew.FootprintLoad(str(root / 'footprints' / (lib + '.pretty')), name)
        if fp is None:
            raise ValueError('KiCad could not load footprint')
        pads = {str(p.GetNumber()) for p in fp.Pads() if str(p.GetNumber())}
        mapping = facts.get('pad_map', {})
        mapped = {str(mapping.get(p, p)) for p in pins}
        allowed = set(facts.get('mechanical_pads', []))
        checks['pads'] = v.result('PASS' if mapped <= pads and pads <= mapped | allowed and len(mapped) == len(pins) else 'FAIL',
                                 'all pins ↔ actual footprint pads')
        # Exercise the native schematic parser independently of our own parser.
        with tempfile.TemporaryDirectory(prefix='.runtime-') as tmp:
            old_dirs = schematic.SYMBOL_DIRS
            try:
                schematic.SYMBOL_DIRS = [str(root / 'symbols')]
                sch = schematic.Schematic('part')
                sch.add_part('U1', facts['symbol'], facts['footprint'], nets={p: 'PIN_' + p for p in pins})
                sch.write(Path(tmp) / 'part.kicad_sch')
                checks['native_symbol'] = v.export_netlist(Path(tmp) / 'part.kicad_sch', Path(tmp) / 'part.xml')
            finally:
                schematic.SYMBOL_DIRS = old_dirs
    except Exception as e:
        checks['runtime'] = v.result('UNKNOWN', 'KiCad qualification', [str(e)])
    if (root/'models.json').is_file():
        from tools import models
        checks['models']=models.qualify(root,facts,json.loads((root/'models.json').read_text()))
    report = v.aggregate(checks)
    report.update(files=_files(root), package_sha256=v.file_hash(root / 'package.json'),
                  checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    if persist:(root / 'qualification.json').write_text(json.dumps(report, indent=2))
    return report


def install_component(package, installed_root):
    """Requalify then atomically publish a new library root; never overwrite."""
    source = Path(package).resolve(); report = qualify_component(source)
    if report['status'] != 'PASS':
        return report
    digest = v.file_hash(source / 'package.json')
    parent = Path(installed_root).resolve(); parent.mkdir(parents=True, exist_ok=True)
    dest = parent / ('qualified-' + digest[:20])
    if dest.exists():
        if _files(dest) != report['files']:
            return v.result('FAIL', 'installed integrity', ['Existing component package was modified'])
        return v.result('PASS', 'already installed', library_root=str(dest))
    with tempfile.TemporaryDirectory(prefix='.publish-', dir=parent) as tmp:
        staged = Path(tmp) / 'package'; shutil.copytree(source, staged)
        if _files(staged) != report['files']:
            return v.result('FAIL', 'publication integrity', ['Package changed while copying'])
        os.replace(staged, dest)
    library.refresh()
    return v.result('PASS', 'qualified CAD plus source-reviewed facts published', library_root=str(dest), mpn=json.loads((dest/'package.json').read_text())['mpn'])


def ensure_component(mpn, catalog, workspace, installed_root):
    """Exact-MPN catalog → acquire → qualify → install; unresolved evidence stays UNKNOWN.

catalog is a JSON mapping of exact MPNs to acquisition requests (HTTPS sources,
facts and review citations). An agent can extend it after source research.
"""
    from tools import knowledge
    found = knowledge.component_facts(mpn)
    if found and found.get('_package_root'):
        return qualify_component(found['_package_root'],persist=False)
    entries = json.loads(Path(catalog).read_text())
    if mpn not in entries:
        return v.result('UNKNOWN', 'component discovery', ['Exact MPN absent from source catalog; research upstream sources and add an acquisition request'], mpn=mpn)
    request = entries[mpn]
    if request['facts']['mpn'] != mpn:
        return v.result('FAIL', 'component identity', ['Catalog MPN differs'])
    folder = Path(workspace) / hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:20]
    if not folder.exists():
        prepare_component(request, folder)
    return install_component(folder, installed_root)
