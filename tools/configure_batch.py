#!/usr/bin/env python3
"""Create a local batch config with Linux paths, without copying API secrets."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'work/config/batch.local.json')
    parser.add_argument('--pg-bin', default='/usr/lib/postgresql/16/bin')
    parser.add_argument('--text-base-url')
    parser.add_argument('--text-model')
    parser.add_argument('--vision-base-url')
    parser.add_argument('--vision-model')
    parser.add_argument('--image-base-url')
    parser.add_argument('--force', action='store_true', help='explicitly replace an existing config; secrets are never overwritten')
    args = parser.parse_args()
    out = args.out.absolute()
    if out.exists() and not args.force:
        parser.error(f'{out} already exists; edit it, or use --force to recreate defaults')
    cfg = json.loads((ROOT/'benchmark/configs/batch.example.json').read_text())
    cfg['bundles'] = [str(ROOT/'benchmark/examples/CAMPUS-01-V4')]
    work = ROOT/'work'
    for name, folder in [('if_line','if_line'), ('ai4visualnovel','AI4VisualNovel'), ('infiplot','infiplot')]:
        c = cfg['systems'][name]
        c['repo_path'] = str(ROOT/'systems'/folder)
        c['source_lock'] = str(ROOT/'baseline-lock.json')
        if name != 'infiplot':
            c['python_executable'] = str(work/'envs'/name/'bin/python')
            c['rembg_model_dir'] = str(work/'models'/name)
        if name != 'ai4visualnovel':
            c['node_executable'] = str(work/'node/bin/node')
            c['node_modules'] = str(work/'media-tools/node_modules')
        if name == 'if_line':
            c['source_patch'] = str(ROOT/'native-patches/if_line/manifest.json')
            c['pg_bin'] = os.path.abspath(args.pg_bin)
            c['redis_executable'] = shutil.which('redis-server') or '/usr/bin/redis-server'
        if name == 'infiplot':
            c['pnpm_executable'] = str(work/'node/bin/pnpm')
            c['playwright_module'] = str(work/'media-tools/node_modules/playwright')
            c['dependency_timeout_seconds'] = 600
    for role in ('text','vision','image'):
        for field in ('base_url','model'):
            value = getattr(args, f'{role}_{field}', None)
            if value:
                cfg['providers'][role][field] = value
    # Same validation as the runners, including no inline credentials and no per-system models.
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name+'.tmp')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2)+'\n')
    sys.path.insert(0, str(ROOT/'benchmark'))
    from story_benchmark.batch import load_batch_config
    try:
        load_batch_config(tmp)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    secrets = work/'secrets.env'
    try:
        fd = os.open(secrets, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as f:
            f.write('# Local only. Use values authorized for the provider URLs in batch.local.json.\n'
                    'BENCH_TEXT_API_KEY=\nBENCH_VISION_API_KEY=\nBENCH_IMAGE_API_KEY=\n')
    print(json.dumps({'config':str(out),'secrets_file':str(secrets),
        'next':'Set provider URLs/models in the config and keys in secrets.env; doctor and smoke make no paid requests.'},ensure_ascii=False,indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
