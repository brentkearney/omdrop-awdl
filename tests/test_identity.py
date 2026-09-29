"""Frozen cross-repository identity vectors and binary framing boundaries."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'userspace'))
import awdl_identity as identity


VECTORS = Path(__file__).with_name('identity-vectors.json')
VECTOR_SHA256 = '7c4df91563d32240ad1dc17c18415cde012b373b9a4afc23f0387f384daed1b9'


class IdentityVectorsTests(unittest.TestCase):
    def test_frozen_contract_vectors(self):
        raw = VECTORS.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), VECTOR_SHA256)
        vectors = json.loads(raw)
        for vector in vectors['selection']:
            with self.subTest(group='selection', name=vector['name']):
                self.assertEqual(identity.select_identity(**vector['input']), vector['expect'])
        for group, parser, field in (
                ('payloads', identity.parse_payload, 'payload_b64'),
                ('window_files', identity.parse_window, 'text'),
                ('settings_files', identity.parse_settings, 'text')):
            for vector in vectors[group]:
                with self.subTest(group=group, name=vector['name']):
                    value = vector[field]
                    if group == 'payloads':
                        value = base64.b64decode(value)
                    self.assertEqual(parser(value), vector['expect'])


class PayloadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.header = dict(certificate=1, key=1, record=1, fetch_id='a' * 32,
                           fetched_at=0, period_ends=0, hard_expiry=86400)

    def frame(self, header=None, body=b'CKR'):
        return b'OMDROP-IDENTITY 1\n' + json.dumps(
            self.header if header is None else header).encode() + b'\n' + body

    def test_duplicate_header_keys_are_not_last_wins(self):
        frame = self.frame().replace(b'{', b'{"key":1,', 1)
        self.assertEqual(identity.parse_payload(frame), 'malformed')

    def test_integer_fields_reject_booleans_floats_and_strings(self):
        for field in ('certificate', 'key', 'record', 'fetched_at', 'period_ends', 'hard_expiry'):
            for value in (True, False, 1.0, '1', None):
                with self.subTest(field=field, value=value):
                    self.assertEqual(identity.parse_payload(
                        self.frame({**self.header, field: value})), 'malformed')

    def test_binary_sections_are_not_decoded_or_searched_for_newlines(self):
        self.header.update(certificate=3, key=2, record=4)
        result = identity.parse_payload(self.frame(body=b'\xff\nC\x00K\x00\n\xffR'))
        self.assertEqual(base64.b64decode(result['certificate_b64']), b'\xff\nC')
        self.assertEqual(base64.b64decode(result['key_b64']), b'\x00K')
        self.assertEqual(base64.b64decode(result['record_b64']), b'\x00\n\xffR')

    def test_exact_kernel_limit_and_next_byte(self):
        self.header.update(certificate=16000, key=16000, record=1)
        # Header size can change when the record length changes; settle it before framing.
        for _ in range(3):
            overhead = len(self.frame(body=b''))
            self.header['record'] = 32767 - overhead - 32000
        body = b'C' * 16000 + b'K' * 16000 + b'R' * self.header['record']
        frame = self.frame(body=body)
        self.assertEqual(len(frame), 32767)
        self.assertIsInstance(identity.parse_payload(frame), dict)
        self.header['record'] += 1
        self.assertEqual(identity.parse_payload(self.frame(body=body + b'R')), 'malformed')

    def test_invalid_json_encoding_and_non_object_headers(self):
        for header in (b'\xff', b'null', b'[]', b'1', b'"text"', b'{'):
            with self.subTest(header=header):
                self.assertEqual(identity.parse_payload(b'OMDROP-IDENTITY 1\n' + header + b'\nCKR'),
                                 'malformed')

    def test_time_order_and_inclusive_lifetime_boundary(self):
        self.assertIsInstance(identity.parse_payload(self.frame()), dict)
        for changes in ({'hard_expiry': 86401}, {'period_ends': -1}, {'period_ends': 86401}):
            with self.subTest(changes=changes):
                self.assertEqual(identity.parse_payload(self.frame({**self.header, **changes})), 'malformed')


if __name__ == '__main__':
    unittest.main()
