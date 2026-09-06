"""Verify every published upstream blob; no network or model calls."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'benchmark'))
from story_benchmark.provenance import verify_repository

lock=json.loads((ROOT/'baseline-lock.json').read_text())
results={name:verify_repository(ROOT/'systems'/name,item['base_commit'],{'source_lock':str(ROOT/'baseline-lock.json')})
         for name,item in lock['systems'].items()}
print(json.dumps(results,ensure_ascii=False,indent=2))
raise SystemExit(0 if all(v['ok'] for v in results.values()) else 1)
