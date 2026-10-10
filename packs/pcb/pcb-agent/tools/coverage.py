"""Measure an independent fixed common-component sample, with missing items visible."""
from pathlib import Path
import json
import datetime
from tools import library,schematic
from tools.sexpr import parse,children


def measure(sample_path=None):
    path=Path(sample_path or Path(__file__).parent/'data'/'common_components.json')
    sample=json.loads(path.read_text());groups={};total=found=0
    for kind in ('symbols','footprints'):
        for category,entries in sample[kind].items():
            rows=[]
            for lid in entries:
                lib,name=lid.split(':',1);row={'lib_id':lid,'status':'MISSING'}
                try:
                    if kind=='symbols':
                        node=schematic.resolved_symbol(lib,name)
                        pins={n for unit in schematic.symbol_units(node).values() for n,*_ in unit}
                        row.update(status='SYNTAX_AND_PINS' if pins else 'NO_PINS',pin_count=len(pins))
                    else:
                        p=next((Path(d)/f'{lib}.pretty'/f'{name}.kicad_mod' for d in library.footprint_dirs() if (Path(d)/f'{lib}.pretty'/f'{name}.kicad_mod').is_file()),None)
                        if p:
                            node=parse(p.read_text());pads={str(n[1]) for n in children(node,'pad') if str(n[1])}
                            row.update(status='SYNTAX_AND_PADS' if pads else 'NO_PADS',pad_count=len(pads))
                except (LookupError,FileNotFoundError):pass
                except Exception as e:row.update(status='PARSE_ERROR',error=str(e))
                rows.append(row)
            n=sum(r['status'] in ('SYNTAX_AND_PINS','SYNTAX_AND_PADS') for r in rows);total+=len(rows);found+=n
            groups[f'{kind}:{category}']={'found':n,'total':len(rows),'items':rows}
    return {'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sample_source':str(path),
            'scope':sample['purpose'],'symbol_dirs':library.symbol_dirs(),'footprint_dirs':library.footprint_dirs(),
            'total':total,'found':found,'groups':groups,'kicad_runtime':'UNKNOWN','datasheet_match':'UNKNOWN',
            'missing':[r['lib_id'] for g in groups.values() for r in g['items'] if r['status'] not in ('SYNTAX_AND_PINS','SYNTAX_AND_PADS')]}


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--sample');p.add_argument('--out',required=True);a=p.parse_args()
    r=measure(a.sample);Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps(r,indent=2))
    print(json.dumps({k:v for k,v in r.items() if k!='groups'},indent=2))
    for k,g in r['groups'].items():print(k,g['found'], '/',g['total'])
