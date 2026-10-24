#!/usr/bin/env python3
"""Verify and restore the research snapshot. No experiment imports or API calls."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile

import zstandard


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Unsafe archive path: ' + name)
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escapes destination: ' + name)
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
        raise ValueError('Symlink in destination: ' + name)
    return path


class Parts(io.RawIOBase):
    def __init__(self, paths):
        super().__init__()
        self.paths = iter(paths)
        self.current = None

    def readable(self):
        return True

    def readinto(self, buffer):
        while True:
            if self.current is None:
                path = next(self.paths, None)
                if path is None:
                    return 0
                self.current = path.open('rb')
            count = self.current.readinto(buffer)
            if count:
                return count
            self.current.close()
            self.current = None

    def close(self):
        if self.current is not None:
            self.current.close()
        super().close()


def restore(root, *, verify_only=False):
    root = Path(root).resolve()
    index = json.loads((root / 'archives/INDEX.json').read_text())
    snapshot = json.loads((root / 'handoff/SOURCE_SNAPSHOT.json').read_text())
    expected = {item['path']: item for item in snapshot['files']
                if PurePosixPath(item['path']).parts[0] == 'runs'}
    parts = []
    for item in index['parts']:
        path = safe_path(root, item['path'])
        if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise ValueError('Compressed part differs: ' + item['path'])
        parts.append(path)
    if not parts:
        raise ValueError('No archive parts')
    # Refuse conflicts before extracting any files. Existing matching V16 files
    # are allowed because they are also directly browsable in the Git checkout.
    if not verify_only:
        for name, item in expected.items():
            path = safe_path(root, name)
            if path.exists() and (not path.is_file() or path.stat().st_size != item['bytes']
                                  or digest(path) != item['sha256']):
                raise ValueError('Existing research file differs; refusing overwrite: ' + name)
    seen = {}
    restored = 0
    with Parts(parts) as joined:
        # python-zstandard enforces this limit in bytes. The actual archive uses
        # a 2**29-byte long-distance window; leave 1 GiB of explicit headroom.
        with zstandard.ZstdDecompressor(max_window_size=1024 * 1024 * 1024).stream_reader(joined) as stream:
            with tarfile.open(fileobj=stream, mode='r|') as archive:
                for member in archive:
                    name = str(PurePosixPath(member.name))
                    path = safe_path(root, name)
                    if PurePosixPath(name).parts[0] != 'runs':
                        raise ValueError('Non-research archive member: ' + name)
                    if member.isdir():
                        continue
                    if name not in expected or name in seen:
                        raise ValueError('Unexpected or duplicate file: ' + name)
                    item = expected[name]
                    if member.islnk():
                        reference = str(PurePosixPath(member.linkname))
                        safe_path(root, reference)
                        if reference not in seen or seen[reference] != item['sha256']:
                            raise ValueError('Unverified archive hardlink: ' + name)
                        source = None if verify_only else safe_path(root, reference).open('rb')
                    elif member.isfile():
                        if member.size != item['bytes']:
                            raise ValueError('Archive size differs: ' + name)
                        source = archive.extractfile(member)
                    else:
                        raise ValueError('Unsupported archive member: ' + name)
                    h = hashlib.sha256()
                    count = 0
                    temporary = None
                    target = None
                    try:
                        if not verify_only and not path.exists():
                            path.parent.mkdir(parents=True, exist_ok=True)
                            temporary = path.with_name(path.name + '.handoff-restoring')
                            target = temporary.open('xb')
                        if source is not None:
                            with source:
                                for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                                    count += len(block)
                                    h.update(block)
                                    if target is not None:
                                        target.write(block)
                            if count != item['bytes'] or h.hexdigest() != item['sha256']:
                                raise ValueError('Research file checksum differs: ' + name)
                        if target is not None:
                            target.close()
                            os.chmod(temporary, int(item.get('mode', '0o600'), 8) & 0o777)
                            temporary.replace(path)
                            restored += 1
                    finally:
                        if target is not None:
                            target.close()
                        if temporary is not None and temporary.exists():
                            temporary.unlink()
                    seen[name] = item['sha256']
            # Consume final padding to verify the complete Zstandard frame/CRC.
            while stream.read(4 * 1024 * 1024):
                pass
    if set(seen) != set(expected):
        missing = sorted(set(expected) - set(seen))
        raise ValueError('Archive is missing manifest-listed research files: ' + repr(missing[:20]))
    return {'status': 'verified' if verify_only else 'restored',
            'archive_files_verified': len(seen), 'new_files_restored': restored,
            'paid_calls': 0}


def verify_restored(root):
    root = Path(root).resolve()
    snapshot = json.loads((root / 'handoff/SOURCE_SNAPSHOT.json').read_text())
    aliases = {'README.md': 'legacy/V4_README.md', '.gitignore': 'legacy/V4_gitignore.txt',
               'AGENTS.md': 'legacy/V4_AGENTS.md'}
    for item in snapshot['files']:
        path = safe_path(root, aliases.get(item['path'], item['path']))
        if not path.is_file() or path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise ValueError('Restored project file differs: ' + item['path'])
    return {'status': 'all_original_files_verified', 'files': len(snapshot['files']), 'paid_calls': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--verify-only', action='store_true')
    group.add_argument('--verify-restored', action='store_true')
    args = parser.parse_args()
    result = verify_restored(args.root) if args.verify_restored else restore(args.root, verify_only=args.verify_only)
    print(json.dumps(result, indent=2))
