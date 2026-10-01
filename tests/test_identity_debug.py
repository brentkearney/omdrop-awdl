import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'userspace'))
from awdl_debug import dump_protocol


class ProtocolDumpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.root)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_default_does_not_materialize_body(self):
        class Body:
            def getvalue(self):
                raise AssertionError('disabled dump accessed transfer body')
        dump_protocol('upload.plist', Body())
        self.assertFalse((self.root / 'omdrop').exists())

    def test_verbose_redacts_nested_records_and_certificates(self):
        body = plistlib.dumps({'SenderRecordData': b'private-record',
                               'nested': [{'Certificate': b'private-cert', 'status': 'ready'}]})
        dump_protocol('ask.plist', body, verbose=True)
        path = self.root / 'omdrop/debug/ask.plist'
        value = plistlib.loads(path.read_bytes())
        self.assertNotEqual(value['SenderRecordData'], b'private-record')
        self.assertNotEqual(value['nested'][0]['Certificate'], b'private-cert')
        self.assertEqual(value['nested'][0]['status'], 'ready')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_non_plist_body_not_copied_in_normal_debug(self):
        dump_protocol('upload.plist', b'private archive', verbose=True)
        self.assertNotIn(b'private archive', (self.root / 'omdrop/debug/upload.plist').read_bytes())

    def test_sensitive_opt_in_preserves_exact_bytes(self):
        with patch.dict(os.environ, {'OMDROP_DEBUG_SENSITIVE': '1'}):
            dump_protocol('ask.plist', b'raw-sensitive-body')
        self.assertEqual((self.root / 'omdrop/debug/ask.plist').read_bytes(), b'raw-sensitive-body')

    def test_refuses_symlinked_debug_directory(self):
        (self.root / 'omdrop').mkdir(mode=0o700)
        (self.root / 'outside').mkdir(mode=0o700)
        (self.root / 'omdrop/debug').symlink_to(self.root / 'outside')
        with self.assertRaises(OSError):
            dump_protocol('ask.plist', b'anything', verbose=True)
        self.assertEqual(list((self.root / 'outside').iterdir()), [])



class MalformedDumpTests(unittest.TestCase):
    def test_malformed_xml_is_redacted_not_a_transfer_failure(self):
        body = b'<?xml version="1.0"?><plist><private-record'
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {}, clear=True):
                dump_protocol('broken.plist', body, verbose=True, runtime_dir=root)
            result = Path(root, 'omdrop/debug/broken.plist').read_bytes()
        self.assertNotIn(b'private-record', result)


if __name__ == '__main__':
    unittest.main()
