"""Strict transient-table retention and explicit analog-to-logic conversion."""
import csv
import math
import re


def parse_raw(text, vectors):
    try:
        header,rest=text.split('Variables:\n',1);variables,body=rest.split('Values:\n',1)
        meta=dict(line.split(':',1) for line in header.splitlines() if ':' in line)
        count=int(meta['No. Variables']);points=int(meta['No. Points'])
        if meta['Flags'].strip()!='real' or not 2<=count<=33 or not 2<=points<=2000000:
            raise ValueError('Unsupported raw dimensions or flags')
        names=[]
        for i,line in enumerate(variables.strip().splitlines()):
            fields=line.split()
            if len(fields)!=3 or int(fields[0])!=i:raise ValueError('Invalid raw variable index')
            names.append(fields[1].lower())
        if len(names)!=count or names[0]!='time' or len(set(names))!=len(names):raise ValueError('Invalid raw variables')
        tokens=body.split()
        if len(tokens)!=points*(count+1):raise ValueError('Truncated/extra raw samples')
        columns=[[] for _ in names]
        for i in range(points):
            offset=i*(count+1)
            if int(tokens[offset])!=i:raise ValueError('Missing raw sample index')
            for j in range(count):
                value=float(tokens[offset+1+j])
                if not math.isfinite(value):raise ValueError('Nonfinite raw sample')
                columns[j].append(value)
        if any(b<=a for a,b in zip(columns[0],columns[0][1:])):raise ValueError('Non-increasing transient time')
        return dict(time_s=columns[0],**{k:columns[names.index(v.lower())] for k,v in vectors.items()})
    except (KeyError,IndexError,ValueError) as error:
        raise ValueError('Invalid ngspice transient raw data: '+str(error)) from error


def write_csv(path,data):
    with path.open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(data)
        writer.writerows(zip(*data.values()))


def logic(data,channels,max_interval_s):
    """Schmitt conversion with stated thresholds; no guessed IO supply levels."""
    t=data['time_s']
    if (type(max_interval_s) not in (int,float) or not math.isfinite(max_interval_s)
        or max_interval_s<=0 or any(b<=a or b-a>max_interval_s*(1+1e-9) for a,b in zip(t,t[1:]))):
        raise ValueError('Transient timestep cannot resolve requested protocol')
    result={'time_s':t}
    for name,limits in channels.items():
        low,high=limits['low_v'],limits['high_v']
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in (low,high)) or low>=high:
            raise ValueError('Explicit finite low_v < high_v required')
        y=data[name];state=0 if y[0]<=low else 1 if y[0]>=high else None
        if state is None:raise ValueError('Initial logic level is indeterminate')
        bits=[]
        for value in y:
            if value<=low:state=0
            elif value>=high:state=1
            bits.append(state)
        result[name]=bits
    return result
