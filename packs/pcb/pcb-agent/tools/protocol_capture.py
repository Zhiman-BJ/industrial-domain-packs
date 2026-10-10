"""Decode reviewed digital captures. No DUT behavior or board evidence is generated.

SD support is single-block SPI with explicit expected command transactions.
SWD support is ADIv5 synchronous transfers, one turnaround clock, no ORUNDETECT.
Unsupported/truncated captures raise ValueError (UNKNOWN); observed errors are
numeric metrics which the original acceptance contract must bound.
"""
import bisect
import hashlib
import math


def logic(data, units, channels, max_interval_s):
    if len(set(channels))!=len(channels):raise ValueError('Protocol channels must be distinct')
    if units.get('time_s') != 's' or any(units.get(c) != 'logic' for c in channels):
        raise ValueError('Protocol capture requires seconds and logic units')
    t = data['time_s']
    if (type(max_interval_s) not in (float, int) or not math.isfinite(max_interval_s)
            or max_interval_s <= 0 or len(t) < 2
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in t)
            or any(b <= a or b-a > max_interval_s*(1+1e-9) for a,b in zip(t,t[1:]))):
        raise ValueError('Protocol capture needs increasing, sufficiently dense samples')
    for c in channels:
        if len(data[c]) != len(t) or any(v not in (0,1) for v in data[c]):
            raise ValueError('Protocol channel needs exact 0/1 samples')
    return t


def integer(value, low, high):
    if type(value) != int or not low <= value <= high:
        raise ValueError('Protocol field outside supported integer range')
    return value


def crc(data, width, polynomial):
    value = 0
    for byte in data:
        for i in range(7,-1,-1):
            feedback = ((value >> (width-1)) & 1) ^ ((byte >> i) & 1)
            value = (value << 1) & ((1 << width)-1)
            if feedback: value ^= polynomial
    return value


def can_classic(data,m,units):
    """Nominal-bit-rate CAN 2.0A data frames, RXD 0=dominant, 1=recessive.

    Decodes the entire capture, with stuffing/CRC/ACK/EOF checks. Extended/RTR,
    CAN FD, arbitration/error frames and oscillator-drift compliance need their
    own qualified decoder. This is capture analysis, not a CAN controller model.
    """
    channel=m['channel'];t=logic(data,units,[channel],m['max_interval_s']);y=data[channel]
    bitrate=m['bitrate']
    if type(bitrate) not in (int,float) or not math.isfinite(bitrate) or not 0<bitrate<=1e6:
        raise ValueError('Explicit classic CAN bit rate in (0, 1 MHz] required')
    period=1/bitrate
    if m['max_interval_s']>period/16*(1+1e-9):raise ValueError('CAN requires at least sixteen samples per bit')
    expected=m['frames']
    if not isinstance(expected,list) or not expected:raise ValueError('Expected complete CAN data frames required')
    for e in expected:
        integer(e['id'],0,0x7ff)
        if not isinstance(e['data'],list) or len(e['data'])>8:raise ValueError('Classic CAN payload must contain 0..8 bytes')
        for b in e['data']:integer(b,0,255)
        integer(e.get('ack',0),0,1)
    if y[0]!=1:raise ValueError('CAN capture must begin at recessive idle')
    values=dict(frames=0,frame_mismatches=0,crc_errors=0,ack_errors=0,form_errors=0)
    cursor=1;decoded=[]
    def number(bits):
        value=0
        for bit in bits:value=(value<<1)|bit
        return value
    while cursor<len(t):
        start=next((i for i in range(cursor,len(t)) if y[i-1]==1 and y[i]==0),None)
        if start is None:break
        origin=t[start];position=0;last=None;run=0
        def raw():
            nonlocal position
            sample=origin+(position+.5)*period;position+=1
            if sample>t[-1]:raise ValueError('Truncated CAN frame')
            return int(y[bisect.bisect_right(t,sample)-1])
        def destuffed(count):
            nonlocal last,run
            bits=[]
            for _ in range(count):
                bit=raw();bits.append(bit)
                run=run+1 if bit==last else 1;last=bit
                if run==5:
                    stuffed=raw()
                    if stuffed==last:raise ValueError('CAN stuff/error frame; further framing is not established')
                    last=stuffed;run=1
            return bits
        head=destuffed(19)
        if head[0]!=0 or head[12:15]!=[0,0,0]:raise ValueError('Only standard CAN data frames are supported')
        length=number(head[15:19])
        if length>8:raise ValueError('Unsupported CAN DLC')
        payload=destuffed(8*length);observed_crc=number(destuffed(15))
        computed=0
        for bit in head+payload:
            feedback=((computed>>14)&1)^bit;computed=(computed<<1)&0x7fff
            if feedback:computed^=0x4599
        values['crc_errors']+=computed!=observed_crc
        tail=[raw() for _ in range(13)] # CRC delimiter, ACK, delimiter, EOF7, IFS3
        values['form_errors']+=sum(bit!=1 for i,bit in enumerate(tail) if i!=1)
        frame=dict(id=number(head[1:12]),data=[number(payload[i:i+8]) for i in range(0,len(payload),8)],ack=tail[1])
        decoded.append(frame)
        end=origin+position*period
        if end>t[-1]+m['max_interval_s']*1.01:raise ValueError('Incomplete CAN intermission')
        cursor=max(start+1,bisect.bisect_left(t,end))
    values['frames']=len(decoded);values['frame_mismatches']=abs(len(decoded)-len(expected))
    for actual,wanted in zip(decoded,expected):
        values['frame_mismatches']+=(actual['id'],actual['data'])!=(wanted['id'],wanted['data'])
        values['ack_errors']+=actual['ack']!=wanted.get('ack',0)
    if not decoded:raise ValueError('No complete CAN data frames')
    return values


def spi_transactions(data, m, units):
    """Full-duplex mode-0/3 SPI, preserving chip-select transaction boundaries."""
    channels = [m[k] for k in ('clock','select','mosi','miso')]
    t = logic(data, units, channels, m['max_interval_s'])
    clk,cs,mosi,miso = (data[c] for c in channels)
    mode = integer(m['mode'],0,3)
    if mode not in (0,3): raise ValueError('SD SPI supports mode 0 or 3')
    if cs[0] != 1 or cs[-1] != 1: raise ValueError('Capture needs complete CS transactions')
    transactions=[];tx=[];rx=[];periods=[];last=None;word_tx=word_rx=bits=0
    for i in range(1,len(t)):
        if cs[i] != cs[i-1]:
            if clk[i] != mode//3 or clk[i] != clk[i-1]:
                raise ValueError('Ambiguous CS/clock boundary')
            if cs[i] == 1:
                if bits: raise ValueError('Truncated SPI byte at CS boundary')
                if tx: transactions.append((tx,rx))
                tx=[];rx=[];last=None
        if cs[i] or clk[i-1] != 0 or clk[i] != 1: continue
        # Clock and data changing within the same sample cannot prove setup.
        if mosi[i] != mosi[i-1] or miso[i] != miso[i-1]:
            raise ValueError('Data transition unresolved at SPI sampling edge')
        if last is not None: periods.append(t[i]-last)
        last=t[i];word_tx=(word_tx<<1)|int(mosi[i]);word_rx=(word_rx<<1)|int(miso[i]);bits+=1
        if bits==8:
            tx.append(word_tx);rx.append(word_rx);word_tx=word_rx=bits=0
    if not transactions or not periods: raise ValueError('No complete SPI traffic')
    if max(periods) <= 0 or m['max_interval_s'] > min(periods)/8*(1+1e-9):
        raise ValueError('At least eight time samples per SPI clock required')
    return transactions, {'clock_min_hz':1/max(periods),'clock_max_hz':1/min(periods)}


def sd_spi(data, m, units):
    transactions, rate = spi_transactions(data,m,units)
    expected=m['transactions']
    if not isinstance(expected,list) or not expected: raise ValueError('Expected SD transactions required')
    result=dict(rate,transactions=len(transactions),transaction_mismatches=abs(len(transactions)-len(expected)),
                command_crc_errors=0,response_errors=0,data_crc_errors=0,payload_mismatches=0,
                read_blocks=0,write_blocks=0,busy_errors=0)
    for index,(tx,rx) in enumerate(transactions):
        # One command per CS transaction; additional commands must not be hidden.
        start=next((i for i,b in enumerate(tx) if b!=0xff),None)
        if start is None or start+6>len(tx): raise ValueError('Missing or truncated SD command')
        packet=tx[start:start+6];cmd=packet[0]&63;argument=int.from_bytes(bytes(packet[1:5]),'big')
        tx_allowed=set(range(start,start+6))
        if packet[0] & 0xc0 != 0x40 or packet[5]&1 != 1:
            result['transaction_mismatches']+=1
        if packet[5] != ((crc(packet[:5],7,0x09)<<1)|1):result['command_crc_errors']+=1
        if index>=len(expected):continue
        e=expected[index];wanted=integer(e['command'],0,63)
        if cmd!=wanted or argument!=integer(e['argument'],0,2**32-1):result['transaction_mismatches']+=1
        delay=integer(e['response_max_bytes'],1,1024)
        pos=start+6
        while pos<len(rx) and rx[pos]==0xff:pos+=1
        if pos>=len(rx):raise ValueError('SD response missing from capture')
        if pos-(start+6)>=delay:result['response_errors']+=1
        response=e['response']
        if not isinstance(response,list) or not response:raise ValueError('Expected complete SD response required')
        response=[integer(v,0,255) for v in response]
        if pos+len(response)>len(rx):raise ValueError('Truncated SD response')
        if rx[pos:pos+len(response)]!=response:result['response_errors']+=1
        pos+=len(response)
        # Do not infer card state or silently accept unsupported data commands.
        if cmd in (17,24):
            size=integer(e['block_bytes'],1,2048)
            expected_hash=e['payload_sha256']
            if not isinstance(expected_hash,str) or len(expected_hash)!=64 or any(c not in '0123456789abcdef' for c in expected_hash):
                raise ValueError('Expected block SHA-256 required')
            stream=rx if cmd==17 else tx
            token_start=pos
            while pos<len(stream) and stream[pos]==0xff:pos+=1
            limit=integer(e['token_max_bytes'],1,1000000)
            if pos>=len(stream):raise ValueError('SD data token missing')
            if pos-token_start>=limit or stream[pos]!=0xfe:result['response_errors']+=1
            pos+=1
            if pos+size+2>len(stream):raise ValueError('Truncated SD block or CRC')
            if cmd==24:tx_allowed.update(range(pos-1,pos+size+2))
            payload=bytes(stream[pos:pos+size]);pos+=size
            observed_crc=int.from_bytes(bytes(stream[pos:pos+2]),'big');pos+=2
            if observed_crc!=crc(payload,16,0x1021):result['data_crc_errors']+=1
            if hashlib.sha256(payload).hexdigest()!=expected_hash:result['payload_mismatches']+=1
            result['read_blocks' if cmd==17 else 'write_blocks']+=1
            if cmd==24:
                while pos<len(rx) and rx[pos]==0xff:pos+=1
                if pos>=len(rx):raise ValueError('Missing SD write response')
                if rx[pos]&31!=5:result['response_errors']+=1
                pos+=1;busy_start=pos
                while pos<len(rx) and rx[pos]==0:pos+=1
                if pos>=len(rx):raise ValueError('Capture ends before SD busy release')
                if pos-busy_start>integer(e['busy_max_bytes'],0,1000000) or rx[pos]!=0xff:result['busy_errors']+=1
                pos+=1
        elif cmd not in (0,8,13,16,55,58,59,41):
            raise ValueError('Unsupported SD command: '+str(cmd))
        if any(b!=0xff for b in tx[pos:]) or any(b!=0xff for b in rx[pos:]):
            result['transaction_mismatches']+=1
        if any(b!=0xff for i,b in enumerate(tx) if i not in tx_allowed):
            result['transaction_mismatches']+=1
    return result


def swd(data, m, units):
    channels=[m['clock'],m['data']]
    t=logic(data,units,channels,m['max_interval_s']);clk,line=(data[c] for c in channels)
    samples=[];edges=[]
    for i in range(1,len(t)):
        if clk[i-1]==0 and clk[i]==1:
            if line[i]!=line[i-1]:raise ValueError('SWD data unresolved at clock edge')
            samples.append(int(line[i]));edges.append(t[i])
    if len(edges)<2:raise ValueError('No SWD clock traffic')
    periods=[b-a for a,b in zip(edges,edges[1:])]
    if m['max_interval_s']>min(periods)/8*(1+1e-9):raise ValueError('At least eight samples per SWD clock required')
    transfers=m['transfers']
    if not isinstance(transfers,list) or not transfers:raise ValueError('Expected SWD transfers required')
    result=dict(transfers=0,request_errors=0,ack_errors=0,parity_errors=0,data_mismatches=0,
                clock_min_hz=1/max(periods),clock_max_hz=1/min(periods))
    cursor=0
    def take(count):
        nonlocal cursor
        if cursor+count>len(samples):raise ValueError('Truncated SWD transfer')
        bits=samples[cursor:cursor+count];cursor+=count;return bits
    def value(bits):return sum(b<<i for i,b in enumerate(bits))
    # Explicit full preamble avoids searching for convenient valid subsequences.
    preamble=m['preamble_bits']
    if not isinstance(preamble,list) or any(type(b)!=int or b not in (0,1) for b in preamble):
        raise ValueError('Explicit SWD preamble bits required')
    if take(len(preamble))!=preamble:result['request_errors']+=1
    for e in transfers:
        idle=integer(e.get('idle_cycles_before',0),0,1000000)
        if any(take(idle)):result['request_errors']+=1
        req=take(8);ap=integer(e['ap'],0,1);read=integer(e['read'],0,1);addr=integer(e['address'],0,12)
        if addr%4:raise ValueError('SWD register address must be 0,4,8,12')
        fields=[ap,read,(addr>>2)&1,(addr>>3)&1]
        if req!=[1,*fields,sum(fields)%2,0,1]:result['request_errors']+=1
        take(1);ack=value(take(3));expected_ack=integer(e.get('ack',1),1,7)
        if expected_ack not in (1,2,4):raise ValueError('SWD ACK must be OK, WAIT or FAULT')
        if ack!=expected_ack:result['ack_errors']+=1
        if ack not in (1,2,4):raise ValueError('Invalid SWD ACK; subsequent framing unknown')
        if ack==1:
            if not req[2]:take(1)
            bits=take(32);parity=take(1)[0]
            if parity!=sum(bits)%2:result['parity_errors']+=1
            if value(bits)!=integer(e['value'],0,2**32-1):result['data_mismatches']+=1
            if req[2]:take(1)
        else:
            # ADIv5 ORUNDETECT=0: WAIT/FAULT have no data phase.
            take(1)
        result['transfers']+=1
    if any(take(len(samples)-cursor)):result['request_errors']+=1
    return result
