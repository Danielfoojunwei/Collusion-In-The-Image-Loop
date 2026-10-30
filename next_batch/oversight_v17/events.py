"""Append-only experiment journal. Failed attempts are never overwritten."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from .finance import canonical


class IntegrityError(RuntimeError):
    pass


class Journal:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'events.jsonl'

    def read(self):
        rows, previous = [], '0' * 64
        if not self.path.exists():
            return rows
        for line in self.path.read_text().splitlines():
            try:
                row = json.loads(line)
                body = row['body']
                digest = hashlib.sha256(canonical(body).encode()).hexdigest()
                if row['sha256'] != digest or body['previous'] != previous or body['sequence'] != len(rows):
                    raise IntegrityError('Journal hash/order mismatch')
            except (ValueError, KeyError) as error:
                raise IntegrityError('Incomplete or malformed journal; recovery required') from error
            previous = digest
            rows.append(row)
        return rows

    @contextmanager
    def lock(self):
        with (self.root / '.journal.lock').open('a') as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    def append_locked(self, kind, data):
        rows = self.read()
        body = {'sequence': len(rows), 'previous': rows[-1]['sha256'] if rows else '0' * 64,
                'at_unix': time.time(), 'kind': kind, 'data': data}
        row = {'body': body, 'sha256': hashlib.sha256(canonical(body).encode()).hexdigest()}
        with self.path.open('ab') as file:
            file.write((canonical(row) + '\n').encode())
            file.flush()
            os.fsync(file.fileno())
        return row

    def append(self, kind, data):
        with self.lock():
            return self.append_locked(kind, data)

    def put(self, raw, suffix='bin'):
        if not isinstance(raw, bytes) or suffix not in {'bin', 'json', 'png', 'patch', 'txt'}:
            raise IntegrityError('Explicit byte artifact and safe suffix required')
        digest = hashlib.sha256(raw).hexdigest()
        path = self.root / 'objects' / (digest + '.' + suffix)
        path.parent.mkdir(exist_ok=True)
        try:
            with path.open('xb') as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
        except FileExistsError:
            if path.read_bytes() != raw:
                raise IntegrityError('Artifact changed')
        return {'sha256': digest, 'relative_path': str(path.relative_to(self.root)), 'bytes': len(raw)}

    def get(self, reference):
        if not isinstance(reference, dict) or set(reference) != {'relative_path','sha256','bytes'}:
            raise IntegrityError('Explicit artifact reference required')
        if not isinstance(reference['relative_path'],str) or not isinstance(reference['sha256'],str) or type(reference['bytes']) is not int:
            raise IntegrityError('Invalid artifact reference types')
        path = self.root / reference['relative_path']
        if not path.resolve().is_relative_to((self.root / 'objects').resolve()):
            raise IntegrityError('Unsafe object path')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != reference['sha256'] or len(raw) != reference['bytes']:
            raise IntegrityError('Artifact digest mismatch')
        return raw
