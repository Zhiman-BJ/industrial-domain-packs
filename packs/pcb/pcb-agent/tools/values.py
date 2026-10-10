"""Parse component values using KiCad engineering notation, independently of tasks."""
import math
import re


def passive_kind(symbol):
    """Infer units from the native KiCad Device symbol, never its reference name."""
    match = re.fullmatch(r'Device:([RCL])(?:_\w+)?', symbol or '')
    return match[1] if match else None


def equivalent(actual, expected, symbol=None):
    """Compare passive nominal values; retain exact text for other components.

    Unit conversion is not component tolerance. The relative epsilon only
    accounts for floating-point conversion and has no absolute floor that
    could merge distinct pF/nH values or a nonzero value with zero.
    """
    if actual == expected:
        return True
    kind = passive_kind(symbol)
    if kind is None or not isinstance(actual, str) or not isinstance(expected, str):
        return False
    try:
        return math.isclose(numeric(actual, kind), numeric(expected, kind),
                            rel_tol=1e-12, abs_tol=0.0)
    except ValueError:
        return False


def numeric(text,kind=None):
    value=str(text).strip().replace('µ','u').replace('μ','u').replace('Ω','Ω')
    value=re.sub(r'\s+','',value)
    units={'R':('ohms','ohm','Ω'),'C':('F',),'L':('H',)}
    choices=units.get(kind,('ohms','ohm','Ω','F','H','V','A'))
    for unit in choices:
        if value.endswith(unit):value=value[:-len(unit)];break
    # 4k7, 2R2 and 100n; M is mega in a component value, not SPICE milli.
    embedded=re.fullmatch(r'(\d+)([pnumkKMGR])(\d+)',value)
    if embedded:value=embedded[1]+'.'+embedded[3]+embedded[2]
    match=re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(meg|MEG|[pnumkKMGR])?',value)
    if not match:raise ValueError('Unsupported component numeric value: '+str(text))
    scale={None:1,'p':1e-12,'n':1e-9,'u':1e-6,'m':1e-3,'k':1e3,'K':1e3,'M':1e6,'meg':1e6,'MEG':1e6,'G':1e9,'R':1}
    result=float(match[1])*scale[match[2]]
    if not math.isfinite(result):raise ValueError('Non-finite component value')
    return result
