"""Explicit unlimited mode changes experiment caps, never native story code."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from story_benchmark.adapters.if_line import IFLineAdapter, IFLineError
from native_shims.if_line.launcher import configure


class IFLineUnlimitedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.config={'budget_mode':'unlimited','max_calls':None,'max_output_tokens':None,
                     'max_input_chars':None,'timeout_seconds':None}

    def tearDown(self): self.tmp.cleanup()

    def test_unlimited_poll_has_no_900_second_deadline(self):
        adapter=IFLineAdapter(self.config)
        task={'id':'native-task','status':'succeeded'}
        with patch.object(adapter,'_request',side_effect=[{**task,'status':'running'},task]), \
             patch('story_benchmark.adapters.if_line.time.monotonic',side_effect=[0,5000]), \
             patch('story_benchmark.adapters.if_line.time.sleep'):
            result=adapter._wait_task({'run_dir':str(self.root/'run')},'bible','native-task')
        self.assertEqual(result,task)
        self.assertIsNone(adapter.timeout)
        self.assertEqual(adapter.api_timeout,60)
        self.assertEqual(adapter.startup_timeout,60)

    def test_default_bounded_poll_still_stops_at_native_adapter_deadline(self):
        adapter=IFLineAdapter({})
        with patch.object(adapter,'_request',return_value={'id':'native-task','status':'running'}), \
             patch('story_benchmark.adapters.if_line.time.monotonic',side_effect=[0,901]):
            with self.assertRaisesRegex(IFLineError,'task_timeout'):
                adapter._wait_task({'run_dir':str(self.root/'run')},'bible','native-task')
        self.assertEqual(adapter.timeout,900)

    def test_launcher_transmits_unlimited_and_clears_stale_caps(self):
        config={**self.config,'repo_path':str(self.root/'source'),'runtime_dir':str(self.root/'runtime'),
                'root_run_id':'unlimited-test','trace_dir':str(self.root/'trace'),
                'model':'fixture','model_base_url':'http://127.0.0.1:9/v1'}
        stale={'BENCH_MAX_CALLS':'1','BENCH_MAX_OUTPUT_TOKENS':'2','BENCH_MAX_INPUT_CHARS':'3',
               'BENCH_MAX_TOTAL_OUTPUT_TOKENS':'4','BENCH_TIMEOUT_SECONDS':'5'}
        with patch.dict(os.environ,stale),patch('story_benchmark.provenance.verify_repository',return_value={'ok':True}):
            configure(config,'entry-check')
            self.assertEqual(os.environ['BENCH_BUDGET_MODE'],'unlimited')
            self.assertFalse(any(k.startswith('BENCH_MAX_') or k=='BENCH_TIMEOUT_SECONDS' for k in os.environ))

    def test_unlimited_launcher_rejects_non_null_or_missing_budget_fields(self):
        config={**self.config,'repo_path':str(self.root/'source'),'runtime_dir':str(self.root/'runtime'),
                'root_run_id':'unlimited-test','trace_dir':str(self.root/'trace'),
                'model':'fixture','model_base_url':'http://127.0.0.1:9/v1'}
        with patch('story_benchmark.provenance.verify_repository',return_value={'ok':True}):
            for field in ('max_calls','max_output_tokens','max_input_chars','timeout_seconds'):
                with self.subTest(field=field):
                    with self.assertRaises(ValueError):configure({**config,field:1},'entry-check')
                    missing=dict(config);del missing[field]
                    with self.assertRaises(ValueError):configure(missing,'entry-check')

    def test_unlimited_preflight_rejects_leftover_cap(self):
        bundle=self.root/'bundle';(bundle/'payloads').mkdir(parents=True)
        (bundle/'shared_task.txt').write_text('共同任务')
        (bundle/'opening.txt').write_text('开头')
        (bundle/'payloads/if_line.json').write_text(json.dumps({'extra_requirements':'共同任务'}))
        with patch('story_benchmark.provenance.verify_repository',return_value={'ok':True,'checks':[],'errors':[]}):
            config={**self.config,'repo_path':str(self.root/'source'),'max_calls':1}
            self.assertFalse(IFLineAdapter(config).preflight(bundle)['ok'])
            config['max_calls']=None
            self.assertTrue(IFLineAdapter(config).preflight(bundle)['ok'])


if __name__=='__main__': unittest.main()
