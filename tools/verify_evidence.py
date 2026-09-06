"""Recheck archived native observations; all provider results are explicit fixtures."""
from pathlib import Path
import hashlib
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'benchmark'))
from story_benchmark.audit import audit_trace
from story_benchmark.io import safe_child
archive=ROOT/'docs/evidence'
for relative,digest in json.loads((archive/'archive-sha256.json').read_text()).items():
    if hashlib.sha256(safe_child(archive,relative).read_bytes()).hexdigest()!=digest:
        raise SystemExit('Archive changed: '+relative)
shared=(ROOT/'benchmark/examples/CAMPUS-01/shared_task.txt').read_text()
opening=(ROOT/'benchmark/examples/CAMPUS-01/opening.txt').read_text()
result={'evidence_kind':'mock','formal_experiment':False,'all_received_texts_equal':True,
        'shared_sha256':hashlib.sha256(shared.encode()).hexdigest(),
        'opening_sha256':hashlib.sha256(opening.encode()).hexdigest(),'systems':{}}
for system in ('if_line','ai4visualnovel','infiplot'):
    run=archive/'mock'/system
    receipts=[json.loads(p.read_text()) for p in (run/'trace').rglob('*.json') if 'received' in p.name]
    if not receipts:raise SystemExit('Missing observed receiver: '+system)
    for receipt in receipts:
        value=receipt.get('received_task',receipt.get('shared_task'))
        if value!=shared or opening not in value:raise SystemExit('Actual received text differs: '+system)
    audit=audit_trace(run,shared,opening)
    if (not audit['external_input_equal'] or audit['task_entry_shared_occurrences']!=1
        or not audit['first_prose_request_observed'] or audit['trace_parse_errors']
        or audit['missing_response_files'] or audit['delivery_unknown_calls']):
        raise SystemExit('Incomplete native input evidence: '+system)
    result['systems'][system]={'received_sha256':hashlib.sha256(value.encode()).hexdigest(),
        'opening_present':True,'receiver_boundaries':audit['receiver_boundaries'],
        'first_task_occurrences':audit['task_entry_shared_occurrences'],
        'first_prose_request_observed':audit['first_prose_request_observed'],
        'observed_fixture_http_calls':audit['observed_http_calls'],
        'fixture_requested_models':audit['requested_models'],
        'native_input_propagation':audit['native_input_propagation']}
print(json.dumps(result,ensure_ascii=False,indent=2))
