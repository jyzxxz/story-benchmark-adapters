import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from story_benchmark.adapters.if_line import IFLineAdapter, IFLineError, export_script_ir

REPO = Path(__file__).resolve().parents[2] / 'if_line'


def script():
    return {'id':'script-r','script_json':{'schema_version':'script-ir-v3','chapter_revision_id':'chapter-r',
        'paragraphs':[{'paragraph_id':'p1','order_index':0,'kind':'narration','text':'雨下着。',
        'source_text':'雨下着。','source_start':0,'source_end':4}]}}


class Native:
    def __init__(self):
        self.calls=[]
        self.heads={}
        self.shared='固定共同任务'
        self.fail=None
    def __call__(self, method, path, body, headers):
        self.calls.append((method,path,body,headers))
        if self.fail == path:
            raise IFLineError('delivery_unknown')
        if method=='POST' and path.endswith('/projects'):
            return {'id':1,'root_story_path_id':'root',**body}
        stages={'bible-generations':'bible','outline-generations':'outline','generations':'chapter','script-generations':'script'}
        if method=='POST':
            return {'task_id':stages[path.split('/')[-1]]+'-t'}
        if '/tasks/' in path:
            stage=path.split('/')[-1].split('-')[0]
            key={'bible':'bible_revision_id','outline':'outline_revision_id','chapter':'chapter_revision_id','script':'chapter_script_revision_id'}[stage]
            return {'id':stage+'-t','status':'succeeded','source_refs':{'project_snapshot':{'extra_requirements':self.shared}},
                    'result_refs':{key:stage+'-r'}}
        if path.endswith('head'):
            if method=='PUT':
                assert headers['If-Match']=='"1"'
                self.heads[path]=body['revision_id']
            return {'revision_id':self.heads.get(path),'lock_version':1}
        if path.endswith('bible-revisions'):
            return [{'id':'wrong-latest'},{'id':'bible-r'}]
        if path.endswith('outline-revisions'):
            return [{'id':'outline-r','chapters':[{'story_path_chapter_id':'placement'}]}]
        if path.endswith('/chapters'):
            return [{'id':'placement','display_index':1}]
        if path.endswith('/chapter-r'):
            return {'id':'chapter-r','content':'雨下着。'}
        if path.endswith('script-revisions'):
            return [{'id':'wrong-latest'},script()]
        raise AssertionError(path)


class IFLineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.bundle=self.root/'bundle'
        (self.bundle/'payloads').mkdir(parents=True)
        (self.bundle/'shared_task.txt').write_text('固定共同任务')
        (self.bundle/'opening.txt').write_text('固定开头')
        self.payload={'title':'题目','story_start':'','story_end':'','extra_requirements':'固定共同任务'}
        (self.bundle/'payloads/if_line.json').write_text(json.dumps(self.payload))
        self.native=Native()
        self.config={'repo_dir':str(REPO),'root_run_id':'test-root','live':False,'transport':self.native,
                     'max_calls':10,'max_output_tokens':32768,'max_input_chars':50000}
        self.adapter=IFLineAdapter(self.config)
        verifier=patch('story_benchmark.provenance.verify_repository',return_value={'ok':True,'checks':['mock_source_fixture'],'errors':[]})
        verifier.start()
        self.addCleanup(verifier.stop)
    def tearDown(self):
        self.tmp.cleanup()
    def test_full_native_chain_result_refs_and_resume(self):
        handle=self.adapter.prepare(self.bundle,self.root/'run')
        result=self.adapter.generate_first_artifact(handle)
        self.assertEqual(result['script_revision_id'],'script-r')
        self.assertEqual(self.adapter.export_first_artifact(handle)['segments'][0]['text'],'雨下着。')
        posts=[c for c in self.native.calls if c[0]=='POST']
        self.assertEqual(len(posts),5)
        self.assertNotIn('instructions',posts[1][2])
        self.assertEqual(posts[1][2]['parameters']['benchmark_input_mode'],'shared_task')
        self.assertEqual(posts[2][2]['chapter_count'],13)
        resumed=self.adapter.prepare(self.bundle,self.root/'run')
        self.adapter.generate_first_artifact(resumed)
        self.assertEqual(len([c for c in self.native.calls if c[0]=='POST']),5)
    def test_ambiguous_project_create_never_repeated(self):
        handle=self.adapter.prepare(self.bundle,self.root/'run')
        self.native.fail='/api/projects'
        with self.assertRaises(IFLineError): self.adapter.generate_first_artifact(handle)
        self.native.fail=None
        with self.assertRaisesRegex(IFLineError,'delivery_unknown'): self.adapter.generate_first_artifact(handle)
        self.assertEqual(len(self.native.calls),1)
    def test_idempotent_generation_resume_reuses_key(self):
        handle=self.adapter.prepare(self.bundle,self.root/'run')
        self.native.fail='/api/projects/1/bible-generations'
        with self.assertRaises(IFLineError): self.adapter.generate_first_artifact(handle)
        self.native.fail=None
        self.adapter.generate_first_artifact(handle)
        keys=[c[3]['Idempotency-Key'] for c in self.native.calls if c[1].endswith('bible-generations')]
        self.assertEqual(len(keys),2)
        self.assertEqual(keys[0],keys[1])
    def test_missing_input_preflight(self):
        (self.bundle/'opening.txt').unlink()
        self.assertIn('missing_opening',self.adapter.preflight(self.bundle)['errors'])
        self.assertEqual(self.native.calls,[])
    def test_payload_mismatch_preflight(self):
        self.payload['extra_requirements']+='改'
        (self.bundle/'payloads/if_line.json').write_text(json.dumps(self.payload))
        self.assertIn('payload_shared_mismatch',self.adapter.preflight(self.bundle)['errors'])
    def test_empty_and_missing_script_are_distinct(self):
        with self.assertRaisesRegex(IFLineError,'missing_script_artifact'):
            self.adapter.export_first_artifact({'run_dir':str(self.root)})
        self.assertEqual(export_script_ir({'script_json':{'schema_version':'script-ir-v3','paragraphs':[]}})['generation_issue_codes'],['empty_prose'])
    def test_source_mismatch_fails(self):
        with self.assertRaisesRegex(IFLineError,'source_mapping_mismatch'):
            export_script_ir(script(),{'id':'chapter-r','content':'天晴着。'})
    def test_choices_not_invented(self):
        result=export_script_ir(script(),{'id':'chapter-r','content':'雨下着。'})
        self.assertEqual(result['choices'],[])
        self.assertEqual(result['choice_support'],'not_in_script_ir_v3')
    def test_explicit_total_chapter_count_is_preserved(self):
        self.adapter.config['chapter_count']=7
        handle=self.adapter.prepare(self.bundle,self.root/'run')
        self.adapter.generate_first_artifact(handle)
        outlines=[c for c in self.native.calls if c[0]=='POST' and c[1].endswith('outline-generations')]
        self.assertEqual(outlines[0][2]['chapter_count'],7)
        generated=[c for c in self.native.calls if c[0]=='POST' and c[1].startswith('/api/path-chapters/')]
        self.assertEqual(len(generated),1)

if __name__=='__main__': unittest.main()
