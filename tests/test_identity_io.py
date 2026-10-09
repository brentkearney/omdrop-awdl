"""Synthetic identities only: resolver state, preservation, and real TLS loading."""
import base64
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'userspace'))
import awdl_identity as identity


def payload(certificate=b'certificate', key=b'key', record=b'record', fetch_id='a' * 32):
    header = dict(certificate=len(certificate), key=len(key), record=len(record),
                  fetched_at=100, period_ends=200, hard_expiry=300, fetch_id=fetch_id)
    return b'OMDROP-IDENTITY 1\n' + json.dumps(header).encode() + b'\n' + certificate + key + record


class ResolverIOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('openssl'):
            raise unittest.SkipTest('openssl is required for synthetic certificate fixtures')
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        directory = Path(cls.fixtures.name)
        identity._create_self_signed(directory / 'first', 'Synthetic identity')
        identity._create_self_signed(directory / 'second', 'Other synthetic identity')
        cls.certificate = (directory / 'first/certificate.self-signed.pem').read_bytes()
        cls.key = (directory / 'first/key.self-signed.pem').read_bytes()
        cls.other_key = (directory / 'second/key.self-signed.pem').read_bytes()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.keys = self.home / '.omdrop/keys'
        self.keys.mkdir(parents=True)
        self.config = self.home / 'config/omdrop'
        self.runtime = self.home / 'runtime/omdrop'
        self.config.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        self.user = types.SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_dir=str(self.home))
        for context in (
                patch.object(identity, '_invoking_user', return_value=(self.user, False)),
                patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.config.parent),
                                       'XDG_RUNTIME_DIR': str(self.runtime.parent)}),
                patch.object(identity.time, 'time', return_value=150.9)):
            context.start()
            self.addCleanup(context.stop)

    def write_pair(self, suffix='', key=None):
        (self.keys / f'certificate{suffix}.pem').write_bytes(self.certificate)
        (self.keys / f'key{suffix}.pem').write_bytes(self.key if key is None else key)

    def settings(self, source):
        (self.config / 'settings').write_text(f'identity_source={source}\n')

    def window(self, source='1password', expiry=300, fetch_id='a' * 32):
        (self.runtime / 'window').write_text(
            f'source={source}\nfetch_id={fetch_id}\nhard_expiry={expiry}\n')

    def assert_error(self, name, **kwargs):
        with self.assertRaisesRegex(identity.IdentityError, '^' + name + '$'):
            identity.resolve_identity(**kwargs)

    def test_matching_disk_uses_only_its_record_and_loads_tls(self):
        self.write_pair()
        self.write_pair('.self-signed')
        (self.keys / 'validation_record.cms').write_bytes(b'synthetic disk record')
        selected = identity.resolve_identity()
        self.assertEqual((selected.source, selected.record_data), ('disk', b'synthetic disk record'))
        selected.load_cert_chain(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))

    def test_standalone_1password_ignores_cache_and_disk_record(self):
        self.settings('1password')
        self.write_pair()
        self.write_pair('.self-signed')
        (self.keys / 'validation_record.cms').write_bytes(b'disk record')
        with patch.object(identity, '_read_cache', side_effect=AssertionError('cache must not be read')):
            selected = identity.resolve_identity()
        self.assertEqual((selected.source, selected.record_data), ('self-signed', None))
        selected.load_cert_chain(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))

    def test_window_pins_cache_and_ignores_changed_settings(self):
        self.settings('disk')
        self.write_pair()
        self.window()
        with patch.object(identity, '_read_cache', return_value=payload(record=b'window record')):
            selected = identity.resolve_identity()
        self.assertEqual((selected.source, selected.record_data), ('cache', b'window record'))

    def test_cache_window_failures_never_fall_back_to_disk(self):
        self.write_pair()
        self.write_pair('.self-signed')
        self.window()
        for frame, error in ((None, 'cache-missing'), (b'bad', 'cache-malformed'),
                             (payload(fetch_id='b' * 32), 'cache-mismatch')):
            with self.subTest(error=error), patch.object(identity, '_read_cache', return_value=frame):
                self.assert_error(error)
        with patch.object(identity, '_read_cache', side_effect=identity.IdentityError('cache-unreadable-as-root')):
            self.assert_error('cache-unreadable-as-root')

    def test_expired_window_and_unparseable_window_precede_cache_io(self):
        self.window(expiry=150)
        with patch.object(identity, '_read_cache', side_effect=AssertionError('no read')):
            self.assert_error('window-expired')
            (self.runtime / 'window').write_text('source=other\n')
            self.assert_error('window-unparseable')

    def test_explicit_keys_replaces_only_material_directory(self):
        self.settings('disk')
        explicit = self.home / 'explicit'
        (explicit / 'keys').mkdir(parents=True)
        (explicit / 'keys/certificate.pem').write_bytes(self.certificate)
        (explicit / 'keys/key.pem').write_bytes(self.key)
        self.assertEqual(identity.resolve_identity(keys=explicit).source, 'disk')
        self.window('self-signed')
        self.assert_error('self-signed-missing', keys=explicit, create=False)
        self.assertFalse((explicit / 'keys/key.self-signed.pem').exists())

    def test_disk_mismatch_falls_back_without_touching_apple_files(self):
        self.write_pair(key=self.other_key)
        (self.keys / 'validation_record.cms').write_bytes(b'preserve this record')
        original = {path.name: path.read_bytes() for path in self.keys.iterdir()}
        with self.assertWarnsRegex(RuntimeWarning, 'disk-mismatch'):
            selected = identity.resolve_identity(computer_name='Synthetic fallback')
        self.assertEqual((selected.source, selected.record_data), ('self-signed', None))
        selected.load_cert_chain(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        for name, value in original.items():
            self.assertEqual((self.keys / name).read_bytes(), value)
        for name in ('certificate.self-signed.pem', 'key.self-signed.pem'):
            self.assertEqual((self.keys / name).stat().st_mode & 0o777, 0o600)
        self.window('disk')
        self.assert_error('disk-mismatch')

    def test_incomplete_disk_is_preserved_and_pinned_disk_errors(self):
        certificate = self.keys / 'certificate.pem'
        certificate.write_bytes(self.certificate)
        self.write_pair('.self-signed')
        with self.assertWarnsRegex(RuntimeWarning, 'disk-incomplete'):
            self.assertEqual(identity.resolve_identity().source, 'self-signed')
        self.assertEqual(certificate.read_bytes(), self.certificate)
        self.assertFalse((self.keys / 'key.pem').exists())
        self.window('disk')
        self.assert_error('disk-missing')

    def test_half_pair_and_read_only_resolution_do_not_create(self):
        self.settings('self-signed')
        self.assert_error('self-signed-missing', create=False)
        self.assertEqual(list(self.keys.iterdir()), [])
        (self.keys / 'key.self-signed.pem').write_bytes(self.key)
        self.assert_error('self-signed-half-pair')
        self.assertEqual((self.keys / 'key.self-signed.pem').read_bytes(), self.key)
        self.assertFalse((self.keys / 'certificate.self-signed.pem').exists())

    def test_root_never_creates_even_when_create_requested(self):
        # Simulated root: the reads that would run as the user run here as
        # this test's own uid, which is what they would get.
        with patch.object(identity, '_invoking_user', return_value=(self.user, True)), \
                patch.object(identity, '_as_user', return_value={}), \
                patch.object(identity, '_identity_paths', return_value=(self.config / 'settings', self.runtime / 'window')):
            self.assert_error('self-signed-missing', create=True)
        self.assertEqual(list(self.keys.iterdir()), [])

    def test_shared_lock_concurrent_creators_keep_one_valid_pair(self):
        directory = self.home / 'new/keys'
        with ThreadPoolExecutor(max_workers=3) as workers:
            list(workers.map(lambda _: identity._create_self_signed(directory, 'Concurrent fixture'), range(3)))
        certificate = directory / 'certificate.self-signed.pem'
        key = directory / 'key.self-signed.pem'
        self.assertTrue(identity._disk_matches(certificate, key))
        first = (certificate.read_bytes(), key.read_bytes())
        identity._create_self_signed(directory, 'Must not replace')
        self.assertEqual((certificate.read_bytes(), key.read_bytes()), first)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(sorted(path.name for path in directory.iterdir()),
                         ['.identity.lock', 'certificate.self-signed.pem', 'key.self-signed.pem'])

    def test_existing_name_during_publication_preserves_winning_pair(self):
        original_link = os.link

        def competing_link(source, destination):
            if Path(destination).name == 'key.self-signed.pem':
                self.write_pair('.self-signed')
            return original_link(source, destination)

        with patch.object(identity.os, 'link', side_effect=competing_link):
            identity._create_self_signed(self.keys, 'Losing creator')
        self.assertEqual((self.keys / 'certificate.self-signed.pem').read_bytes(), self.certificate)
        self.assertEqual((self.keys / 'key.self-signed.pem').read_bytes(), self.key)

    def test_certificate_link_failure_removes_only_our_published_key(self):
        original_link = os.link

        def failing_link(source, destination):
            if Path(destination).name == 'certificate.self-signed.pem':
                raise OSError('synthetic failure')
            return original_link(source, destination)

        with patch.object(identity.os, 'link', side_effect=failing_link):
            with self.assertRaisesRegex(identity.IdentityError, '^self-signed-create-failed$'):
                identity._create_self_signed(self.keys, 'Failed creator')
        self.assertEqual(sorted(path.name for path in self.keys.iterdir()), ['.identity.lock'])

    @unittest.skipUnless(hasattr(os, 'memfd_create') and sys.platform == 'linux', 'Linux memfd TLS path')
    def test_cached_tls_real_context_and_failure_close_all_memfds(self):
        selected = identity.Identity('cache', certificate_data=self.certificate,
                                     key_data=self.key, record_data=b'synthetic record')
        original = os.memfd_create
        opened = []

        def memfd(*args):
            fd = original(*args)
            opened.append(fd)
            return fd

        with patch.object(identity.os, 'memfd_create', side_effect=memfd), \
                patch.object(identity, '_protect_cache'):
            selected.load_cert_chain(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
            selected._key_data = self.other_key
            with self.assertRaisesRegex(identity.IdentityError, '^identity-tls-load-failed$'):
                selected.load_cert_chain(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        self.assertEqual(len(opened), 4)
        for fd in opened:
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertEqual(list(self.keys.iterdir()), [])


class InvokingUserTests(unittest.TestCase):
    def test_root_priority_and_passwd_paths_ignore_environment_home_and_xdg(self):
        users = {value: types.SimpleNamespace(pw_uid=value, pw_gid=value, pw_dir=f'/home/user{value}')
                 for value in (0, 1001, 1002)}
        with patch.object(identity.os, 'geteuid', return_value=0), \
                patch.object(identity.pwd, 'getpwuid', side_effect=users.__getitem__), \
                patch.dict(os.environ, {'PKEXEC_UID': '1001', 'SUDO_UID': '1002', 'HOME': '/wrong',
                                        'XDG_CONFIG_HOME': '/wrong', 'XDG_RUNTIME_DIR': '/wrong'}, clear=True):
            user, root = identity._invoking_user()
            self.assertEqual(identity._identity_paths(user, root),
                             (Path('/home/user1001/.config/omdrop/settings'), Path('/run/user/1001/omdrop/window')))
            del os.environ['PKEXEC_UID']
            self.assertEqual(identity._invoking_user()[0].pw_uid, 1002)
            del os.environ['SUDO_UID']
            self.assertEqual(identity._invoking_user()[0].pw_uid, 0)

    def test_nonroot_home_comes_from_passwd_not_home_environment(self):
        user = types.SimpleNamespace(pw_uid=1001, pw_gid=1001, pw_dir='/passwd/home')
        with patch.object(identity.os, 'geteuid', return_value=1001), \
                patch.object(identity.os, 'getuid', return_value=1001), \
                patch.object(identity.pwd, 'getpwuid', return_value=user), \
                patch.dict(os.environ, {'HOME': '/wrong'}, clear=True):
            resolved, root = identity._invoking_user()
            self.assertEqual(identity._identity_paths(resolved, root),
                             (Path('/passwd/home/.config/omdrop/settings'), Path('/run/user/1001/omdrop/window')))


class KeyringReadTests(unittest.TestCase):
    def setUp(self):
        self.user = types.SimpleNamespace(pw_uid=1001, pw_gid=1002, pw_dir='/unused')
        protection = patch.object(identity, '_protect_cache')
        protection.start()
        self.addCleanup(protection.stop)

    def result(self, code=0, output=b'', error=b''):
        return subprocess.CompletedProcess([], code, output, error)

    def test_binary_keyctl_pipe_is_returned_intact(self):
        frame = payload(record=b'\x00\xff\n')
        with patch.object(identity, '_keyctl', side_effect=[self.result(output=b'123\n'), self.result(output=frame)]):
            self.assertEqual(identity._read_cache(self.user, False), frame)

    def test_root_fallback_targets_invoking_user_not_roots_user_keyring(self):
        frame = payload()
        rings = []

        def direct(ring):
            rings.append(ring)
            if ring == '@u':
                return payload(fetch_id='b' * 32)  # Wrong user's otherwise valid cache.
            raise identity.IdentityError('cache-unreadable')

        with patch.object(identity, '_read_keyring', side_effect=direct), \
                patch.object(identity.subprocess, 'run', return_value=self.result(output=frame)) as child:
            self.assertEqual(identity._read_cache(self.user, True), frame)
        self.assertEqual(rings, ['%:_uid.1001'])
        self.assertEqual(child.call_args.kwargs['user'], 1001)
        self.assertEqual(child.call_args.kwargs['group'], 1002)
        self.assertEqual(child.call_args.kwargs['extra_groups'], [])

    def test_failed_root_direct_lookup_is_not_treated_as_absent(self):
        with patch.object(identity, '_read_keyring', return_value=None), \
                patch.object(identity.subprocess, 'run', return_value=self.result(output=payload())):
            self.assertEqual(identity._read_cache(self.user, True), payload())

    def test_child_absence_and_refusal_have_distinct_observable_errors(self):
        with patch.object(identity, '_read_keyring', side_effect=identity.IdentityError('cache-unreadable')):
            with patch.object(identity.subprocess, 'run', return_value=self.result(code=3)):
                self.assertIsNone(identity._read_cache(self.user, True))
            with patch.object(identity.subprocess, 'run', return_value=self.result(code=2, error=b'secret diagnostic')):
                with self.assertRaisesRegex(identity.IdentityError, '^cache-unreadable-as-root$'):
                    identity._read_cache(self.user, True)

    def test_keyctl_failures_do_not_emit_diagnostics(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                patch.object(identity, '_keyctl', return_value=self.result(code=1, error=b'private material')):
            with self.assertRaisesRegex(identity.IdentityError, '^cache-unreadable$'):
                identity._read_cache(self.user, False)
        self.assertEqual((output.getvalue(), errors.getvalue()), ('', ''))

    @unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0,
                         'requires Linux root to exercise a real credential drop')
    def test_fallback_really_drops_child_credentials_without_altering_parent(self):
        parent = (os.getuid(), os.getgid(), os.getgroups())
        frame = payload()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            directory.chmod(0o755)
            executable = directory / 'keyctl'
            executable.write_text(
                f'#!{sys.executable}\n'
                'import base64, os, sys\n'
                f'if (os.getuid(), os.getgid(), os.getgroups()) != ({self.user.pw_uid}, {self.user.pw_gid}, []):\n'
                '    sys.exit(9)\n'
                'args = sys.argv[1:]\n'
                'if args[0] == "session":\n'
                '    os.execv(args[2], args[2:])\n'
                'elif args[0] == "link":\n'
                '    sys.exit(0)\n'
                'elif args[0] == "search":\n'
                '    print(123)\n'
                'elif args[0] == "pipe":\n'
                f'    sys.stdout.buffer.write(base64.b64decode({base64.b64encode(frame)!r}))\n'
                'else:\n'
                '    sys.exit(8)\n')
            executable.chmod(0o755)
            with patch.dict(os.environ, {'PATH': str(directory) + os.pathsep + os.environ.get('PATH', '')}), \
                    patch.object(identity, '_read_keyring', side_effect=identity.IdentityError('cache-unreadable')):
                self.assertEqual(identity._read_cache(self.user, True), frame)
        self.assertEqual((os.getuid(), os.getgid(), os.getgroups()), parent)


class CacheProtectionTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'linux', 'Linux process dump protection')
    def test_cache_protection_disables_dumpability_and_core_limits_in_child(self):
        # Protection is deliberately irreversible: isolate it from the suite.
        code = (
            'import ctypes, resource, sys; '
            f'sys.path.insert(0, {str(Path(identity.__file__).parent)!r}); '
            'import awdl_identity; awdl_identity._protect_cache(); '
            'print(resource.getrlimit(resource.RLIMIT_CORE)); '
            'print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))'
        )
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '(0, 0)\n0\n')


if __name__ == '__main__':
    unittest.main()
