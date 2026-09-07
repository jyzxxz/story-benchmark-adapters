#!/usr/bin/env python3
"""Run one native system using the local common environment and secret file."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system', required=True, choices=['if_line','ai4visualnovel','infiplot'])
    parser.add_argument('--config', type=Path, default=ROOT/'work/config/batch.local.json')
    parser.add_argument('--secrets', type=Path, default=ROOT/'work/secrets.env')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--count', type=int, default=1, help='Number of independent attempts, not guaranteed successes')
    parser.add_argument('--concurrency', type=int, default=1)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--resume', action='store_true')
    mode.add_argument('--verify', action='store_true')
    mode.add_argument('--preflight', action='store_true')
    mode.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('Use Ubuntu Linux or Ubuntu under WSL2; native Windows is not supported.')
    common = ROOT/'work/envs/common/bin/python'
    if not common.is_file():
        parser.error('Run bash tools/bootstrap_linux.sh first (missing common environment).')
    # sys.prefix identifies a venv without resolving its python symlink to the base executable.
    if Path(sys.prefix).absolute() != common.parent.parent:
        os.execv(str(common), [str(common), str(Path(__file__).absolute()), *sys.argv[1:]])
    from dotenv import load_dotenv
    # Explicitly exported environment takes precedence; never execute the .env as a shell script.
    if args.secrets.exists() and not args.verify:
        load_dotenv(args.secrets, override=False, interpolate=False)
    os.environ['PATH'] = str(ROOT/'work/node/bin')+os.pathsep+os.environ.get('PATH','')
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'work/browser-cache'))
    target = ROOT/'benchmark'/f'run_{args.system}_batch.py'
    argv = [str(common), str(target), '--config', str(args.config.absolute()),
            '--count', str(args.count), '--concurrency', str(args.concurrency)]
    if args.out:
        argv += ['--out', str(args.out.absolute())]
    for mode in ('resume','verify','preflight','plan_only'):
        if getattr(args,mode): argv += ['--'+mode.replace('_','-')]
    os.execv(str(common),argv)

if __name__ == '__main__':
    main()
