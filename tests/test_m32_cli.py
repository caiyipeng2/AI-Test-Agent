import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai_test_agent.__main__ import main
from m32_support import provider_recording


class M32CliTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'.local'/('m32-cli-'+uuid4().hex)
        self.root.mkdir(parents=True)
        self.source=self.root/'recording.json'
        self.output=self.root/'result.json'

    def tearDown(self):
        shutil.rmtree(self.root)

    def prepare(self,mode='assembly_probe'):
        data=provider_recording(self.root,mode)
        self.source.write_text(json.dumps(data),encoding='utf-8')
        return data

    def invoke(self,target=None):
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer),contextlib.redirect_stderr(io.StringIO()), \
             patch('ai_test_agent.android.AndroidDevice',side_effect=AssertionError('no device actions')):
            try:
                code=main(['inspect-provider-m3',str(self.source),'--output',str(target or self.output)])
            except SystemExit as error:
                code=error.code
        return code,json.loads(buffer.getvalue()) if buffer.getvalue() else {}

    def test_assembly_probe_is_explicitly_not_live_provider(self):
        self.prepare()
        code,result=self.invoke()
        self.assertEqual(0,code,result)
        self.assertEqual('compiled_assembly_identity',result['scope'])
        self.assertEqual('synthetic',result['evidence_mode'])
        self.assertFalse(result['live_provider'])
        self.assertEqual('NOT_EVALUATED',result['business_status'])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),result['input_sha256'])
        self.assertIn('NOT_EVALUATED',self.output.with_suffix('.md').read_text(encoding='utf-8'))

    def test_recorded_device_proof_is_evidence_review_only(self):
        self.prepare('device')
        code,result=self.invoke()
        self.assertEqual(0,code,result)
        self.assertEqual('provider_runtime_identity',result['scope'])
        self.assertFalse(result['live_provider'])

    def test_tampered_dll_is_blocked(self):
        data=self.prepare()
        (self.root/data['dll_path']).write_bytes(b'tampered')
        code,result=self.invoke()
        self.assertEqual(2,code)
        self.assertEqual('BLOCKED',result.get('status'),result)

    def test_missing_provider_capture_bytes_is_blocked(self):
        data=self.prepare('device')
        (self.root/data['transport']['log_evidence']['path']).unlink()
        code,result=self.invoke()
        self.assertEqual(2,code)
        self.assertEqual('BLOCKED',result.get('status'),result)

    def test_invalid_json_and_duplicate_keys_are_structured(self):
        for raw in ('{','{"schema_version":1,"schema_version":1}'):
            self.source.write_text(raw,encoding='utf-8')
            code,result=self.invoke()
            self.assertEqual(2,code)
            self.assertEqual('INVALID_INPUT',result.get('category'),result)

    def test_input_dll_and_capture_log_cannot_be_output_hardlinks(self):
        data=self.prepare('device')
        protected=[self.source,self.root/data['dll_path'],self.root/data['transport']['log_evidence']['path']]
        for source in protected:
            original=source.read_bytes()
            for target in (self.output,self.output.with_suffix('.md')):
                with self.subTest(source=source.name,target=target.name):
                    if target.exists(): target.unlink()
                    target.hardlink_to(source)
                    code,result=self.invoke()
                    self.assertEqual(2,code,result)
                    self.assertIn('overwrite',str(result).lower())
                    self.assertEqual(original,source.read_bytes())
                    target.unlink()

    def test_invalid_m2_proof_relative_evidence_cannot_be_direct_output(self):
        proof_dir=self.root/'proofs'
        proof_dir.mkdir()
        for status in ('PASS','BLOCKED'):
            for declared in ('missing.log','invalid\0.log'):
                for suffix in ('.json','.md'):
                    with self.subTest(status=status,declared=repr(declared),suffix=suffix):
                        self._check_relative_evidence_output(proof_dir,status,declared,suffix)

    def _check_relative_evidence_output(self,proof_dir,status,declared,suffix):
        data=self.prepare('device')
        proof=json.loads((self.root/data['transport']['installed_proof']).read_text(encoding='utf-8'))
        evidence=proof_dir/('preserved'+suffix)
        original=b'original captured evidence must survive a blocked review\n'
        evidence.write_bytes(original)
        proof['status']=status
        proof['evidence'][0].update(path=str(proof_dir/declared),relative_path=evidence.name)
        proof_path=proof_dir/'m2-result.json'
        proof_path.write_text(json.dumps(proof),encoding='utf-8')
        data['transport']['installed_proof']=str(proof_path.relative_to(self.root))
        self.source.write_text(json.dumps(data),encoding='utf-8')
        code,result=self.invoke(proof_dir/'preserved.json')
        self.assertEqual(2,code,result)
        self.assertIn('overwrite',str(result).lower())
        self.assertEqual(original,evidence.read_bytes())

    def test_invalid_m2_proof_both_evidence_paths_cannot_be_output_hardlinks(self):
        proof_dir=self.root/'proofs'
        proof_dir.mkdir()
        for field in ('path','relative_path'):
            for suffix in ('.json','.md'):
                with self.subTest(field=field,suffix=suffix):
                    data=self.prepare('device')
                    proof=json.loads((self.root/data['transport']['installed_proof']).read_text(encoding='utf-8'))
                    evidence=proof_dir/'original.log'
                    original=b'original captured evidence must survive an aliased output\n'
                    evidence.write_bytes(original)
                    proof['status']='BLOCKED'
                    proof['evidence'][0].update(
                        path=str(evidence if field=='path' else proof_dir/'missing-declared.log'),
                        relative_path=evidence.name if field=='relative_path' else 'missing-relative.log')
                    proof_path=proof_dir/'m2-result.json'
                    proof_path.write_text(json.dumps(proof),encoding='utf-8')
                    data['transport']['installed_proof']=str(proof_path.relative_to(self.root))
                    self.source.write_text(json.dumps(data),encoding='utf-8')
                    target=self.output.with_suffix(suffix)
                    if target.exists(): target.unlink()
                    target.hardlink_to(evidence)
                    try:
                        code,result=self.invoke()
                        self.assertEqual(2,code,result)
                        self.assertIn('overwrite',str(result).lower())
                        self.assertEqual(original,evidence.read_bytes())
                        self.assertTrue(target.samefile(evidence))
                    finally:
                        target.unlink()

    def test_oversized_m2_evidence_list_refuses_export_before_last_item_is_overwritten(self):
        for suffix in ('.json','.md'):
            with self.subTest(suffix=suffix):
                data=self.prepare('device')
                proof_path=self.root/data['transport']['installed_proof']
                proof=json.loads(proof_path.read_text(encoding='utf-8'))
                evidence=self.output.with_suffix(suffix)
                original=b'last referenced evidence must survive an over-budget proof\n'
                evidence.write_bytes(original)
                final=dict(proof['evidence'][0],path=str(evidence),relative_path=evidence.name,
                           bytes=len(original),sha256=hashlib.sha256(original).hexdigest())
                proof['evidence']=[dict(proof['evidence'][0]) for _ in range(4096)]+[final]
                raw=json.dumps(proof).encode('utf-8')
                self.assertEqual(4097,len(proof['evidence']))
                self.assertLess(len(raw),8*1024*1024)
                proof_path.write_bytes(raw)
                other=self.output.with_suffix('.md' if suffix=='.json' else '.json')
                if other.exists(): other.unlink()
                code,result=self.invoke()
                self.assertEqual(2,code,result)
                self.assertEqual('INVALID_INPUT',result.get('category'),result)
                self.assertIn('4096',str(result))
                self.assertEqual(original,evidence.read_bytes())
                self.assertFalse(other.exists())

    def test_oversized_m2_proof_bytes_refuse_export(self):
        data=self.prepare('device')
        proof_path=self.root/data['transport']['installed_proof']
        raw=proof_path.read_bytes()
        proof_path.write_bytes(raw+b' '*(8*1024*1024+1-len(raw)))
        code,result=self.invoke()
        self.assertEqual(2,code,result)
        self.assertEqual('INVALID_INPUT',result.get('category'),result)
        self.assertIn('8 MiB',str(result))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix('.md').exists())

    def test_unresolvable_evidence_path_does_not_hide_later_declared_files(self):
        data=self.prepare('device')
        proof_path=self.root/data['transport']['installed_proof']
        proof=json.loads(proof_path.read_text(encoding='utf-8'))
        evidence=self.root/'preserved.md'
        original=b'a preceding broken declaration must not hide this evidence\n'
        evidence.write_bytes(original)
        proof['status']='BLOCKED'
        proof['evidence']=[{'path':'unresolvable.log'},
                           dict(proof['evidence'][0],path=str(evidence),relative_path=evidence.name)]
        proof_path.write_text(json.dumps(proof),encoding='utf-8')
        for error_type in (OSError,ValueError,RuntimeError):
            with self.subTest(error_type=error_type.__name__):
                resolve=Path.resolve
                def reject_one_path(path,*args,**kwargs):
                    if path.name=='unresolvable.log':
                        raise error_type('Simulated filesystem path resolution failure')
                    return resolve(path,*args,**kwargs)
                with patch.object(Path,'resolve',reject_one_path):
                    code,result=self.invoke(self.root/'preserved.json')
                self.assertEqual(2,code,result)
                self.assertIn('overwrite',str(result).lower())
                self.assertEqual(original,evidence.read_bytes())
                self.assertFalse((self.root/'preserved.json').exists())

    def test_proofs_at_protection_budget_limits_can_still_export(self):
        for boundary in ('bytes','items'):
            with self.subTest(boundary=boundary):
                data=self.prepare('device')
                proof_path=self.root/data['transport']['installed_proof']
                raw=proof_path.read_bytes()
                if boundary=='bytes':
                    proof_path.write_bytes(raw+b' '*(8*1024*1024-len(raw)))
                else:
                    proof=json.loads(raw)
                    proof['evidence']=[dict(proof['evidence'][0]) for _ in range(4096)]
                    proof_path.write_text(json.dumps(proof),encoding='utf-8')
                code,result=self.invoke()
                self.assertEqual(0 if boundary=='bytes' else 2,code,result)
                self.assertEqual('PROVIDER_IDENTITY_CONFIRMED' if boundary=='bytes' else
                                 'PROVIDER_EVIDENCE_INVALID',result.get('category'),result)
                self.assertTrue(self.output.is_file())
                self.assertTrue(self.output.with_suffix('.md').is_file())

    def test_repeated_review_has_fixed_input_digest_and_unique_assessment(self):
        self.prepare()
        a,first=self.invoke()
        b,second=self.invoke(self.root/'second.json')
        self.assertEqual(0,a,first)
        self.assertEqual(0,b,second)
        self.assertEqual(first['input_sha256'],second['input_sha256'])
        self.assertNotEqual(first['assessment_id'],second['assessment_id'])

    def test_output_suffix_is_explicit(self):
        self.prepare()
        code,result=self.invoke(self.root/'result.txt')
        self.assertEqual(2,code)
        self.assertIn('.json',str(result))
