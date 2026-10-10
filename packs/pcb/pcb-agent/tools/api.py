"""Canonical model tools by default; library internals require explicit opt-in.

    python3 -m tools.api            # JSON manifest
    python3 -m tools.api --md       # Markdown table

An MCP server, a CLI, or an agent-CLI plugin can be generated from this
without hand-writing the list, and the manifest stays true to the code.
"""
from __future__ import annotations
import inspect, json, sys
from tools import design,validation,knowledge,library,intake,routing,postroute,release,model_runner,analysis,board_ops
from tools import pcb_editor, kicad_cli, datasheet_search, pins_of, bootstrap, schematic, workspace, rules, pinout, loop, interface, verify, trajectory, observation_schema, board_observation

MODULES = {"analysis":analysis,"board_ops":board_ops,"workspace":workspace,"schematic":schematic,"model_runner":model_runner,"intake":intake,"routing":routing,"postroute":postroute,"release":release,"library": library,"design": design, "validation": validation, "knowledge": knowledge,"pcb_editor": pcb_editor, "kicad_cli": kicad_cli, "datasheet_search": datasheet_search,
           "pins_of": pins_of, "bootstrap": bootstrap, "rules": rules, "pinout": pinout, "loop": loop, "interface": interface, "verify": verify, "trajectory": trajectory}


def manifest(*, internal: bool = False, include_legacy: bool = False, groups=None) -> list[dict]:
    if not internal and not include_legacy:
        selected=workspace.parse_tool_groups(groups)
        return [{'tool':name,'signature':str(inspect.signature(fn)),'doc':inspect.getdoc(fn).split('\n')[0],
                 'tier':'public','groups':[group for group,names in workspace.TOOL_GROUPS.items() if name in names],
                 'parameters':workspace._schema(fn),
                 'output_schema':board_observation.output_schema() if name=='inspect_board' else observation_schema.schema_for(name)}
                for name,fn in workspace.PUBLIC.items()
                if selected is None or any(name in workspace.TOOL_GROUPS[group] for group in selected)]
    out = []
    for mod_name, mod in MODULES.items():
        if not include_legacy and mod_name in {"interface", "pinout", "rules"}:
            continue
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != mod.__name__:
                continue
            legacy = mod_name == "pcb_editor" and name in {"autoroute", "route_net", "route_hv", "route_diff_pair", "fix_short", "connectivity", "check_diff_pairs", "check_decoupling", "check_protection", "check_dangling", "review", "fill_zones"}
            if legacy and not include_legacy:
                continue
            doc = (inspect.getdoc(fn) or "").split("\n")[0]
            out.append({"tool": f"{mod_name}.{name}", "signature": str(inspect.signature(fn)), "doc": doc, "tier": "experimental" if legacy or mod_name in {"interface", "pinout", "rules"} else "library"})
    return out


if __name__ == "__main__":
    requested=None
    if '--groups' in sys.argv:
        index=sys.argv.index('--groups')
        if index+1>=len(sys.argv):raise SystemExit('--groups requires comma-separated names')
        requested=workspace.parse_tool_groups(sys.argv[index+1])
    m = manifest(internal="--internal" in sys.argv,include_legacy="--include-legacy" in sys.argv)
    if requested is not None and not ('--internal' in sys.argv):
        allowed={x['function']['name'] for x in workspace.definitions(requested)}
        m=[x for x in m if x['tool'] in allowed]
    if "--md" in sys.argv:
        print("| tool | groups | signature | what |\n|---|---|---|---|")
        for t in m:
            t=dict(t,signature=t['signature'].replace('|',r'\|'))
            print(f"| `{t['tool']}` | {', '.join(t.get('groups',[]))} | `{t['signature']}` | {t['doc']} |")
    else:
        print(json.dumps(m, ensure_ascii=False, indent=1))
