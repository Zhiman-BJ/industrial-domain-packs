"""Compact, evidence-linked feedback over shared checks; never changes a verdict."""
from collections import Counter, deque


def check_summary(report, report_path=None):
    """Index public check statuses and their own causes at exact JSON pointers.

    This reads only the already-public report; it neither re-evaluates checks nor
    consults private task data. Bulky native netlists/logs never enter the index.
    """
    rows=[];pending=deque([(report, '')])
    while pending:
        node,path=pending.popleft()
        if not isinstance(node,dict):continue
        if isinstance(node.get('status'),str):
            row={'path':path, 'status':node['status']}
            issues=node.get('issues') or []
            if not isinstance(issues,list):issues=[issues]
            if issues:
                first=issues[0]
                text=(first.get('description') or first.get('message') or first.get('type') or str(first)) if isinstance(first,dict) else str(first)
                row.update(reason=text[:280],issues_total=len(issues))
                if len(text)>280:row['reason_truncated']=True
            children=node.get('checks',{})
            if isinstance(children,dict):
                counts=Counter(c['status'] if isinstance(c.get('status'),str) else 'UNKNOWN'
                               for c in children.values() if isinstance(c,dict))
                if counts:row['child_statuses']=dict(counts)
            rows.append(row)
        # Compiled acceptance nests checks under cad/coverage; raw native
        # verification uses checks. Follow actual status-bearing dictionaries
        # in either schema without inventing a synthetic /checks wrapper.
        for name,child in node.items():
            if name in ('report','feedback','data','sources','artifacts','measurements'):continue
            if name=='verification_layers' and ('checks' in node or 'cad' in node):continue
            if isinstance(child,dict) and (isinstance(child.get('status'),str) or 'checks' in child
                    or name in ('checks','electrical_evidence','acceptance_tiers','verification_layers','layers')):
                token=str(name).replace('~','~0').replace('/','~1')
                pending.append((child,path+'/'+token))
    return {'checks':rows,'total_checks':len(rows),'returned_checks':len(rows),'omitted_checks':0,
            'full_report_path':str(report_path) if report_path else None,
            'read_hint':'Paths are JSON pointers in the raw report, not in the observation envelope. Reasons belong only to their named check; inspect child paths for aggregate causes.'}


def refresh_omissions(original, shown):
    """Recount public finding pages after *every* display/byte reduction."""
    if not isinstance(original,dict) or not isinstance(shown,dict):return
    if isinstance(original.get('findings'),list) and isinstance(shown.get('findings'),list):
        omitted=original.get('omitted_findings',0)
        total=len(original['findings'])+(max(0,omitted) if type(omitted) is int else 0)
        shown.update(total_findings=total,returned_findings=len(shown['findings']),
                     omitted_findings=max(0,total-len(shown['findings'])))
        if isinstance(shown.get('omitted_fields'),dict) and 'findings' in shown['omitted_fields']:
            shown['omitted_fields']['findings']=len(original['findings'])-len(shown['findings'])
    for key,value in shown.items():
        if isinstance(value,dict):refresh_omissions(original.get(key),value)


def test_results(report):
    """Small, factual test receipts, independent of aggregate qualification."""
    analyses=report if 'categories' in report else report.get('analyses') or report.get('checks',{}).get('analyses') or {}
    return [dict(test_id=ident,category=category,**{key:item[key] for key in (
                'status','conditions_sha256','board_sha256','schematic_sha256',
                'simulation_source','pcb_copper_extracted','scope','measurements') if key in item})
            for category,group in analyses.get('categories',{}).items() if isinstance(group,dict)
            for ident,item in group.get('checks',{}).items() if isinstance(item,dict)]


def summarize(report, stage='', report_path=None, limit=12):
    findings = []
    evidence_keys = ('source_sha256', 'board_sha256', 'schematic_sha256', 'netlist_sha256', 'source_path')

    def visit(node, path):
        if not isinstance(node, dict):
            return
        children = node.get('checks', {})
        if isinstance(children, dict):
            for name, child in children.items():
                visit(child, path + [name])
        status = node.get('status')
        issues = node.get('issues') or []
        if not isinstance(issues, list):
            issues = [issues]
        if status not in ('FAIL', 'UNKNOWN'):
            issues=[]
        # Native warnings need actionable feedback even when zero errors gives
        # this check PASS. Preserve severity; do not silently promote to FAIL.
        warnings=node.get('warnings',[])
        if not warnings and isinstance(node.get('report'),dict):
            warnings=[x for x in node['report'].get('violations',[]) if x.get('severity')=='warning']
        issues=list(issues)+[x for x in warnings if x not in issues]
        if status not in ('FAIL','UNKNOWN') and not issues:return
        if not issues and not children:
            issues = [node.get('scope', 'Required check has no usable evidence')]
        native = node.get('report') or {}
        if not isinstance(native, dict):
            native = {}
        for issue in issues:
            detail = issue if isinstance(issue, dict) else {}
            message = detail.get('description') or (str(issue) if not detail else str(detail.get('type', 'Check failed')))
            evidence = {key: node[key] for key in evidence_keys if key in node}
            if report.get('candidate_sha256'):
                evidence.update(board_sha256=report['candidate_sha256'],candidate=report.get('candidate_id') or report.get('candidate'))
            if native.get('report_path'):
                evidence['report_path'] = native['report_path']
            elif report_path:
                evidence['report_path'] = str(report_path)
            item_status='WARNING' if detail.get('severity')=='warning' else status
            item = {'check': '.'.join(path) or stage, 'status': item_status,
                    'code': detail.get('type') or node.get('scope', 'check'), 'message': message,
                    'objects': detail.get('items', []), 'evidence': evidence}
            if node.get('measurements'):
                item['measurements']={key:value for key,value in node['measurements'].items()
                                      if value.get('status')!='PASS'}
            if status == 'UNKNOWN' and (native.get('report_errors') or native.get('available') is False):
                item['next_action'] = 'Inspect the saved native CLI report/error, restore a complete check, and rerun this stage; no valid verdict was produced.'
            for key in ('sheet_path', 'sheet_uuid_path'):
                if key in detail:
                    item[key] = detail[key]
            findings.append(item)

    visit(report, [stage] if stage else [])
    layers = report.get('verification_layers') or {}
    if layers.get('engineering_coverage'):
        visit(layers['engineering_coverage'], ['engineering_coverage'])
    # Avoid repeating identical messages from the same check.
    unique = {}
    for finding in findings:
        key = (finding['check'], finding['status'], finding['message'], str(finding['objects']))
        unique.setdefault(key, finding)
    # Keep the first actionable cause from every check visible before showing
    # repeated leaf evidence from one large check (for example, many missing
    # component facts).  A flat severity sort can hide an unrelated postroute
    # or task-coverage blocker behind the first page of that repeated list.
    findings = list(unique.values())
    def bucket(item):
        parts=item['check'].split('.')
        ignored={stage, 'checks', 'candidate_diagnostics', 'verification_layers', 'engineering_coverage'}
        return next((part for part in parts if part and part not in ignored), parts[-1] if parts else '')
    grouped={}
    order=[]
    for item in findings:
        key=(item['status'],bucket(item))
        if key not in grouped: grouped[key]=[];order.append(key)
        grouped[key].append(item)
    findings=[]
    for status in ('FAIL','UNKNOWN','WARNING'):
        keys=[key for key in order if key[0]==status]
        # Round-robin buckets so one repeated category cannot consume the
        # complete public page.  Preserve the verifier's original order inside
        # each bucket and therefore keep UUID/object evidence deterministic.
        remaining=True;index=0
        while remaining:
            remaining=False
            for key in keys:
                values=grouped[key]
                if index<len(values):
                    findings.append(values[index]);remaining=True
            index+=1
    advice = {
        'drc': 'Inspect the listed copper/pad objects and positions; rerun run_drc after the edit.',
        'erc': 'Inspect the listed pin/object UUIDs, sheet and verified positions; make a local schematic correction and rerun run_erc and netlist parity. Regenerate only after an intentional circuit change.',
        'parity': 'Inspect actual pin/pad/net membership; update the affected CAD stage and rerun compare_schematic_pcb.',
        'intent': 'Compare the actual exported pins with the declared circuit intent; rerun verify_schematic.',
        'netlist': 'Inspect the netlist export error and source schematic; rerun export_netlist.',
        'footprints': 'Inspect the actual symbol pins and footprint pads; correct their mapping and rerun check_footprints.',
        'datasheets': 'Look up exact-MPN evidence; qualify missing facts/models before rerunning the component checks.',
        'power_tree': 'Inspect supply paths, declared voltage ranges and current budgets; rerun check_power_tree.',
        'interfaces': 'Inspect actual interface pins, voltage ranges and supporting components; rerun check_interface.',
        'analyses': 'Inspect the named model, operating conditions and failed measurement; rerun run_analysis.',
        'engineering_coverage': 'Supply the required analysis evidence or a justified applicable exclusion before claiming electrical PASS.',
        'requirements_schema': 'Report the invalid external acceptance condition; candidate edits cannot repair an invalid task contract.',
    }
    shown = findings[:limit]
    for finding in shown:
        tokens = finding['check'].split('.')
        key = next((name for name in reversed(tokens) if name in advice), None)
        finding.setdefault('next_action', advice.get(key, 'Inspect the saved check evidence, correct its underlying cause, and rerun the affected verification stage.'))
    return {'schema_version': 1, 'stage': stage, 'counts': dict(Counter(item['status'] for item in findings)),
            'warning_types':dict(Counter(item['code'] for item in findings if item['status']=='WARNING')),
            'findings': shown, 'omitted_findings': max(0, len(findings) - len(shown)),
            'full_report_path': str(report_path) if report_path else None,
            'scope': 'Feedback explains existing checks; original requirements and evidence scopes still determine acceptance.'}
