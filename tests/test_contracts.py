import importlib
import struct
import tempfile
import unittest
import zlib
from pathlib import Path


def png_bytes(width=1, height=1):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    pixels = b''.join(b'\x00' + b'\x00\x00\x00' * width for _ in range(height))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


class ContractTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('ai_test_agent.contracts')
        except ModuleNotFoundError:
            self.fail('contracts module has not been implemented')

    def test_ids_have_prefix_and_are_unique(self):
        values = {self.api.make_id('run') for _ in range(100)}
        self.assertEqual(100, len(values))
        self.assertTrue(all(value.startswith('run_') for value in values))

    def test_case_requires_approved_independent_expectation(self):
        case = dict(case_id='case_1', requirement_id='req_1', source_ref='manual-contract-v1', approved=True,
                    steps=[{'action': 'launch'}], expectation={'target_id': 'candidate', 'window_id': 'UIUpgradeWindow',
                    'level_delta': 1, 'cost': '10', 'non_upgrade_delta': '0', 'approved': True})
        self.assertEqual([], self.api.validate_case(case))
        for key in case:
            broken = dict(case)
            broken.pop(key)
            self.assertTrue(self.api.validate_case(broken), key)
        case['approved'] = 'true'
        self.assertTrue(self.api.validate_case(case))

    def test_case_rejects_empty_steps_and_expected_result(self):
        case = dict(case_id='case_1', requirement_id='req_1', source_ref='manual-v1', approved=True,
                    steps=[], expectation={})
        self.assertGreaterEqual(len(self.api.validate_case(case)), 2)

    def test_png_evidence_has_real_dimensions_and_hash(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shot.png'
            raw = png_bytes(2, 3)
            path.write_bytes(raw)
            evidence = self.api.evidence_for_png(path)
            self.assertEqual((2, 3, len(raw)), (evidence['width'], evidence['height'], evidence['bytes']))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), evidence['sha256'])
            self.assertEqual(str(path.resolve()), evidence['path'])

    def test_png_evidence_receives_nonempty_reference_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shot.png'
            path.write_bytes(png_bytes())
            self.assertTrue(self.api.evidence_for_png(path).get('evidence_id'))

    def test_unknown_upgrade_expectation_is_not_execution_ready(self):
        case = dict(case_id='case_1', requirement_id='req_1', source_ref='manual-v1', approved=True,
                    steps=[{'action': 'observe'}], expectation={'target_id': 'candidate', 'window_id': 'UIUpgradeWindow',
                    'level_delta': 1, 'cost': '10', 'non_upgrade_delta': '0', 'approved': True})
        for field in ('target_id', 'window_id', 'cost', 'non_upgrade_delta'):
            expected = dict(case['expectation'])
            case['expectation'][field] = None
            self.assertTrue(self.api.validate_case(case), field)
            case['expectation'] = expected

    def test_unapproved_template_can_be_valid_structure_but_not_executable(self):
        import inspect
        case = dict(case_id='case_template', requirement_id='req_template', source_ref='manual-template', approved=False,
                    steps=[{'action': 'observe'}], expectation={'target_id': None, 'cost': None, 'non_upgrade_delta': None})
        self.assertIn('execution_ready', inspect.signature(self.api.validate_case).parameters)
        self.assertEqual([], self.api.validate_case(case, execution_ready=False))
        self.assertTrue(self.api.validate_case(case))

    def test_png_rejects_signature_only_truncation_crc_and_trailing_data(self):
        valid = png_bytes()
        corrupt = bytearray(valid)
        corrupt[29] ^= 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.png'
            for data in (b'not png', valid[:20], bytes(corrupt), valid + b'untrusted'):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    self.api.evidence_for_png(path)

    def test_png_rejects_invalid_compressed_pixels_even_with_valid_crc(self):
        raw = png_bytes()
        pos = raw.index(b'IDAT')
        size = struct.unpack('>I', raw[pos-4:pos])[0]
        invalid = b'bad' + b'\x00' * (size-3)
        broken = raw[:pos+4] + invalid + struct.pack('>I', zlib.crc32(b'IDAT' + invalid) & 0xffffffff) + raw[pos+4+size+4:]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.png'
            path.write_bytes(broken)
            with self.assertRaises(ValueError):
                self.api.evidence_for_png(path)


if __name__ == '__main__':
    unittest.main()
