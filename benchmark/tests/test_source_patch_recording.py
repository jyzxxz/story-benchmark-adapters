"""A run keeps exact verified patch evidence before any provider is started."""
import tempfile
import unittest
from pathlib import Path

from story_benchmark.batch_worker import preserve_source_patch
from story_benchmark.io import BenchmarkError, sha256
from story_benchmark.recording import Recorder


class SourcePatchRecordingTests(unittest.TestCase):
    def test_verified_patch_is_portable_and_changed_patch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / 'manifest.json'
            patch = root / 'change.patch'
            manifest.write_bytes(b'{"schema_version":"source-patch.1","patch_file":"change.patch"}\n')
            patch.write_bytes(b'Exact published native source patch\n')
            row = {'source_patch_manifest': str(manifest),
                   'source_patch_manifest_sha256': sha256(manifest.read_bytes()),
                   'source_patch_file': str(patch),
                   'source_patch_sha256': sha256(patch.read_bytes())}
            report = {'ok': True, 'source_modified': True, 'checks': [row]}
            recorder = Recorder(root / 'run', 'run', 4000)
            preserve_source_patch(recorder, report)
            self.assertEqual((recorder.root / 'native/source_patch/change.patch').read_bytes(), patch.read_bytes())
            self.assertEqual((recorder.root / 'native/source_patch/manifest.json').read_bytes(), manifest.read_bytes())
            patch.write_bytes(b'Changed after provenance verification\n')
            with self.assertRaisesRegex(BenchmarkError, 'source_patch_changed_before_run'):
                preserve_source_patch(Recorder(root / 'other-run', 'other-run', 4000), report)

    def test_unmodified_or_failed_source_has_no_claimed_patch_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            for index, report in enumerate(({'ok': True, 'source_modified': False},
                                            {'ok': False, 'source_modified': True})):
                recorder = Recorder(Path(temp) / str(index), str(index), 4000)
                preserve_source_patch(recorder, report)
                self.assertFalse((recorder.root / 'native/source_patch').exists())


if __name__ == '__main__':
    unittest.main()
