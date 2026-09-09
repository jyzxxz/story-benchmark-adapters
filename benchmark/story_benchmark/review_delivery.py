"""Publish an immutable, whole-batch recipient ZIP after generation stops."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import zipfile

from .io import BenchmarkError, atomic_json, atomic_write, read_json, sha256
from .playback import (ASSETS, Recording, _child, _digest, collection_coverage,
                       discover_runs, export_collection)


def _exporter_identity():
    paths = [Path(__file__), Path(__file__).with_name('playback.py')]
    paths += sorted(p for p in ASSETS.iterdir() if p.is_file())
    return {p.name: _digest(p) for p in paths}


def _verify_existing(destination, records, coverage):
    """Reject modified archives/readers instead of silently sending stale files."""
    report = read_json(_child(destination, 'report.json'))
    expected_paths = {'entry_file': str(destination/'review/index.html'),
                      'recipient_entry_file': str(destination/'review/打开故事.html'),
                      'zip_file': str(destination/'review.zip'),
                      'organizer_file': str(destination/'organizer.json')}
    if any(report.get(key) != value for key, value in expected_paths.items()) or report.get('coverage') != coverage:
        raise BenchmarkError('review_delivery_report_changed')
    organizer_path = _child(destination, 'organizer.json')
    if report.get('organizer_sha256') != _digest(organizer_path):
        raise BenchmarkError('review_delivery_organizer_changed')
    organizers = read_json(organizer_path).get('runs', [])
    by_digest = {row.get('source_manifest_sha256'): row for row in organizers}
    if len(by_digest) != len(records) or len(organizers) != len(records):
        raise BenchmarkError('review_delivery_organizer_changed')
    for record in records:
        row = by_digest.get(record.digest, {})
        if (row.get('metrics') != record.metrics or row.get('sample_id') != 'sample-'+record.digest[:16]
                or row.get('source_system') != record.manifest.get('system')
                or row.get('source_run_id') != record.manifest.get('root_run_id')):
            raise BenchmarkError('review_delivery_organizer_changed')
    review = _child(destination, 'review')
    inventory = read_json(_child(review, 'package_manifest.json'))['files']
    actual = set()
    for path in review.rglob('*'):
        if path.is_symlink():
            raise BenchmarkError('review_delivery_symlink_forbidden')
        if path.is_file() and path.name != 'package_manifest.json':
            actual.add(path.relative_to(review).as_posix())
    if actual != set(inventory):
        raise BenchmarkError('review_delivery_package_file_set_changed')
    for relative, expected in inventory.items():
        if _digest(_child(review, relative)) != expected:
            raise BenchmarkError('review_delivery_package_file_changed')
    archive_path = _child(destination, 'review.zip')
    if not report.get('zip_sha256') or _digest(archive_path) != report['zip_sha256']:
        raise BenchmarkError('review_delivery_zip_changed')
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names) or set(names) != actual | {'package_manifest.json'}:
            raise BenchmarkError('review_delivery_zip_file_set_changed')
        for name in names:
            if sha256(archive.read(name)) != _digest(_child(review, name)):
                raise BenchmarkError('review_delivery_zip_contents_changed')
    return report


def publish_delivery(source):
    """No generation retries; errors are delivery status, never native failures.

    Sealed single runs use export_run directly: automatic delivery status belongs
    only to an unsealed batch/experiment directory outside all evidence roots.
    """
    source = Path(source).absolute()
    status = {'stage': 'review_delivery', 'status': 'failed', 'paid_calls': 0,
              'generation_retry_requested': False, 'reused': False}
    if source.is_symlink() or not source.is_dir() or (source/'manifest.json').exists():
        return {**status, 'error': 'review_delivery_requires_batch_or_experiment_directory'}
    try:
        try:
            runs = discover_runs(source)
        except BenchmarkError as exc:
            if str(exc) != 'playback_no_sealed_runs_found':
                raise
            runs = []
        coverage = collection_coverage(source, runs)
        status['coverage'] = coverage
        if not runs:
            status.update(status='no_sealed_runs', message='尚无已封存的故事；未发送任务不会自动重新生成。')
        else:
            # Recheck all original hashes even when the derived ZIP is reused.
            records = [Recording(run) for run in runs]
            snapshot = {'version': 'review-delivery.1', 'coverage': coverage,
                        'sealed_manifests': sorted(r.digest for r in records),
                        'source_directories': sorted(str(r.root) for r in records),
                        'exporter': _exporter_identity()}
            snapshot_id = sha256(json.dumps(snapshot, sort_keys=True))
            destination = source/'review-deliveries'/snapshot_id[:20]
            if destination.is_symlink() or destination.parent.is_symlink():
                raise BenchmarkError('review_delivery_symlink_forbidden')
            if destination.exists():
                stored = read_json(_child(destination, 'delivery-snapshot.json'))
                if stored != snapshot:
                    raise BenchmarkError('review_delivery_snapshot_conflict')
                report = _verify_existing(destination, records, coverage)
                status['reused'] = True
            else:
                report = export_collection(source, destination)
                atomic_json(destination/'delivery-snapshot.json', snapshot)
            # Retain unknown completeness when no frozen plan was available.
            partial = coverage['all_planned_sealed'] is False or (coverage['unsealed_runs'] or 0)>0
            status.update(status='partial' if partial else 'ready', snapshot_id=snapshot_id,
                          report=report, entry_file=report['recipient_entry_file'],
                          zip_file=report['zip_file'], organizer_file=report['organizer_file'])
    except Exception as exc:
        status.update(status='failed', error_type=type(exc).__name__, error=str(exc))
    try:
        atomic_json(source/'review-delivery.json', status)
        if status['status'] in ('ready', 'partial'):
            expected = status['coverage']['expected_runs']
            coverage_text = ('计划数量未知；此包包含所有当前发现的已封存故事。' if expected is None else
                             f"计划 {expected} 份，已封存 {status['coverage']['sealed_runs']} 份，尚未封存 {status['coverage']['unsealed_runs']} 份。")
            text = ('发给评审者的故事包\n\n发送这个 ZIP：\n'+status['zip_file']+'\n\n'
                    '评审者：解压整个 ZIP，然后双击“打开故事.html”或 index.html。无需安装项目或配置 API。\n'
                    '目录包含全部已封存小说，也保留无正文/失败记录；进入小说后可逐页播放并返回目录。\n'
                    +coverage_text+'\n\n'
                    '组织者保存 organizer.json 与原始 runs/，不要向盲评者发送组织者映射或 API 日志。\n'
                    '原始八项指标不变；本包回放实际生成路径，未探索分支不会补写。\n')
        else:
            text = ('本次没有新的可发送整批评审包。\n状态：'+status['status']+'\n'
                    +status.get('error', status.get('message', ''))+'\n'
                    '请查看 review-delivery.json。已有历史包不会被覆盖；不要把旧包误认为本次完整结果。\n')
        atomic_write(source/'REVIEW_DELIVERY.txt', text)
    except Exception as exc:
        status['status_write_error'] = str(exc)
        print(json.dumps(status, ensure_ascii=False), file=sys.stderr, flush=True)
    return status
