"""Offline tests. Native orchestration is mocked; never sends a model request."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import eval30
import experiment as exp
from setup_experiment import endpoint
from story_benchmark.compiler import compile_case, verify_bundle


class SuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work=tempfile.TemporaryDirectory()
        cls.suite=Path(cls.work.name)/'suite'
        cls.manifest=eval30.expand_legacy_suite(cls.suite)
        cls.catalog,cls.briefs,cls.prefix=eval30.catalog_sources()
    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()
    def test_30_compiled_inputs_match_supplied_hashes(self):
        self.assertEqual(len(self.manifest['cases']),30)
        for row in self.catalog['cases']:
            result=verify_bundle(self.suite/'compiled'/row['case_id'])
            self.assertEqual(result['shared_sha256'],row['shared_sha256'])
    def test_all_three_native_payloads_equal(self):
        for row in self.catalog['cases']:
            bundle=self.suite/'compiled'/row['case_id']
            shared=(bundle/'shared_task.txt').read_text()
            self.assertEqual(json.loads((bundle/'payloads/if_line.json').read_text())['extra_requirements'],shared)
            self.assertEqual(json.loads((bundle/'payloads/infiplot.json').read_text())['worldSetting'],shared)
            self.assertEqual((bundle/'payloads/requirements.txt').read_text(),shared)
    def test_briefs_are_verbatim(self):
        for row in self.catalog['cases']:
            self.assertEqual((self.suite/'briefs'/f'{row["source_prompt_id"]}.txt').read_bytes(),self.briefs[row['source_prompt_id']].encode())
    def test_all_inputs_remain_pilot(self):
        self.assertTrue(all(row['review_status']=='pilot' for row in self.manifest['cases']))
        self.assertEqual(self.manifest['paid_model_calls'],0)
        self.assertEqual(self.manifest['native_integration'],'not_run')
    def test_refuses_overwrite(self):
        with self.assertRaises(FileExistsError): eval30.expand_suite(self.suite)
    def test_prefix_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)
            shutil.copytree(eval30.CATALOG.parent,dest,dirs_exist_ok=True)
            (dest/'prefix.txt').write_bytes(self.prefix+b'changed')
            with self.assertRaises(ValueError): eval30.catalog_sources(dest/'catalog.json')
    def test_opening_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder);shutil.copytree(eval30.CATALOG.parent,dest,dirs_exist_ok=True)
            values=json.loads((dest/'campus.json').read_text());values[0]['opening']+='changed'
            (dest/'campus.json').write_text(json.dumps(values,ensure_ascii=False))
            with self.assertRaises(ValueError): eval30.catalog_sources(dest/'catalog.json')
    def test_native_compile_rejects_missing_review(self):
        with tempfile.TemporaryDirectory() as folder:
            case=self.suite/self.manifest['cases'][0]['case_file']
            with self.assertRaises(ValueError): compile_case(case,Path(folder)/'out',allow_pilot=False)
    def test_approval_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,str(ROOT/'tools/approve_eval30.py'),'--suite-root',str(self.suite),'--out',str(Path(folder)/'approved'),'--reviewer','test-fixture-reviewer'],capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((Path(folder)/'approved').exists())
    def test_hash_bound_approved_fixture_compiles_and_tamper_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);approved=folder/'approved'
            result=subprocess.run([sys.executable,str(ROOT/'tools/approve_eval30.py'),'--suite-root',str(self.suite),'--out',str(approved),'--reviewer','test-fixture-reviewer','--confirm-reviewed'],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            row=self.manifest['cases'][0];case=approved/row['case_file']
            compile_case(case,folder/'compiled',allow_pilot=False)
            value=json.loads(case.read_text());value['scope_map']['test']='unapproved change';case.write_text(json.dumps(value,ensure_ascii=False))
            with self.assertRaises(ValueError): compile_case(case,folder/'tampered',allow_pilot=False)
    def test_full_preset_is_270_attempts(self):
        ids,repeat=exp.selection('full',None,None,self.catalog['cases'])
        self.assertEqual(len(ids)*repeat*3,270)
    def test_genre_preset_is_six_cases(self):
        ids,repeat=exp.selection('genres',None,None,self.catalog['cases'])
        self.assertEqual(len(ids),6);self.assertEqual(repeat,1)
    def test_duplicate_and_unknown_cases_rejected(self):
        for ids in (['CAMPUS-01','CAMPUS-01'],['NO-SUCH-ID']):
            with self.assertRaises(ValueError): exp.selection('quick',ids,None,self.catalog['cases'])
    def test_complete_prompt_export(self):
        text=(self.suite/'PROMPTS_30_COMPILED.md').read_text()
        self.assertEqual(text.count('<<<SHARED_TASK_V2_BEGIN>>>'),30)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.cfg=self.base/'settings/base.json';self.cfg.parent.mkdir()
        config={'schema_version':'batch.1','evidence_kind':'live','allow_pilot':True,
                'providers':{role:{'model':'example','base_url':'https://example.invalid/v1','api_key_env':f'BENCH_{role.upper()}_API_KEY','parameters':{}} for role in ('text','image','vision')},
                'systems':{s:{'repo_path':f'../../systems/{s}','python_executable':'../venv/bin/python'} for s in exp.SYSTEMS},
                'bundles':['unused'], 'render_mode':'offscreen_native','reading_delay_seconds':0,'choice_indices':[0]}
        self.cfg.write_text(json.dumps(config))
        self.args=argparse.Namespace(preset='quick',case_ids=None,repeat=None,systems=list(exp.SYSTEMS),choices=[0,1],concurrency=1,
             config=self.cfg,secrets=self.base/'secrets.env',suite_root=None,out=self.base/'experiment',allow_pilot=True,
             yes=True,run=False,resume=False,verify=False,preflight=False)
        self.out,self.plan=exp.prepare(self.args)
    def tearDown(self): self.temp.cleanup()
    def test_config_paths_keep_original_base(self):
        saved=json.loads((self.out/'config.json').read_text())
        self.assertEqual(saved['systems']['if_line']['python_executable'],str(self.base/'venv/bin/python'))
        self.assertEqual(saved['choice_indices'],[0,1])
        self.assertEqual(json.loads(self.cfg.read_text())['choice_indices'],[0])
    def test_native_index_policy_is_disclosed(self):
        self.assertIn('repeat last index',self.plan['choice_semantics'])
        self.assertEqual(self.plan['total_attempts'],3)
    def test_resume_rejects_changed_repeat(self):
        with self.assertRaises(SystemExit):
            exp.main(['--resume','--out',str(self.out),'--repeat','99'])
    def test_frozen_manifest_loads(self): self.assertEqual(exp.read_plan(self.out)['total_attempts'],3)
    def test_tampered_config_rejected(self):
        with (self.out/'config.json').open('a') as file:file.write(' ')
        with self.assertRaises(ValueError):exp.read_plan(self.out)
    def test_tampered_input_rejected(self):
        p=next((self.out/'inputs/openings').glob('*.txt'));p.write_text('tampered')
        with self.assertRaises(ValueError):exp.read_plan(self.out)
    def test_changed_code_rejected(self):
        with patch.object(exp,'code_inventory',return_value={'changed':'hash'}):
            with self.assertRaises(ValueError):exp.read_plan(self.out)
    def test_default_prepare_and_yes_alone_make_no_calls(self):
        target=self.base/'another'
        with patch.object(exp,'call',side_effect=AssertionError('unexpected native call')):
            code=exp.main(['--config',str(self.cfg),'--out',str(target),'--allow-pilot','--yes'])
        self.assertEqual(code,0);self.assertTrue((target/'experiment.json').is_file())
    def test_first_preflight_failure_stops_all_paid_calls(self):
        modes=[]
        def fake(cmd,out):modes.append(cmd);return 2
        with patch.object(exp,'call',side_effect=fake):code=exp.execute(self.out,self.plan,self.args)
        self.assertEqual(code,2);self.assertEqual(len(modes),1);self.assertIn('--preflight',modes[0])
    def test_all_preflights_precede_native_execution(self):
        seen=[]
        def fake(cmd,out):
            mode=next((m for m in ('--preflight','--plan-only','--resume','--verify') if m in cmd),'compare')
            seen.append(mode)
            if mode!='compare':
                system=cmd[cmd.index('--system')+1];target=out/system
                if mode=='--plan-only':target.mkdir();(target/'plan.json').write_text('{}')
                if mode=='--resume':(target/'results.json').write_text(json.dumps({'runs':[{'scope_reached':True,'evidence_verified':True,'stop_reason':'reading_window'}]}))
            else:(out/'comparison.json').write_text('{"comparison_ready":true}')
            return 0
        with patch.object(exp,'call',side_effect=fake):code=exp.execute(self.out,self.plan,self.args)
        self.assertEqual(code,0);self.assertEqual(seen[:6],['--preflight']*3+['--plan-only']*3)
        self.assertEqual(seen.count('--resume'),3)
        self.assertTrue((self.out/'EXPERIMENT_REPORT.md').is_file())
    def test_verify_command_never_loads_secrets(self):
        cmd=exp.batch_command(self.out,'if_line','--verify',1,self.args.secrets,1)
        self.assertNotIn('--secrets',cmd)
    def test_missing_rows_not_counted_as_success(self):
        summary=exp.summarize(self.out,self.plan,[])
        self.assertFalse(summary['all_scopes_reached']);self.assertFalse(summary['all_evidence_verified'])
        self.assertEqual(summary['systems']['if_line']['recorded_rows'],0)
    def test_interrupted_execution_preserves_summary(self):
        with patch.object(exp,'call',side_effect=KeyboardInterrupt):code=exp.execute(self.out,self.plan,self.args)
        self.assertEqual(code,130);self.assertTrue((self.out/'experiment_summary.json').is_file())
    @unittest.skipUnless(sys.platform=='linux','POSIX experiment lock')
    def test_exclusive_experiment_lock(self):
        with exp.experiment_lock(self.out):
            with self.assertRaises(ValueError):
                with exp.experiment_lock(self.out):pass


class SetupTests(unittest.TestCase):
    def test_valid_endpoint(self):self.assertEqual(endpoint('https://example.invalid/v1'),'https://example.invalid/v1')
    def test_endpoint_rejects_credentials_or_wrong_path(self):
        for value in ('https://user:password@example.invalid/v1','https://example.invalid/v1?api_key=secret','https://example.invalid/api','file:///v1'):
            with self.assertRaises(ValueError):endpoint(value)


if __name__=='__main__':unittest.main()
