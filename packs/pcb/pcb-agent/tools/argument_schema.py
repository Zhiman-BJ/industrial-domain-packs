"""Public input validation and bounded, actionable error observations.

Expected values come only from the public API or current candidate state, never
from private acceptance contracts or reference designs. No automatic coercion.
"""
import json
import math
import re

_MISSING = object()
_SENSITIVE = re.compile(r'(password|secret|token|api[_-]?key|authorization|credential)', re.I)


def value_type(value):
    return ('missing' if value is _MISSING else 'null' if value is None else
            'boolean' if isinstance(value, bool) else 'integer' if isinstance(value, int) else
            'number' if isinstance(value, float) else 'string' if isinstance(value, str) else
            'array' if isinstance(value, list) else 'object' if isinstance(value, dict) else 'invalid')


def preview(value, path='', depth=0):
    if _SENSITIVE.search(path):return '<redacted>'
    if value is _MISSING:return '<missing>'
    if depth > 3:return '<nested value omitted>'
    if isinstance(value, str):return value[:240] + ('…' if len(value)>240 else '')
    if isinstance(value, dict):
        out={str(k)[:100]:preview(v,str(k),depth+1) for k,v in list(value.items())[:8]}
        if len(value)>8:out['<omitted_keys>']=len(value)-8
        return out
    if isinstance(value, (list,tuple)):
        return [preview(v,path,depth+1) for v in value[:8]]+(['<more items omitted>'] if len(value)>8 else [])
    if isinstance(value,float) and not math.isfinite(value):return str(value)
    if value is None or isinstance(value,(bool,int,float)):return value
    return '<'+type(value).__name__+'>'


def expected_schema(schema):
    # Local schema only; never repeat an entire nested contract in each error.
    keys=('type','enum','minimum','maximum','exclusiveMinimum','exclusiveMaximum',
          'minItems','maxItems','minLength','maxLength','required','description')
    out={k:preview(schema[k]) for k in keys if k in schema}
    if 'enum' in schema:
        out['enum']=schema['enum'][:64]
        if len(schema['enum'])>64:out['enum_truncated']=True
    if 'properties' in schema:out['allowed_fields']=list(schema['properties'])[:40]
    if 'additionalProperties' in schema:out['additionalProperties']=schema['additionalProperties']
    if 'items' in schema:out['items']={k:v for k,v in schema['items'].items() if k in keys}
    return out


class InputError(ValueError):
    """Compatible with existing ValueError callers; carries public repair data."""
    def __init__(self, code, field, received, expected, remediation, message=None):
        self.feedback={'code':code,'field':field,
            'received':{'type':value_type(received),'value':preview(received,field)},
            'expected':expected,'remediation':remediation}
        self.operation_executed=None
        super().__init__(message or f'{field}: {code}; {remediation}')


def validate(value,schema,path='arguments'):
    kinds=schema['type'] if isinstance(schema['type'],list) else [schema['type']]
    actual=value_type(value)
    def fail(code,fix,message=None,received=value,expected=None,field=path):
        raise InputError(code,field,received,expected_schema(schema) if expected is None else expected,fix,message)
    if actual not in kinds and not(actual=='integer' and 'number' in kinds):
        fix='Supply a value of the expected JSON type.'
        if actual=='string' and ('number' in kinds or 'integer' in kinds):fix='Use an unquoted JSON number, or null only if allowed.'
        if actual=='string' and ('array' in kinds or 'object' in kinds):fix='Pass a native JSON array/object, not a string containing JSON.'
        if actual=='null' and ('array' in kinds or 'object' in kinds):fix='Optional containers use omission for defaults; inspect_tool shows required arguments.'
        fail('TYPE_MISMATCH',fix,f'{path}: expected {kinds}, got {actual}; {fix}')
    if actual in ('integer','number'):
        if not math.isfinite(value):fail('NONFINITE_NUMBER','Supply a finite JSON number.',expected={'type':schema['type'],'finite':True})
        for key,bad in (('minimum',lambda x:value<x),('maximum',lambda x:value>x),
                        ('exclusiveMinimum',lambda x:value<=x),('exclusiveMaximum',lambda x:value>=x)):
            if key in schema and bad(schema[key]):fail('OUT_OF_RANGE','Supply a number within the stated bounds.')
    if 'enum' in schema and value not in schema['enum']:fail('INVALID_CHOICE','Use one of the allowed values.')
    if actual=='string' and (len(value)<schema.get('minLength',0) or len(value)>schema.get('maxLength',len(value))):
        fail('STRING_LENGTH','Adjust the string to the stated length bounds.')
    if actual=='object':
        props=schema.get('properties',{})
        missing=sorted(set(schema.get('required',[]))-set(value))
        if missing:
            key=missing[0]
            fail('MISSING_FIELD','Supply this required field; keep other valid arguments.',
                 f'{path}: missing required properties {missing}',received=_MISSING,
                 expected=expected_schema(props.get(key,{})),field=path+'.'+key)
        unknown=sorted(set(value)-set(props))
        if schema.get('additionalProperties') is False and unknown:
            key=unknown[0]
            fail('UNKNOWN_FIELD','Remove or rename this field using the allowed field names.',
                 f'{path}: unknown properties {unknown}',received=value[key],
                 expected={'allowed_fields':list(props),'additionalProperties':False},field=path+'.'+key)
        for key,child in value.items():
            if key in props:validate(child,props[key],path+'.'+key)
    elif actual=='array':
        if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',len(value)):
            fail('ARRAY_LENGTH','Supply an array within the stated item-count bounds.',expected=dict(expected_schema(schema),received_length=len(value)))
        if 'items' in schema:
            for i,child in enumerate(value):validate(child,schema['items'],f'{path}[{i}]')


def decode_arguments(raw):
    if isinstance(raw,str):
        try:raw=json.loads(raw)
        except json.JSONDecodeError as e:
            error=InputError('INVALID_JSON','arguments',raw,{'type':'object','encoding':'valid JSON'},
                f'Fix JSON syntax at line {e.lineno}, column {e.colno}; send one object with quoted keys.')
            error.operation_executed=False
            raise error from e
    try:validate(raw,{'type':'object'})
    except InputError as error:
        error.operation_executed=False
        raise
    return raw


def error_observation(error, tool_name=None):
    out={'status':'ERROR','error_type':type(error).__name__,'message':str(error),
         'tool_name':tool_name,'scope':tool_name or 'dispatch',
         'schema_hint':{'tool':'inspect_tool','arguments':{'name':tool_name}}}
    if isinstance(error,InputError):
        out['input_error']=error.feedback
        out['operation_executed']=error.operation_executed
        out['next_action']=error.feedback['remediation']
    else:
        out['next_action']='Inspect the error and current candidate state; use inspect_tool for the public argument contract. Do not rebuild or replay a mutation before checking operation_outcome.'
    return out
