"""Small strict KiCad s-expression reader preserving quoted strings."""
import json
import re

class Quoted(str):
    pass


def parse(text):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', text)
    stack, roots = [], []
    for t in tokens:
        if t == '(':
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif t == ')':
            if not stack: raise ValueError('Unbalanced closing parenthesis')
            stack.pop()
        else:
            if not stack: raise ValueError('Atom outside expression')
            # KiCad permits literal newlines/tabs inside quoted properties.
            # JSON's default string control-character rule is stricter than
            # the native format; retain those characters without rewriting CAD.
            stack[-1].append(Quoted(json.loads(t, strict=False)) if t.startswith('"') else t)
    if stack or len(roots) != 1: raise ValueError('Unbalanced or multiple root expressions')
    return roots[0]


def dump(node):
    if isinstance(node, list): return '(' + ' '.join(dump(n) for n in node) + ')'
    return json.dumps(str(node), ensure_ascii=False) if isinstance(node, Quoted) else str(node)


def children(node, key):
    return [n for n in node if isinstance(n, list) and n and n[0] == key]


def child(node, key, default=None):
    return next(iter(children(node, key)), default)
