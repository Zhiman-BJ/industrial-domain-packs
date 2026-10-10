"""Recompute measurements from operator-approved captures bound to actual CAD.

The catalog and its files must be mounted read-only outside the agent workspace.
This is an analysis adapter, not a source of instrument data or device models.
No capture, calibration, model qualification or physical condition is inferred.
"""
import argparse
import bisect
import csv
import hashlib
import json
import math
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prerequisite_issues(test,catalog):
    """Public capture-plan gaps without returning private record contents."""
    issues=[];p=test.get('parameters')
    if not isinstance(p,dict):return ['Capture parameters must be an object']
    key=p.get('record')
    if not isinstance(key,str) or not key.strip():issues.append('Missing approved capture record identifier')
    for name in ('conditions','measurements'):
        if not isinstance(p.get(name),dict) or not p[name]:issues.append('Missing explicit capture '+name)
    if not isinstance(test.get('assertions'),dict) or not test['assertions']:issues.append('Missing independent measurement bounds')
    records=catalog.get('records',{})
    record=records.get(key) if isinstance(records,dict) and isinstance(key,str) else None
    if not isinstance(record,dict) or not record:
        issues.append('Missing approved candidate-bound capture record')
    else:
        if record.get('kind') not in ('measurement','qualified_simulation'):
            issues.append('Capture record is not independently qualified')
        if test.get('category')=='physical' and record.get('kind')!='measurement':
            issues.append('Physical witness requires a measurement, not a simulation record')
        if test.get('category') not in record.get('categories',[]):
            issues.append('Capture record is not qualified for the requested category')
        if p.get('conditions')!=record.get('conditions'):
            issues.append('Capture conditions differ from the approved record')
    return issues


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Expected a finite number')
    return value


def retained(root, item):
    name=item['path']
    if not isinstance(name,str) or Path(name).is_absolute():raise ValueError('Evidence paths must be relative')
    path=(root/name).resolve()
    if root not in path.parents or not path.is_file() or digest(path)!=item['sha256']:
        raise ValueError('Missing, changed or escaping evidence file: '+name)
    if path.stat().st_size>128*1024*1024:raise ValueError('Capture exceeds 128 MiB limit')
    return path


def catalog_inputs(catalog_path):
    """Indirect evidence dependencies for workspace freshness; never follow escapes."""
    catalog_path=Path(catalog_path).resolve();root=catalog_path.parent
    if not catalog_path.is_file():return [catalog_path]
    try:
        catalog=json.loads(catalog_path.read_text());paths={catalog_path}
        for record in catalog['records'].values():
            for item in [record['setup'],record['qualification'],*record['captures'].values()]:
                name=item['path']
                if Path(name).is_absolute():raise ValueError('Absolute evidence path')
                p=(root/name).resolve()
                if root not in p.parents:raise ValueError('Escaping evidence path')
                paths.add(p)
        return sorted(paths)
    except (ValueError,KeyError,TypeError,AttributeError):
        # The catalog bytes still invalidate old reports; execution reports the
        # schema/coverage error without silently following malformed references.
        return [catalog_path]


def table(path, units):
    with path.open(newline='') as f:
        reader=csv.DictReader(f)
        columns=reader.fieldnames
        if not columns or len(set(columns))!=len(columns) or set(columns)!=set(units):
            raise ValueError('CSV needs unique columns and explicit units for every column')
        result={k:[] for k in columns}
        for i,row in enumerate(reader):
            if i>=2000000:raise ValueError('Capture exceeds 2 million samples')
            if set(row)!=set(columns):raise ValueError('Malformed CSV row')
            for k in columns:result[k].append(number(float(row[k])))
    if not result[columns[0]]:raise ValueError('Empty capture')
    return result


def window(data, m, axis='time_s'):
    x=data[axis];start=number(m['start']);end=number(m['end'])
    if len(x)<2 or any(a>=b for a,b in zip(x,x[1:])):raise ValueError('Strictly increasing capture axis required')
    if not x[0]<=start<end<=x[-1]:raise ValueError('Capture does not cover requested observation interval')
    left=max(0,bisect.bisect_right(x,start)-1);right=min(len(x),bisect.bisect_left(x,end)+1)
    gap=number(m['max_interval'])
    if gap<=0 or any(b-a>gap*(1+1e-9) for a,b in zip(x[left:right],x[left+1:right])):
        raise ValueError('Capture sampling is too sparse for declared measurement resolution')
    selected=[i for i,t in enumerate(x) if start<=t<=end]
    if len(selected)<2:raise ValueError('At least two samples required inside observation interval')
    # No interpolation of peaks or settling across missing interval boundaries.
    if x[selected[0]]!=start or x[selected[-1]]!=end:
        raise ValueError('Measurement interval must have captured boundary samples')
    return selected


def waveform(data, m, units):
    if units.get('time_s')!='s' or units.get(m['channel'])!=m['unit']:
        raise ValueError('Waveform axis/channel units differ from contract')
    idx=window(data,m);x=[data['time_s'][i] for i in idx];y=[data[m['channel']][i] for i in idx]
    op=m['operation']
    if op=='min':return min(y)
    if op=='max':return max(y)
    if op=='peak_to_peak':return max(y)-min(y)
    if op=='mean':return sum((b-a)*(u+v)/2 for a,b,u,v in zip(x,x[1:],y,y[1:]))/(x[-1]-x[0])
    target=number(m['target'])
    if op=='max_abs_error':return max(abs(v-target) for v in y)
    if op=='undershoot':return max(0,target-min(y))
    if op=='overshoot':return max(0,max(y)-target)
    if op=='settling_s':
        tolerance=number(m['tolerance']);hold=number(m['hold_s']);trigger=number(m['trigger_s'])
        if tolerance<=0 or hold<=0 or trigger!=x[0]:raise ValueError('Positive settling tolerance/hold and captured trigger required')
        outside=[i for i,v in enumerate(y) if abs(v-target)>tolerance]
        settled=outside[-1]+1 if outside else 0
        if settled>=len(x) or x[-1]-x[settled]<hold:
            # Right censored: the last capture sample is a lower bound on settling.
            # A numeric lower bound could be mistaken for a measured settling time.
            raise ValueError('Settling not established for the required hold interval')
        return x[settled]-trigger
    raise ValueError('Unsupported waveform operation: '+str(op))


def digital(data, channel):
    times=data['time_s'];values=data[channel]
    if len(times)<2 or any(a>=b for a,b in zip(times,times[1:])) or any(v not in (0,1) for v in values):
        raise ValueError('Digital captures require increasing timestamps and exact 0/1 logic samples')
    def sample(t):
        if not times[0]<=t<=times[-1]:raise ValueError('Truncated digital frame')
        return int(values[bisect.bisect_right(times,t)-1])
    return times,values,sample


def uart(data,m,units):
    if units.get('time_s')!='s' or units.get(m['channel'])!='logic':raise ValueError('UART requires seconds/logic units')
    rate=number(m['baud']);bits=m.get('data_bits',8);stop=m.get('stop_bits',1)
    if rate<=0 or type(bits)!=int or bits not in (7,8) or type(stop)!=int or stop not in (1,2):raise ValueError('UART supports explicit 7/8 data bits, 1/2 stops, no parity')
    if m.get('parity','none')!='none':raise ValueError('Unsupported UART parity')
    from tools.protocol_capture import logic
    logic(data,units,[m['channel']],m.get('max_interval_s'))
    if m['max_interval_s']>1/rate/16*(1+1e-9):raise ValueError('UART requires at least sixteen samples per bit')
    t,y,sample=digital(data,m['channel']);period=1/rate
    if y[0]!=1:raise ValueError('Capture must begin in idle UART state')
    expected=m['expected_bytes']
    if not isinstance(expected,list) or not expected or any(type(b)!=int or not 0<=b<2**bits for b in expected):
        raise ValueError('Nonempty expected UART payload required')
    decoded=[];errors=0;busy=t[0]
    for i in range(1,len(t)):
        if y[i-1]!=1 or y[i]!=0 or t[i]<busy-period*1e-6:continue
        start=t[i]
        if sample(start+.5*period)!=0:errors+=1
        value=sum(sample(start+(1.5+b)*period)<<b for b in range(bits))
        for b in range(stop):
            if sample(start+(1.5+bits+b)*period)!=1:errors+=1
        decoded.append(value);busy=start+(1+bits+stop)*period
        if busy>t[-1]:raise ValueError('Truncated UART stop interval')
    # Complete capture is compared; a correct prefix cannot hide extra frames.
    return {'frames':len(decoded),'framing_errors':errors,
            'payload_mismatches':sum(a!=b for a,b in zip(decoded,expected))+abs(len(decoded)-len(expected))}


def spi(data,m,units):
    if units.get('time_s')!='s' or any(units.get(m[k])!='logic' for k in ('clock','select','data')):
        raise ValueError('SPI requires seconds/logic units')
    cpol=m['cpol'];cpha=m['cpha'];width=m.get('word_bits',8)
    if cpol not in (0,1) or cpha not in (0,1) or type(width)!=int or not 1<=width<=32:
        raise ValueError('Explicit SPI mode and 1..32 word bits required')
    if m.get('bit_order','msb') not in ('lsb','msb'):raise ValueError('Invalid SPI bit order')
    from tools.protocol_capture import logic
    channels=[m[k] for k in ('clock','select','data')]
    if len(set(channels))!=3:raise ValueError('SPI clock/select/data must be distinct channels')
    logic(data,units,channels,m.get('max_interval_s'))
    t,clk,_=digital(data,m['clock']);_,cs,select=digital(data,m['select']);_,_,sample=digital(data,m['data'])
    if cs[0]!=1 or cs[-1]!=1:raise ValueError('Capture must contain complete active-low chip-select transactions')
    words=[];bits=[];partial=0;periods=[];last=None
    for i in range(1,len(t)):
        if cs[i]!=cs[i-1] and (clk[i]!=cpol or clk[i]!=clk[i-1]):
            raise ValueError('Ambiguous SPI CS/clock boundary')
        if cs[i-1]==0 and cs[i]==1:
            partial+=bool(bits);bits=[];last=None
        if clk[i]==clk[i-1] or select(t[i])!=0:continue
        leading=clk[i]!=cpol
        if leading!=(cpha==0):continue
        if data[m['data']][i]!=data[m['data']][i-1]:
            raise ValueError('Data transition unresolved at SPI sampling edge')
        if last is not None:periods.append(t[i]-last)
        last=t[i];bits.append(sample(t[i]))
        if len(bits)==width:
            ordered=bits if m.get('bit_order','msb')=='lsb' else list(reversed(bits))
            words.append(sum(b<<i for i,b in enumerate(ordered)));bits=[]
    expected=m['expected_words']
    if not isinstance(expected,list) or not expected or any(type(w)!=int or not 0<=w<2**width for w in expected):
        raise ValueError('Nonempty expected SPI words required')
    if not periods:raise ValueError('Insufficient SPI edges to measure clock rate')
    if m['max_interval_s']>min(periods)/8*(1+1e-9):raise ValueError('SPI requires at least eight samples per clock')
    return {'words':len(words),'partial_words':partial,
            'payload_mismatches':sum(a!=b for a,b in zip(words,expected))+abs(len(words)-len(expected)),
            'clock_min_hz':1/max(periods),'clock_max_hz':1/min(periods)}


def sparameter(data,m,units):
    if units.get('frequency_hz')!='Hz' or any(units.get(m[k])!='1' for k in ('real','imag')):
        raise ValueError('S-parameters require Hz and dimensionless real/imaginary columns')
    idx=window(data,m,'frequency_hz')
    values=[math.hypot(data[m['real']][i],data[m['imag']][i]) for i in idx]
    if m['operation']=='magnitude_max':return max(values)
    if m['operation']=='magnitude_min':return min(values)
    raise ValueError('Unsupported S-parameter measurement')


def source_identity(request):
    """Keep file roles/relative paths, so swapping child sheets cannot reuse proof."""
    root=Path(request['schematic']).resolve().parent;result={}
    for key,value in request['design_sources'].items():
        if key=='spec':name='spec'
        else:
            path=Path(key).resolve()
            try:name='project/'+str(path.relative_to(root))
            except ValueError:name='external:'+str(path)
        if name in result:raise ValueError('Duplicate normalized evidence source')
        result[name]=value
    return result


def evaluate(request,catalog_path):
    catalog_path=Path(catalog_path).resolve();root=catalog_path.parent
    catalog=json.loads(catalog_path.read_text());parameters=request['parameters']
    if catalog.get('schema_version')!=1:raise ValueError('Unsupported capture catalog schema')
    record=catalog['records'][parameters['record']]
    if record['kind'] not in ('measurement','qualified_simulation'):raise ValueError('Capture is not qualified engineering evidence')
    if request['category'] not in record['categories']:raise ValueError('Capture not qualified for requested category')
    # Operator approval is a trust boundary; the agent cannot register itself.
    for key in ('reviewer','reviewed_at','method','scope'):
        if not isinstance(record.get(key),str) or not record[key].strip():raise ValueError('Missing capture review: '+key)
    for key in ('setup','qualification'):retained(root,record[key])
    actual={}
    for key in ('schematic','board'):
        path=request.get(key)
        if not path or not Path(path).is_file():raise ValueError('Both actual CAD artifacts required for board capture acceptance')
        actual[key]=digest(path)
    # Hierarchical sheets, spec, model and stimulus files are bound separately by
    # source digest; absolute workspace relocation does not change their bytes.
    required=record.get('design_sources',{})
    current=source_identity(request)
    if (not isinstance(required,dict) or not required or 'MISSING' in current.values()
            or required!=current or 'spec' not in current):
        raise ValueError('Capture source/spec/model hashes differ from current analysis')
    if actual!=record['design_sha256']:raise ValueError('Capture is for a different CAD revision')
    conditions=parameters['conditions']
    if not isinstance(conditions,dict) or not conditions or conditions!=record['conditions']:
        raise ValueError('Operating/fixture/model conditions differ from approved capture')
    measurements=parameters['measurements']
    if not isinstance(measurements,dict) or not measurements:raise ValueError('Explicit capture measurements required')
    values={};dependencies={str(catalog_path):digest(catalog_path)};cache={}
    for name,m in measurements.items():
        if not isinstance(name,str) or not name:raise ValueError('Named measurements required')
        item=record['captures'][m['capture']];path=retained(root,item);dependencies[str(path)]=digest(path)
        if path not in cache:cache[path]=table(path,item['units'])
        data=cache[path];kind=m['kind']
        if kind=='waveform':values[name]=waveform(data,m,item['units'])
        elif kind=='sparameter':values[name]=sparameter(data,m,item['units'])
        elif kind in ('uart','spi'):
            result=(uart if kind=='uart' else spi)(data,m,item['units'])
            for metric,value in result.items():
                key=name+'_'+metric
                if key in values:raise ValueError('Duplicate measurement name')
                values[key]=value
        elif kind in ('sd_spi','swd','can_classic'):
            from tools import protocol_capture
            result=getattr(protocol_capture,kind)(data,m,item['units'])
            for metric,value in result.items():
                key=name+'_'+metric
                if key in values:raise ValueError('Duplicate measurement name')
                values[key]=value
        else:raise ValueError('Unsupported capture decoder: '+str(kind))
    if set(values)!=set(request['assertions']):raise ValueError('Every computed metric needs independent bounds')
    # Recheck bytes after processing, including setup/qualification references.
    for item in (record['setup'],record['qualification']):
        path=retained(root,item);dependencies[str(path)]=digest(path)
    if any(digest(path)!=h for path,h in dependencies.items()):raise ValueError('Evidence changed during analysis')
    # Return the exact candidate revision digests so the analysis adapter can
    # enforce bind_to_revision independently of the catalog's source record.
    condition_hash=request.get('conditions_sha256')
    return {'status':'PASS','request_sha256':request['request_sha256'],
            'evidence_kind':'measurement' if record['kind']=='measurement' else 'simulation',
            'method':record['method'],'measurements':values,'evidence_sha256':dependencies,
            'board_sha256':actual['board'],'schematic_sha256':actual['schematic'],
            'spec_sha256':request.get('spec_sha256') or request.get('design_sources',{}).get('spec'),
            'conditions_sha256':condition_hash,
            'scope':record['scope']+'; sampled measurements under the exact approved conditions only'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--catalog',required=True)
    parser.add_argument('--request',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    request=json.loads(Path(args.request).read_text())
    try:result=evaluate(request,args.catalog)
    except (OSError,ValueError,KeyError,TypeError,IndexError,AttributeError) as error:
        result={'status':'UNKNOWN','method':'approved capture remeasurement',
                'request_sha256':request.get('request_sha256'),'issues':[str(error)]}
    Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
