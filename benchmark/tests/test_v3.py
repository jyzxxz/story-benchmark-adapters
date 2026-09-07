import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from story_benchmark.compiler import compile_case, verify_bundle
from story_benchmark.contracts import canonical_case_hash
from story_benchmark.io import BenchmarkError, atomic_json, sha256
from story_benchmark.result import validate_result, RESULT_KEYS
from story_benchmark.runner import execute_run, resume_export, verify_saved_run
from test_core import RecordedFixtureAdapter

ROOT=Path(__file__).resolve().parents[1]


class Fixture(RecordedFixtureAdapter):
    def __init__(self,system,mode='normal'):
        super().__init__();self.system=system;self.mode=mode

    def generate_first_artifact(self,handle):
        result=super().generate_first_artifact(handle)
        root=Path(handle['run_dir'])
        events=[json.loads(s) for s in (root/'trace/fixture.jsonl').read_text().splitlines()]
        for event in events:
            event['system']=self.system
            event['stage']={'if_line':'branch.candidates.generate','ai4visualnovel':'writer_agent.synthesize_script','infiplot':'writer'}[self.system]
        (root/'trace/fixture.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
        if self.mode=='native_failure':
            class NativeFailure(RuntimeError):code='native_graph_node_count_mismatch'
            raise NativeFailure('expected 12, received 13')
        atomic_json(root/'native/choices.json',[{'id':'a','label':'A','preview':'未选择预览甲'},{'id':'b','label':'B','preview':'未选择预览乙'}])
        return result

    def export_first_artifact(self,handle):
        body=super().export_first_artifact(handle)['segments'] if self.mode=='body' else []
        choices=[{'id':key,'label':key.upper(),'native_source':'native/choices.json','native_pointer':f'/{i}/label'} for i,key in enumerate(('a','b'))]
        previews=[{'segment_id':f'p{i}','choice_id':key,'kind':'branch_preview','speaker':None,'text':text,
                   'native_source':'native/choices.json','native_pointer':f'/{i}/preview'} for i,(key,text) in enumerate((('a','未选择预览甲'),('b','未选择预览乙')))] if self.system=='if_line' else []
        if self.mode=='forged':choices[0]['label']='适配器伪造文字'
        if self.mode=='orphan':previews[0]['choice_id']='unknown'
        if self.mode=='missing_choice':choices=[];previews=[]
        if self.mode=='duplicate_id':choices[1]['id']=choices[0]['id']
        return {'segments':body,'choices':choices,'unselected_previews':previews,'stop_reason':'first_choice' if choices else 'empty',
                'selection_executed':True if self.mode=='selected' else False,
                'generation_issue_codes':['budget_exhausted'] if self.mode=='budget' else []}

    def close(self,handle):
        super().close(handle)
        if self.mode=='cleanup_failure':raise RuntimeError('unfinished writer')


class V3Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for name in ('cases','profiles'):shutil.copytree(ROOT/name,self.root/name)
        self.bundle=self.root/'bundle'
        compile_case(self.root/'cases/CAMPUS-01-V3.json',self.bundle,True)

    def tearDown(self):self.tmp.cleanup()

    def run_fixture(self,system='if_line',mode='normal'):
        return execute_run(system,self.bundle,{'model':'frozen'},self.root/(system+'-'+mode),Fixture(system,mode),mock=True)

    def test_compiled_input_is_frozen_and_legacy_unchanged(self):
        self.assertEqual(verify_bundle(self.bundle)['shared_sha256'],'c8741573a37879a743113da4013765d432d0e3dc9cc22f60697d1883030b9335')
        self.assertEqual(verify_bundle(ROOT/'examples/CAMPUS-01-C1')['shared_sha256'],'290f9d773504447f7230750d91d8b7edffd173745478a05cbb5d0b990fd43d15')
        verify_bundle(ROOT/'examples/CAMPUS-01')

    def test_case_change_with_updated_file_hash_cannot_change_scope(self):
        case=json.loads((self.bundle/'case.json').read_text());case['scope_map']['tamper']='新指令'
        atomic_json(self.bundle/'case.json',case)
        manifest=json.loads((self.bundle/'manifest.json').read_text())
        manifest['files']['case.json']=sha256((self.bundle/'case.json').read_bytes())
        manifest['canonical_case_sha256']=canonical_case_hash(case)
        atomic_json(self.bundle/'manifest.json',manifest)
        with self.assertRaisesRegex(BenchmarkError,'rendered_task'):verify_bundle(self.bundle)

    def test_three_systems_same_envelope_and_input_different_native_output(self):
        results=[self.run_fixture(system) for system in ('if_line','ai4visualnovel','infiplot')]
        for result in results:
            self.assertEqual(set(result),RESULT_KEYS);validate_result(result)
            self.assertEqual(result['outcome'],'completed',result['errors'])
            self.assertEqual(result['adapter_status'],'passed')
            self.assertEqual(result['scope']['selection_executed'],False)
            self.assertEqual(result['content']['body'],[])
            self.assertEqual(len(result['content']['choices']),2)
        self.assertEqual(len({r['input']['shared_text'] for r in results}),1)
        self.assertEqual(len(results[0]['content']['previews']),2)
        self.assertEqual(results[1]['content']['previews'],[])

    def test_native_failure_uniform_and_resume_never_resends(self):
        result=self.run_fixture('ai4visualnovel','native_failure')
        self.assertEqual(result['outcome'],'native_error',result)
        self.assertEqual(result['adapter_status'],'passed')
        self.assertEqual(result['content']['body'],[])
        self.assertEqual(result['scope']['selection_executed'],None)
        run=self.root/'ai4visualnovel-native_failure'
        self.assertEqual(resume_export(run),result)
        verify_saved_run(run)

    def test_invalid_mapping_does_not_pass(self):
        result=self.run_fixture(mode='forged')
        self.assertEqual(result['outcome'],'adapter_error')
        self.assertEqual(result['adapter_status'],'failed')

    def test_orphan_preview_keeps_failure_envelope(self):
        result=self.run_fixture(mode='orphan')
        self.assertEqual(result['outcome'],'adapter_error');validate_result(result)

    def test_optional_body_retained_when_present(self):
        result=self.run_fixture(mode='body')
        self.assertEqual(result['content']['body'][0]['text'],'模拟正文')
        self.assertEqual(result['outcome'],'completed')

    def test_budget_or_missing_boundary_not_success(self):
        self.assertEqual(self.run_fixture(mode='budget')['outcome'],'budget_exhausted')
        self.assertEqual(self.run_fixture(mode='missing_choice')['outcome'],'native_output_incomplete')

    def test_native_duplicate_ids_keep_source_labels_and_explicit_failure(self):
        result=self.run_fixture('infiplot','duplicate_id')
        self.assertEqual(result['outcome'],'native_output_incomplete')
        self.assertEqual(result['adapter_status'],'passed')
        self.assertEqual([c['label'] for c in result['content']['choices']],['A','B'])
        self.assertIn('native_duplicate_choice_id',result['scope']['issues'])

    def test_cleanup_failure_not_passed(self):
        result=self.run_fixture(mode='cleanup_failure')
        self.assertEqual(result['outcome'],'adapter_error')
        self.assertIsNone(result['provenance']['native_source_unchanged'])

    def test_selection_cannot_silently_be_reported_false(self):
        result=self.run_fixture(mode='selected')
        self.assertNotEqual(result['outcome'],'completed')
        self.assertTrue(result['scope']['selection_executed'])

    def test_rejection_same_shape_and_existing_evidence_unchanged(self):
        result=self.run_fixture()
        root=self.root/'if_line-normal';before=(root/'manifest.json').read_bytes()
        rejected=execute_run('if_line',self.bundle,{},root)
        self.assertEqual(rejected['outcome'],'input_rejected');validate_result(rejected)
        self.assertEqual((root/'manifest.json').read_bytes(),before)
        rejected=execute_run('if_line',self.bundle,{},self.root/'rejected')
        self.assertEqual(rejected['outcome'],'input_rejected');validate_result(rejected)
        self.assertEqual(set(rejected),set(result))


if __name__=='__main__':unittest.main()
