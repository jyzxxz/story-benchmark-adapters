"""Explicit IF Line patch verification; no native services or model requests."""
from contextlib import redirect_stdout
import copy
import difflib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from story_benchmark.batch import load_batch_config, prepare_plan, read_plan
from story_benchmark.batch_worker import run_job
from story_benchmark.experiment import load_experiment
from story_benchmark.io import BenchmarkError, atomic_json, sha256
from story_benchmark.provenance import source_identity, verify_repository


def diff(path, before, after):
    mode = 'new file mode 100644\n' if not before else 'deleted file mode 100644\n' if not after else ''
    return 'diff --git a/{0} b/{0}\n'.format(path) + mode + ''.join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile='a/'+path if before else '/dev/null', tofile='b/'+path if after else '/dev/null'))


class SourcePatchTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.repo=self.root/'systems/if_line';self.repo.mkdir(parents=True)
        self.original={'main.py':'value = 1\n','other.py':'untouched = True\n'}
        for p,s in self.original.items():(self.repo/p).write_text(s)
        self.lock={'systems':{'if_line':{'base_commit':'base-if','adapted_commit':'base-if',
            'source_modified':False,'files':{p:sha256(s) for p,s in self.original.items()}}}}
        self.lockpath=self.root/'baseline-lock.json';atomic_json(self.lockpath,self.lock)
        self.manifest_path=self.root/'native-patches/if_line/manifest.json'
        self.patchpath=self.manifest_path.parent/'fix.patch';self.patchpath.parent.mkdir(parents=True)
        (self.repo/'main.py').write_text('value = 2\n');(self.repo/'helper.py').write_text('helper = True\n')
        self.patchpath.write_text(diff('main.py',self.original['main.py'],'value = 2\n')+diff('helper.py','','helper = True\n'))
        self.manifest={'schema_version':'source-patch.1','system':'if_line','base_commit':'base-if',
            'baseline_lock_sha256':sha256(self.lockpath.read_bytes()),'native_variant':'if_line_html_fix_v1',
            'patch_file':'fix.patch','patch_sha256':sha256(self.patchpath.read_bytes()),
            'files':{'main.py':{'before_sha256':sha256(self.original['main.py']),'after_sha256':sha256('value = 2\n')},
                     'helper.py':{'before_sha256':None,'after_sha256':sha256('helper = True\n')}}}
        self.config={'source_lock':str(self.lockpath),'source_patch':str(self.manifest_path)}
        self.save()
    def tearDown(self):self.temp.cleanup()
    def save(self):atomic_json(self.manifest_path,self.manifest)
    def check(self):return verify_repository(self.repo,'base-if',self.config)
    def reject(self,code):
        result=self.check();self.assertFalse(result['ok'],result)
        self.assertIn(code,';'.join(result['errors']))

    def test_valid_patch_complete_tree_and_no_native_or_lock_writes(self):
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=self.check();self.assertTrue(result['ok'],result)
        self.assertTrue(result['source_modified']);self.assertEqual(result['native_variant'],'if_line_html_fix_v1')
        check=result['checks'][0];self.assertEqual(check['source_files_verified'],3)
        self.assertIsNone(check['adapted_commit']);self.assertTrue(check['source_patch_reverse_verified'])
        self.assertEqual(check['source_patch_manifest_sha256'],sha256(self.manifest_path.read_bytes()))
        self.assertEqual(check['source_patch_sha256'],sha256(self.patchpath.read_bytes()))
        expected={p:sha256((self.repo/p).read_bytes()) for p in ('main.py','other.py','helper.py')}
        self.assertEqual(check['source_tree_sha256'],sha256(json.dumps(expected,ensure_ascii=False,sort_keys=True,separators=(',',':'))))
        self.assertEqual(before,{str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_default_still_requires_pristine(self):
        result=verify_repository(self.repo,'base-if',{'source_lock':str(self.lockpath)})
        self.assertFalse(result['ok'])
        (self.repo/'helper.py').unlink();(self.repo/'main.py').write_text(self.original['main.py'])
        result=verify_repository(self.repo,'base-if',{'source_lock':str(self.lockpath)})
        self.assertTrue(result['ok']);self.assertFalse(result['source_modified'])
        self.assertEqual(result['native_variant'],'pristine')

    def test_non_ifline_patch_and_pristine_drift_still_rejected(self):
        for system in ('AI4VisualNovel','infiplot'):
            with self.subTest(system=system):
                self.lock['systems']={system:{'base_commit':'base-if','adapted_commit':'base-if','files':{p:sha256(s) for p,s in self.original.items()}}}
                atomic_json(self.lockpath,self.lock)
                self.reject('source_patch_only_supported_for_if_line')
                for p,s in self.original.items():(self.repo/p).write_text(s)
                (self.repo/'helper.py').unlink(missing_ok=True)
                self.assertTrue(verify_repository(self.repo,'base-if',{'source_lock':str(self.lockpath)})['ok'])
                (self.repo/'main.py').write_text('changed')
                self.assertFalse(verify_repository(self.repo,'base-if',{'source_lock':str(self.lockpath)})['ok'])
                (self.repo/'main.py').write_text(self.original['main.py'])
                (self.repo/'extra_prompt.py').write_text('unlocked source')
                self.assertFalse(verify_repository(self.repo,'base-if',{'source_lock':str(self.lockpath)})['ok'])
                (self.repo/'extra_prompt.py').unlink()

    def test_requires_lock_and_original_commit(self):
        self.config.pop('source_lock');self.reject('source_patch_requires_baseline_lock')
        self.config['expected_commit']='new-commit';self.reject('modified_native_revision_forbidden')

    def test_schema_system_revision_and_lock_binding(self):
        for field,value,error in [('schema_version','unknown','invalid_source_patch_manifest'),
            ('system','infiplot','source_patch_system_or_revision_mismatch'),
            ('base_commit','another','source_patch_system_or_revision_mismatch'),
            ('baseline_lock_sha256','0'*64,'source_patch_baseline_lock_mismatch'),
            ('native_variant','pristine','explicit_modified_native_variant_required')]:
            with self.subTest(field=field):
                old=self.manifest[field];self.manifest[field]=value;self.save();self.reject(error)
                self.manifest[field]=old;self.save()
        self.manifest['unapproved']=True;self.save();self.reject('invalid_source_patch_manifest')

    def test_patch_before_after_and_extra_source_binding(self):
        self.patchpath.write_text(self.patchpath.read_text()+'\nchanged');self.reject('source_patch_file_hash_mismatch')
        self.manifest['patch_sha256']=sha256(self.patchpath.read_bytes())
        self.manifest['files']['main.py']['before_sha256']='0'*64;self.save();self.reject('source_patch_before_hash_mismatch')
        self.manifest['files']['main.py']['before_sha256']=sha256(self.original['main.py'])
        self.manifest['files']['main.py']['after_sha256']='0'*64;self.save();self.reject('source_patch_after_hash_mismatch')
        self.manifest['files']['main.py']['after_sha256']=sha256('value = 2\n');self.save()
        (self.repo/'extra_prompt.py').write_text('extra');self.reject('unlocked_source_files')
        (self.repo/'extra_prompt.py').unlink();(self.repo/'other.py').write_text('changed');self.reject('source_hash_mismatch')

    def test_declared_hash_is_not_enough_diff_must_reproduce_before(self):
        self.patchpath.write_text(diff('main.py','wrong baseline\n','value = 2\n')+diff('helper.py','','helper = True\n'))
        self.manifest['patch_sha256']=sha256(self.patchpath.read_bytes());self.save()
        self.reject('source_patch_diff_does_not_match_declared_files')

    def test_undeclared_patch_deletion_is_not_accepted(self):
        self.patchpath.write_text(self.patchpath.read_text()+diff('undeclared.py','removed source\n',''))
        self.manifest['patch_sha256']=sha256(self.patchpath.read_bytes());self.save()
        self.reject('source_patch_diff_does_not_match_declared_files')

    def test_path_traversal_absolute_and_symlinks_rejected(self):
        for value in ('../fix.patch','/tmp/fix.patch','a/../fix.patch','a\\fix.patch'):
            with self.subTest(value=value):
                self.manifest['patch_file']=value;self.save();self.reject('invalid_source_patch_relative_path')
        self.manifest['patch_file']='fix.patch';self.save()
        source=self.repo/'main.py';source.unlink();source.symlink_to(self.repo/'helper.py')
        self.reject('source_symlink_forbidden')
        source.unlink();source.write_text('value = 2\n')
        (self.repo/'unlocked_link').symlink_to(self.manifest_path.parent,target_is_directory=True)
        self.reject('unlocked_source_files')

    def test_new_case_alias_and_unchanged_declaration_rejected(self):
        self.manifest['files']['Main.py']=copy.deepcopy(self.manifest['files']['helper.py'])
        self.save();self.reject('source_patch_new_case_alias_forbidden')
        self.manifest['files'].pop('Main.py');self.manifest['files']['other.py']={
            'before_sha256':sha256(self.original['other.py']),'after_sha256':sha256(self.original['other.py'])}
        self.save();self.reject('source_patch_unchanged_file')

    def test_missing_manifest_patch_symlink_and_source_path_traversal(self):
        self.config['source_patch']=str(self.root/'missing.json')
        self.assertFalse(self.check()['ok']);self.config['source_patch']=str(self.manifest_path)
        self.manifest['patch_file']='linked.patch';self.save()
        (self.manifest_path.parent/'linked.patch').symlink_to(self.patchpath)
        self.reject('source_symlink_forbidden')
        self.manifest['patch_file']='fix.patch'
        self.manifest['files']['../outside.py']=self.manifest['files'].pop('helper.py');self.save()
        self.reject('invalid_source_patch_relative_path')

    def test_patch_path_resolved_in_both_configuration_loaders(self):
        common={'live':False,'model':None,'model_base_url':None,'timeout_seconds':None,'max_calls':None,
                'max_output_tokens':None,'max_input_chars':None,'allow_pilot':True}
        specific={s:{'repo_path':'systems/'+s} for s in ('if_line','ai4visualnovel','infiplot')}
        specific['if_line']['source_patch']='native-patches/if_line/manifest.json'
        configpath=self.root/'experiment.json';atomic_json(configpath,{'schema_version':'2.1','common':common,'systems':specific})
        self.assertEqual(load_experiment(configpath)['if_line']['source_patch'],str(self.manifest_path))
        providers={role:{'model':'gpt-image-2' if role=='image' else 'model','base_url':'http://127.0.0.1:1/v1',
                          'api_key_env':'TEST_KEY'} for role in ('text','vision','image')}
        atomic_json(configpath,{'schema_version':'batch.1','evidence_kind':'fixture','allow_pilot':True,
                   'providers':providers,'systems':specific,'bundles':['bundle']})
        self.assertEqual(load_batch_config(configpath)['systems']['if_line']['source_patch'],str(self.manifest_path))

    def test_verify_sources_default_manifest_and_explicit_pristine_cli(self):
        modulepath=Path(__file__).resolve().parents[2]/'tools/verify_sources.py'
        spec=importlib.util.spec_from_file_location('verify_sources_tool',modulepath)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        stream=io.StringIO()
        with redirect_stdout(stream):code=module.main([],root=self.root)
        self.assertEqual(code,0);self.assertTrue(json.loads(stream.getvalue())['if_line']['source_modified'])
        with redirect_stdout(io.StringIO()):self.assertEqual(module.main(['--pristine-only'],root=self.root),1)
        with redirect_stdout(io.StringIO()):self.assertEqual(module.main(['--source-patch',str(self.manifest_path)],root=self.root),0)

    def test_frozen_plan_rejects_another_valid_patch_at_same_path_before_gateway(self):
        example=Path(__file__).resolve().parents[1]/'configs/batch.example.json'
        config=load_batch_config(example);config['policy']['evidence_kind']='fixture'
        config['systems']['if_line'].update(repo_path=str(self.repo),**self.config)
        out=self.root/'batch';plan=prepare_plan('if_line',config,out,1)
        frozen=source_identity(self.check());self.assertEqual(plan['source_identity'],frozen)
        # Another internally consistent approved-format patch at the same paths
        # must not change the identity of an already prepared experiment.
        (self.repo/'main.py').write_text('value = 3\n')
        self.patchpath.write_text(diff('main.py',self.original['main.py'],'value = 3\n')+diff('helper.py','','helper = True\n'))
        self.manifest['patch_sha256']=sha256(self.patchpath.read_bytes())
        self.manifest['files']['main.py']['after_sha256']=sha256('value = 3\n');self.save()
        replacement=self.check();self.assertTrue(replacement['ok'],replacement)
        self.assertNotEqual(source_identity(replacement),frozen)
        # Reading historical plans does not revalidate against today's source.
        self.assertEqual(read_plan(out)['source_identity'],frozen)
        job=plan['jobs'][0];runroot=out/'runs'/job['run_id']
        with patch('story_benchmark.batch_worker.ModelGateway') as gateway:
            result=run_job(plan,job,runroot,driver=object())
        gateway.assert_not_called()
        self.assertEqual(result['stop_reason'],'adapter_error')
        self.assertFalse(result['source_verification']['frozen_identity_unchanged'])
        failure=json.loads((runroot/'native/exception.json').read_text())
        self.assertEqual(failure['message'],'native_source_changed_since_plan')

    def test_legacy_plan_with_patch_requires_new_prepare(self):
        from story_benchmark.batch_worker import verify_planned_source
        with self.assertRaisesRegex(BenchmarkError,'source_patch_requires_new_frozen_plan'):
            verify_planned_source({},self.config,self.check())
        (self.repo/'helper.py').unlink();(self.repo/'main.py').write_text(self.original['main.py'])
        original_config={'source_lock':str(self.lockpath)}
        self.assertTrue(verify_planned_source({},original_config,
            verify_repository(self.repo,'base-if',original_config)))

    def test_valid_patch_swap_during_run_is_recorded_as_source_identity_drift(self):
        example=Path(__file__).resolve().parents[1]/'configs/batch.example.json'
        config=load_batch_config(example);config['policy']['evidence_kind']='fixture'
        config['systems']['if_line'].update(repo_path=str(self.repo),**self.config)
        out=self.root/'batch';plan=prepare_plan('if_line',config,out,1)
        fixture=self
        class Driver:
            @staticmethod
            def preflight(*args):return {'ok':True}
            @staticmethod
            def run(*args):
                (fixture.repo/'main.py').write_text('value = 3\n')
                fixture.patchpath.write_text(diff('main.py',fixture.original['main.py'],'value = 3\n')+diff('helper.py','','helper = True\n'))
                fixture.manifest['patch_sha256']=sha256(fixture.patchpath.read_bytes())
                fixture.manifest['files']['main.py']['after_sha256']=sha256('value = 3\n');fixture.save()
                return {'stop_reason':'native_end','native_ended':True}
        job=plan['jobs'][0];runroot=out/'runs'/job['run_id']
        original_manifest=self.manifest_path.read_bytes()
        with patch('story_benchmark.batch_worker.ModelGateway'):
            result=run_job(plan,job,runroot,driver=Driver)
        self.assertFalse(result['source_verification']['frozen_identity_unchanged'])
        self.assertTrue(result['source_verification']['before_ok']);self.assertTrue(result['source_verification']['after_ok'])
        self.assertEqual((runroot/'native/source_patch/manifest.json').read_bytes(),original_manifest)
        errors=[json.loads(line) for line in (runroot/'errors.jsonl').read_text().splitlines()]
        self.assertTrue(any(row['code']=='native_source_identity_changed' for row in errors))


    def test_successful_patched_run_manifest_has_exact_frozen_hashes(self):
        config=load_batch_config(Path(__file__).resolve().parents[1]/'configs/batch.example.json')
        config['policy']['evidence_kind']='fixture'
        config['systems']['if_line'].update(repo_path=str(self.repo),**self.config)
        out=self.root/'batch';plan=prepare_plan('if_line',config,out,1)
        class Driver:
            @staticmethod
            def preflight(*_):return {'ok':True}
            @staticmethod
            def run(*_):return {'stop_reason':'native_end','native_ended':True}
        job=plan['jobs'][0]
        with patch('story_benchmark.batch_worker.ModelGateway'):
            result=run_job(plan,job,out/'runs'/job['run_id'],driver=Driver)
        expected=self.check()['checks'][0]
        variant=result['native_source_variant']
        self.assertTrue(variant['source_modified'])
        for key in ('native_variant','source_patch_manifest_sha256',
                    'source_patch_sha256','source_tree_sha256'):
            self.assertEqual(variant[key],expected[key],key)
            self.assertIsNotNone(variant[key],key)
        self.assertTrue(result['source_verification']['frozen_identity_unchanged'])

    def test_nested_patch_filename_remains_portable(self):
        from story_benchmark.batch_worker import preserve_source_patch
        from story_benchmark.recording import Recorder
        nested=self.patchpath.parent/'nested/native-visible-fix.patch'
        nested.parent.mkdir();self.patchpath.rename(nested)
        self.patchpath=nested;self.manifest['patch_file']='nested/native-visible-fix.patch';self.save()
        report=self.check();self.assertTrue(report['ok'],report)
        recorder=Recorder(self.root/'recording','portable',4000)
        preserve_source_patch(recorder,report)
        saved=recorder.root/'native/source_patch/manifest.json'
        manifest=json.loads(saved.read_text())
        self.assertEqual(manifest['patch_file'],'nested/native-visible-fix.patch')
        saved_patch=saved.parent/manifest['patch_file']
        self.assertEqual(saved_patch.read_bytes(),nested.read_bytes())
        self.assertEqual(sha256(saved_patch.read_bytes()),manifest['patch_sha256'])


if __name__=='__main__':unittest.main()
