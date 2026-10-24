"""Create-only atomic evidence, checked reads and an exclusive producer lock."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import math
import re
from pathlib import Path
import tempfile


class IntegrityError(ValueError): pass


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def safe(path, root=None):
    path=Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise IntegrityError('Evidence path traverses a symlink or parent')
    if root is not None and not path.is_relative_to(Path(root).absolute()):
        raise IntegrityError('Evidence path escapes its run')
    return path


def put_bytes(path, raw):
    path=safe(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=raw: raise IntegrityError('Immutable evidence changed')
        return path
    fd,tmp=tempfile.mkstemp(prefix='.v11-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        try: os.link(tmp,path)
        except FileExistsError:
            if path.read_bytes()!=raw:raise IntegrityError('Concurrent evidence changed')
        directory=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:os.unlink(tmp)
    return path


def put(path, body):
    return put_bytes(path,(canonical({'body':body,'sha256':sha(canonical(body).encode())})+'\n').encode())


def parse(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise IntegrityError('Duplicate JSON key')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(IntegrityError('Nonfinite JSON')))


def read(path):
    value=parse(safe(path).read_text())
    if set(value)!={'body','sha256'} or value['sha256']!=sha(canonical(value['body']).encode()):
        raise IntegrityError('Evidence checksum differs')
    return value['body']


@contextmanager
def lock(out):
    out=safe(out);out.mkdir(parents=True,exist_ok=True)
    with (out/'producer.lock').open('a+b') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise IntegrityError('Another producer owns this run') from None
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def validate(value, schema):
    """Validate the explicit JSON Schema subset emitted by this package."""
    known={'type','const','enum','properties','required','additionalProperties','items',
           'minItems','maxItems','uniqueItems','minLength','maxLength','pattern','minimum','maximum','description'}
    if set(schema)-known:raise IntegrityError('Unsupported schema keyword')
    kind=schema.get('type')
    predicates={'object':lambda:type(value) is dict,'array':lambda:type(value) is list,
                'string':lambda:isinstance(value,str),'integer':lambda:type(value) is int,
                'number':lambda:type(value) in (int,float) and math.isfinite(value),
                'boolean':lambda:type(value) is bool,'null':lambda:value is None}
    if kind is not None and (kind not in predicates or not predicates[kind]()):raise IntegrityError('Wrong JSON value type')
    if 'const' in schema and (type(value)!=type(schema['const']) or value!=schema['const']):raise IntegrityError('Constant differs')
    if 'enum' in schema and not any(type(value)==type(x) and value==x for x in schema['enum']):raise IntegrityError('Value outside enum')
    if kind=='object':
        props=schema.get('properties',{})
        if set(schema.get('required',()))-set(value):raise IntegrityError('Required JSON field missing')
        if schema.get('additionalProperties') is False and set(value)-set(props):raise IntegrityError('Extra JSON field')
        for key,item in value.items():
            if key in props:validate(item,props[key])
    if kind=='array':
        if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',10**9):raise IntegrityError('Array length differs')
        if schema.get('uniqueItems') and len({canonical(v) for v in value})!=len(value):raise IntegrityError('Duplicate array item')
        for item in value:validate(item,schema.get('items',{}))
    if kind=='string':
        if not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',10**9):raise IntegrityError('String length differs')
        if 'pattern' in schema and re.search(schema['pattern'],value) is None:raise IntegrityError('String pattern differs')
    if kind in ('number','integer') and not schema.get('minimum',-math.inf)<=value<=schema.get('maximum',math.inf):
        raise IntegrityError('Number outside bounds')
