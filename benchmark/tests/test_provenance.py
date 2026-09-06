import copy
import json
from pathlib import Path
import tempfile
import unittest
from story_benchmark.io import atomic_json, sha256, BenchmarkError
from story_benchmark.provenance import verify_repository
from story_benchmark.experiment import load_experiment


class SourceLockTest(unittest.TestCase):
    def test_snapshot_hash_and_extra_code_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'native';repo.mkdir();(repo/'main.py').write_text('x=1\n')
            lock={'systems':{'sample':{'base_commit':'abc','adapted_commit':'abc','files':{'main.py':sha256((repo/'main.py').read_bytes())}}}}
            atomic_json(root/'lock.json',lock);config={'source_lock':str(root/'lock.json')}
            self.assertTrue(verify_repository(repo,'abc',config)['ok'])
            (repo/'extra_prompt.py').write_text('x=2')
            self.assertFalse(verify_repository(repo,'abc',config)['ok'])
            (repo/'extra_prompt.py').unlink();(repo/'main.py').write_text('x=3')
            self.assertFalse(verify_repository(repo,'abc',config)['ok'])

    def test_case_different_budget_not_permitted(self):
        common={'live':False,'model':None,'model_base_url':None,'timeout_seconds':None,'max_calls':None,'max_output_tokens':None,'max_input_chars':None,'allow_pilot':True}
        systems={s:{'repo_path':'../systems/'+s} for s in ('if_line','infiplot','ai4visualnovel')}
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'experiment.json';atomic_json(path,{'schema_version':'2.1','common':common,'systems':systems})
            self.assertEqual(len(load_experiment(path)),3)
            systems['if_line']['max_calls']=1000
            atomic_json(path,{'schema_version':'2.1','common':common,'systems':systems})
            with self.assertRaisesRegex(BenchmarkError,'per_system_override'):load_experiment(path)

    def test_modified_baseline_revision_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'main.py').write_text('original')
            result=verify_repository(root,'baseline',{'expected_commit':'adapted'})
            self.assertFalse(result['ok'])
            self.assertIn('modified_native_revision_forbidden',result['errors'][0])


if __name__=='__main__':unittest.main()
