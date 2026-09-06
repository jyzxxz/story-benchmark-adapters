import argparse
import json
from pathlib import Path
from .compiler import compile_case, verify_bundle
from .io import read_json
from .runner import preflight, run_once, resume_export, RootRunTimeout, verify_saved_run
from .audit import audit_trace


def main():
    parser = argparse.ArgumentParser(description='v2.1 first-artifact native integration; no scoring or branch player')
    sub = parser.add_subparsers(dest='command', required=True)
    compile_cmd = sub.add_parser('compile')
    compile_cmd.add_argument('--case', required=True)
    compile_cmd.add_argument('--out', required=True)
    compile_cmd.add_argument('--allow-pilot', action='store_true')
    verify = sub.add_parser('verify')
    verify.add_argument('--bundle', required=True)
    group=sub.add_parser('preflight-set')
    group.add_argument('--experiment',required=True)
    group.add_argument('--bundle',required=True)
    for command in ('preflight', 'run-first'):
        child = sub.add_parser(command)
        child.add_argument('--system', required=True, choices=('ai4visualnovel','infiplot','if_line'))
        child.add_argument('--bundle', required=True)
        configuration = child.add_mutually_exclusive_group(required=True)
        configuration.add_argument('--config', help='Single-system engineering configuration')
        configuration.add_argument('--experiment', help='One shared model, endpoint and budget configuration for all three systems')
        if command == 'run-first': child.add_argument('--run-dir', required=True)
    for command in ('audit-run', 'resume-export'):
        child = sub.add_parser(command)
        child.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'compile': result = compile_case(args.case, args.out, args.allow_pilot)
        elif args.command == 'verify': result = verify_bundle(args.bundle)
        elif args.command == 'preflight-set':
            from .experiment import preflight_set
            result=preflight_set(args.experiment,args.bundle)
        elif args.command in ('preflight', 'run-first'):
            if args.experiment:
                from .experiment import load_experiment
                config = load_experiment(args.experiment)[args.system]
            else:
                config = read_json(args.config)
            result = (preflight(args.system,args.bundle,config) if args.command == 'preflight' else run_once(args.system,args.bundle,config,args.run_dir))
        elif args.command == 'resume-export': result = resume_export(args.run_dir)
        else:
            root = Path(args.run_dir)
            verify_saved_run(root)
            result = audit_trace(root,(root/'shared_task.txt').read_text(),(root/'export/provided_prefix.txt').read_text())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        failed = result.get('ok') is False or result.get('adapter_status') == 'failed'
        if args.command in ('run-first', 'resume-export'):
            failed = failed or result.get('audit_status') != 'passed' or result.get('generation_status') in ('failed','budget_exhausted','delivery_unknown')
        return 1 if failed else 0
    except (ValueError, OSError, RuntimeError, RootRunTimeout) as exc:
        from .io import redact
        print(json.dumps({'error': redact(str(exc)), 'error_type': type(exc).__name__}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
