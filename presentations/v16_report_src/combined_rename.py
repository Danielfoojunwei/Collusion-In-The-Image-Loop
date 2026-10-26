"""Display names for the two studies and Study G's groups, applied to visible text only."""
import re

_REPS = [('Study 1 (V15)', 'Permissive study'), ('Study 2 (V16)', 'Guarded study'), ('Study 1', 'Permissive study'), ('Study 2', 'Guarded study'),
         ('V15', 'Permissive study'), ('V16', 'Guarded study')]
_ARMS = (('b01', 'G-Full'), ('b03', 'G-NoFig'), ('b05', 'G-Memory'))


def _fix(t):
    for x, y in _REPS:
        t = t.replace(x, y)
    for x, y in _ARMS:
        t = re.sub(rf'\b{x}\b', y, t)
    return t


def rename(html_text):
    """Rename in text nodes; tags, attributes and file paths are left untouched."""
    parts = re.split(r'(<[^>]+>)', html_text)
    return ''.join(p if p.startswith('<') else _fix(p) for p in parts)
