"""Verify published baselines and an explicitly declared IF Line source patch."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'benchmark'))
from story_benchmark.provenance import verify_repository


def main(argv=None, root=ROOT):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--source-patch',type=Path,help='IF Line source-patch.1 manifest; applies only to if_line')
    group.add_argument('--pristine-only',action='store_true',help='Require original baseline bytes for all systems')
    args=parser.parse_args(argv)
    root=Path(root).resolve()
    lock=json.loads((root/'baseline-lock.json').read_text())
    default=root/'native-patches/if_line/manifest.json'
    source_patch=(args.source_patch.resolve() if args.source_patch else default if default.exists() else None)
    if args.pristine_only:source_patch=None
    results={}
    for name,item in lock['systems'].items():
        config={'source_lock':str(root/'baseline-lock.json')}
        if name=='if_line' and source_patch is not None:config['source_patch']=str(source_patch)
        results[name]=verify_repository(root/'systems'/name,item['base_commit'],config)
    print(json.dumps(results,ensure_ascii=False,indent=2))
    return 0 if all(v['ok'] for v in results.values()) else 1


if __name__=='__main__':
    raise SystemExit(main())
