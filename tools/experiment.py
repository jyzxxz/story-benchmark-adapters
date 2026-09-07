#!/usr/bin/env python3
"""One-command preparation, generation and evidence checks for the three systems.
Preparation is free. --run/--resume require confirmation; --yes is explicit consent.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

from eval30 import ROOT, CATALOG, catalog_sources, expand_suite, digest
from story_benchmark.compiler import compile_case, verify_bundle
from story_benchmark.io import atomic_json, atomic_write, read_json, redact, safe_child

SYSTEMS = ('if_line', 'ai4visualnovel', 'infiplot')
PATH_FIELDS = {'repo_path','repo_dir','source_lock','source_patch','python_executable','redis_executable',
               'node_executable','pnpm_executable','runtime_root','rembg_model_dir','playwright_module',
               'dependency_repo_path','node_modules','pg_bin','chromium_executable'}
PRESETS = {'quick': (['CAMPUS-01'], 1), 'genres': ([f'{g}-01' for g in
           ('CAMPUS','SCI-FI','MYSTERY','FANTASY','HISTORY','EMOTION')], 1),
           'pilot': (None, 1), 'full': (None, 3)}


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('Must be at least 1')
    return number


def selection(preset: str, requested: list[str] | None, repeat: int | None, rows: list[dict]) -> tuple[list[str], int]:
    default_ids, default_repeat = PRESETS[preset]
    ids = requested or default_ids or [row['source_prompt_id'] for row in rows]
    if len(ids) != len(set(ids)) or not set(ids).issubset({row['source_prompt_id'] for row in rows}):
        raise ValueError('Unknown/duplicate case IDs. Repetitions belong in --repeat.')
    return ids, repeat if repeat is not None else default_repeat


def code_inventory() -> dict[str, str]:
    paths = [ROOT/'tools'/name for name in ('experiment.py','eval30.py','run_batch.py')]
    paths += sorted((ROOT/'benchmark/story_benchmark').rglob('*.py'))
    return {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in paths if p.is_file()}


def input_inventory(out: Path) -> dict[str, str]:
    return {str(p.relative_to(out)): digest(p.read_bytes()) for p in sorted((out/'inputs').rglob('*')) if p.is_file()}


def prepare(args: argparse.Namespace) -> tuple[Path, dict]:
    if args.suite_root:
        catalog = read_json(args.suite_root/'suite_manifest.json')
    else:
        catalog, _, _ = catalog_sources()
    ids, repeat = selection(args.preset, args.case_ids, args.repeat, catalog['cases'])
    config = read_json(args.config)
    if config.get('schema_version') != 'batch.1' or config.get('evidence_kind') != 'live':
        raise ValueError('Use a live batch.1 common config, never fixture data')
    if redact(config) != config:
        raise ValueError('Inline credentials forbidden; use the local secrets.env file')
    if set(config.get('systems', {})) != set(SYSTEMS):
        raise ValueError('Keep all three native system settings in the common config')
    # Keep relative path meaning when copying config into an experiment directory.
    # abspath deliberately preserves virtualenv interpreter symlinks.
    for settings in config['systems'].values():
        for key in PATH_FIELDS:
            if settings.get(key):
                settings[key] = os.path.abspath(args.config.absolute().parent / Path(settings[key]).expanduser())
    out = (args.out or ROOT/'work/experiments'/f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}').absolute()
    out.mkdir(parents=True, exist_ok=False)
    if args.suite_root:
        indexed = {row['source_prompt_id']: row for row in catalog['cases']}
        if len(indexed) != len(catalog['cases']):
            raise ValueError('Duplicate source IDs in suite manifest')
        bundles = []
        for number, ident in enumerate(ids):
            source = safe_child(args.suite_root, indexed[ident]['case_file'])
            destination = out/'inputs/compiled'/str(number)
            compile_case(source, destination, allow_pilot=args.allow_pilot)
            bundles.append(destination)
    else:
        if not args.allow_pilot:
            raise ValueError('Bundled inputs are pilot. Add --allow-pilot or provide a human-approved --suite-root.')
        catalog = expand_suite(out/'inputs')
        indexed = {row['source_prompt_id']: row for row in catalog['cases']}
        bundles = [out/'inputs/compiled'/indexed[ident]['case_id'] for ident in ids]
    manifests = []
    for bundle in bundles:
        verify_bundle(bundle)
        manifests.append(read_json(bundle/'manifest.json'))
    pilot = any(m['review_status'] != 'approved' for m in manifests)
    if pilot and not args.allow_pilot:
        raise ValueError('Pilot inputs require explicit --allow-pilot')
    if any(m.get('output_contract', {}).get('version') != '4.0' for m in manifests):
        raise ValueError('Only v4 image-readable-window bundles are supported')
    if len({m['output_contract']['window_chars'] for m in manifests}) != 1:
        raise ValueError('Use one common reading window per experiment')
    config.update(bundles=[str(p) for p in bundles], choice_indices=args.choices, allow_pilot=pilot)
    atomic_json(out/'config.json', config)
    plan = {'schema_version':'experiment.1', 'root':str(out), 'created_at':datetime.now(timezone.utc).isoformat(),
            'systems':list(args.systems), 'case_ids':ids, 'repeat':repeat,
            'attempts_per_system':len(ids)*repeat, 'total_attempts':len(ids)*repeat*len(args.systems),
            'choice_indices':args.choices, 'choice_semantics':'zero-based native menu indices; repeat last index; not semantic C1/C2 routing',
            'review_status':'pilot' if pilot else 'approved', 'config_sha256':digest((out/'config.json').read_bytes()),
            'input_inventory':input_inventory(out), 'code_inventory':code_inventory(),
            'system_execution':'sequential; within-system concurrency is supplied at each launch',
            'quality_scoring':'not_run; five content metrics require external evaluation',
            'budget':'no adapter total token/time/cost cap; reading window is not a cost limit'}
    atomic_json(out/'experiment.json', plan)
    atomic_write(out/'experiment.sha256', digest((out/'experiment.json').read_bytes())+'\n')
    return out, plan


def read_plan(out: Path) -> dict:
    if digest((out/'experiment.json').read_bytes()) != (out/'experiment.sha256').read_text().strip():
        raise ValueError('Experiment manifest changed')
    plan = read_json(out/'experiment.json')
    if plan.get('schema_version') != 'experiment.1' or plan['root'] != str(out.absolute()):
        raise ValueError('Wrong/moved experiment directory; frozen absolute paths must remain valid')
    if digest((out/'config.json').read_bytes()) != plan['config_sha256'] or input_inventory(out) != plan['input_inventory']:
        raise ValueError('Frozen configuration or input files changed')
    if code_inventory() != plan['code_inventory']:
        raise ValueError('Runner/adapter changed; do not resume or reinterpret an old experiment with new code')
    return plan


@contextmanager
def experiment_lock(out: Path):
    import fcntl
    with (out/'.orchestrator.lock').open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another process is operating on this experiment') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def call(argv: list[str], out: Path) -> int:
    print('+', ' '.join(argv), flush=True)
    process = subprocess.Popen(argv, cwd=ROOT, start_new_session=True)
    try:
        return process.wait()
    except KeyboardInterrupt:
        print('Interrupted. Forwarding SIGINT; preserving sent requests and native cleanup.', flush=True)
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        while True:
            try:
                process.wait()
                break
            except KeyboardInterrupt:
                print('Native cleanup is still active; not force-killing or retrying.', flush=True)
        raise


def batch_command(out: Path, system: str, mode: str, concurrency: int, secrets: Path, count: int) -> list[str]:
    command = [sys.executable, str(ROOT/'tools/run_batch.py'), '--system', system,
               '--config', str(out/'config.json'), '--out', str(out/system),
               '--count', str(count), '--concurrency', str(concurrency), mode]
    if mode != '--verify':
        command += ['--secrets', str(secrets.absolute())]
    return command


def summarize(out: Path, plan: dict, stages: list[dict]) -> dict:
    summaries = {}
    for system in plan['systems']:
        path = out/system/'results.json'
        rows = read_json(path).get('runs', []) if path.is_file() else []
        summaries[system] = {'expected_attempts':plan['attempts_per_system'], 'recorded_rows':len(rows),
            'scope_reached':sum(r.get('scope_reached') is True for r in rows),
            'scope_not_reached':sum(r.get('scope_reached') is False for r in rows),
            'scope_unknown':sum(r.get('scope_reached') is None for r in rows),
            'evidence_verified':sum(r.get('evidence_verified') is True for r in rows),
            'stop_reasons':dict(Counter(r.get('stop_reason','unknown') for r in rows))}
    comparison = read_json(out/'comparison.json') if (out/'comparison.json').is_file() else None
    result = {'schema_version':'experiment-summary.1','systems':summaries,'stages':stages,
              'all_attempts_retained':True,'quality_scoring':'not_run','comparison':comparison,
              'updated_at':datetime.now(timezone.utc).isoformat()}
    result['all_scopes_reached'] = all(s['scope_reached']==s['expected_attempts'] for s in summaries.values())
    result['all_evidence_verified'] = all(s['evidence_verified']==s['expected_attempts'] for s in summaries.values())
    atomic_json(out/'experiment_summary.json', result)
    lines = ['# Experiment summary', '', 'Five content quality metrics remain unevaluated. No missing value is replaced with zero.', '',
             '| System | Expected attempts | Rows | Scope reached | Evidence verified |', '|---|---:|---:|---:|---:|']
    lines += [f'| {name} | {s["expected_attempts"]} | {s["recorded_rows"]} | {s["scope_reached"]} | {s["evidence_verified"]} |' for name,s in summaries.items()]
    lines += ['', 'Retain whole system batch directories, including failures. Reading-window completion is not full-story completion.',
              'See experiment_summary.json, each results.json, metrics.json, errors.jsonl, and evaluation/ packages.',
              'Latency comparisons must disclose scheduling, host load and provider throttling.']
    atomic_write(out/'EXPERIMENT_REPORT.md', '\n'.join(lines)+'\n')
    return result


def execute(out: Path, plan: dict, args: argparse.Namespace) -> int:
    stages = []
    session_file = out/'sessions'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]+'.json')
    def stage(system: str, mode: str) -> int:
        code = call(batch_command(out,system,mode,args.concurrency,args.secrets,plan['attempts_per_system']),out)
        stages.append({'system':system,'mode':mode,'returncode':code})
        atomic_json(session_file, {'stages':stages,'concurrency':args.concurrency})
        return code
    with experiment_lock(out):
        try:
            if not args.verify:
                # Validate every system before the first paid request; each plan-only operation is free.
                for system in plan['systems']:
                    if stage(system,'--preflight'):
                        summarize(out,plan,stages)
                        return 2
                if args.preflight:
                    summarize(out,plan,stages)
                    return 0
                for system in plan['systems']:
                    if not (out/system/'plan.json').exists():
                        if (out/system).exists():
                            raise ValueError('Partial native plan directory; inspect it, never overwrite it')
                        if stage(system,'--plan-only'):
                            summarize(out,plan,stages)
                            return 2
                for system in plan['systems']:
                    if stage(system,'--resume'):
                        summarize(out,plan,stages)
                        return 2
            for system in plan['systems']:
                if not (out/system/'plan.json').is_file():
                    raise ValueError('No native plan to verify: '+system)
                if stage(system,'--verify'):
                    summarize(out,plan,stages)
                    return 2
            if set(plan['systems']) == set(SYSTEMS):
                common = ROOT/'work/envs/common/bin/python'
                # Module lives in benchmark, not repository root; no global PYTHONPATH changes.
                argv = [str(common),'-c', 'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("story_benchmark.batch_compare",run_name="__main__")',str(ROOT/'benchmark'),*[str(out/s) for s in SYSTEMS],'--out',str(out/'comparison.json')]
                code = call(argv,out)
                stages.append({'system':'all','mode':'compare','returncode':code})
            result = summarize(out,plan,stages)
            return 0 if (all(s['returncode']==0 for s in stages) and result['all_scopes_reached'] and result['all_evidence_verified']) else 1
        except KeyboardInterrupt:
            stages.append({'mode':'interrupted','returncode':130})
            summarize(out,plan,stages)
            return 130


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preset',choices=PRESETS,default='quick')
    parser.add_argument('--case-ids',nargs='+')
    parser.add_argument('--repeat',type=positive)
    parser.add_argument('--systems',nargs='+',choices=SYSTEMS,default=list(SYSTEMS))
    parser.add_argument('--choices',nargs='+',type=int,default=[0,1])
    parser.add_argument('--concurrency',type=positive,default=1)
    parser.add_argument('--config',type=Path,default=ROOT/'work/config/batch.local.json')
    parser.add_argument('--secrets',type=Path,default=ROOT/'work/secrets.env')
    parser.add_argument('--suite-root',type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--allow-pilot',action='store_true')
    parser.add_argument('--yes',action='store_true',help='Explicitly authorize paid calls without an interactive confirmation')
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--run',action='store_true')
    modes.add_argument('--resume',action='store_true')
    modes.add_argument('--verify',action='store_true')
    modes.add_argument('--preflight',action='store_true',help='Prepare and check native environments, never generate')
    args=parser.parse_args(argv)
    supplied = argv if argv is not None else sys.argv[1:]
    frozen_flags = {'--preset','--case-ids','--repeat','--systems','--choices','--config','--suite-root','--allow-pilot'}
    if (args.resume or args.verify) and any(a.split('=',1)[0] in frozen_flags for a in supplied):
        parser.error('Resume/verify uses the frozen plan. Only --out, --concurrency, --secrets and --yes may change.')
    if len(set(args.systems)) != len(args.systems) or any(i<0 for i in args.choices):
        parser.error('Duplicate systems or negative menu index')
    if args.resume or args.verify:
        if args.out is None:
            parser.error('--out is required for resume/verify')
        out=args.out.absolute(); plan=read_plan(out)
    else:
        out,plan=prepare(args)
    print(json.dumps({k:plan[k] for k in ('systems','case_ids','repeat','attempts_per_system','total_attempts','review_status')},ensure_ascii=False,indent=2),flush=True)
    print('Experiment:',out,flush=True)
    if not any((args.run,args.resume,args.verify,args.preflight)):
        print('Preparation only: zero model calls. Start this saved plan with --resume --out PATH.')
        return 0
    if sys.platform != 'linux' or not (ROOT/'work/envs/common/bin/python').is_file():
        raise ValueError('Native execution requires prepared Ubuntu/WSL2 Linux. Run bash tools/experiment.sh setup first.')
    if args.run or args.resume:
        print('PAID GENERATION: no adapter total-cost/token/time cap. Failed or possibly sent attempts are never retried automatically.',flush=True)
        if not args.yes:
            if not sys.stdin.isatty() or input('Type RUN to authorize this experiment: ').strip()!='RUN':
                print('Not started. Inputs remain prepared; no model calls made.')
                return 0
    code=execute(out,plan,args)
    print('Summary:',out/'EXPERIMENT_REPORT.md',flush=True)
    return code


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(f'ERROR: {exc}. Existing output is retained; do not delete failures to retry.',file=sys.stderr)
        raise SystemExit(2)
