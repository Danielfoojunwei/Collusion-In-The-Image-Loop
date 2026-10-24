import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import zstandard

spec = importlib.util.spec_from_file_location('restore_research_archive', Path(__file__).with_name('restore_research_archive.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RestoreTests(unittest.TestCase):
    def test_preserved_original_instructions_verify_through_legacy_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'handoff').mkdir()
            (root / 'legacy').mkdir()
            original = b'original historical instructions\n'
            (root / 'legacy/V4_AGENTS.md').write_bytes(original)
            (root / 'AGENTS.md').write_text('current V16 entry point\n')
            item = {'path': 'AGENTS.md', 'bytes': len(original),
                    'sha256': hashlib.sha256(original).hexdigest()}
            (root / 'handoff/SOURCE_SNAPSHOT.json').write_text(json.dumps({'files': [item]}))
            self.assertEqual(module.verify_restored(root)['files'], 1)
            (root / 'legacy/V4_AGENTS.md').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Restored project file differs'):
                module.verify_restored(root)

    def test_actual_archive_window_fits_explicit_decoder_limit(self):
        parameters = zstandard.ZstdCompressionParameters.from_level(3, window_log=29, write_content_size=0)
        compressed = zstandard.ZstdCompressor(compression_params=parameters).compress(b'x' * (4 * 1024 * 1024))
        self.assertGreater(zstandard.get_frame_parameters(compressed).window_size, 1024 * 1024)
        decoder = zstandard.ZstdDecompressor(max_window_size=1024 * 1024 * 1024)
        with decoder.stream_reader(io.BytesIO(compressed)) as stream:
            self.assertEqual(len(stream.read()), 4 * 1024 * 1024)

    def fixture(self, root, *, wrong_hash=False, missing=False, unsafe=False):
        (root / 'archives').mkdir()
        (root / 'handoff').mkdir()
        data = b'actual research evidence\n'
        name = 'runs/main/evidence.txt'
        item = {'path': name, 'bytes': len(data), 'mode': '0o600',
                'sha256': hashlib.sha256(data).hexdigest() if not wrong_hash else '0' * 64}
        (root / 'handoff/SOURCE_SNAPSHOT.json').write_text(json.dumps({'files': [item]}))
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as archive:
            if not missing:
                member = tarfile.TarInfo('../escape.txt' if unsafe else name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        compressed = zstandard.ZstdCompressor().compress(output.getvalue())
        parts = []
        for i, offset in enumerate(range(0, len(compressed), 37)):
            part = compressed[offset:offset + 37]
            path = 'archives/part-' + str(i)
            (root / path).write_bytes(part)
            parts.append({'path': path, 'bytes': len(part), 'sha256': hashlib.sha256(part).hexdigest()})
        (root / 'archives/INDEX.json').write_text(json.dumps({'parts': parts}))
        return root / name, data

    def test_restore_and_matching_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, data = self.fixture(root)
            self.assertEqual(module.restore(root)['new_files_restored'], 1)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(module.restore(root)['new_files_restored'], 0)
            self.assertEqual(module.restore(root, verify_only=True)['status'], 'verified')

    def test_conflicting_existing_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = self.fixture(root)
            path.parent.mkdir(parents=True)
            path.write_bytes(b'existing different evidence')
            with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
                module.restore(root)
            self.assertEqual(path.read_bytes(), b'existing different evidence')

    def test_corrupt_part_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / 'archives/part-0').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'Compressed part differs'):
                module.restore(root, verify_only=True)

    def test_wrong_research_digest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = self.fixture(root, wrong_hash=True)
            with self.assertRaisesRegex(ValueError, 'checksum differs'):
                module.restore(root)
            self.assertFalse(path.exists())

    def test_traversal_and_missing_evidence_are_rejected(self):
        for options, expected in [({'unsafe': True}, 'Unsafe archive path'), ({'missing': True}, 'missing manifest')]:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root, **options)
                with self.assertRaisesRegex(ValueError, expected):
                    module.restore(root, verify_only=True)


if __name__ == '__main__':
    unittest.main()
