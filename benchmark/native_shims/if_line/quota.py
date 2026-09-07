"""Remove only the disposable application's synthetic quota gate in v4.

There is no native credit-grant service. Native reservation, usage ledger,
settlement and retry logic still execute; no real provider account is touched.
"""
import functools
import json
import os
from pathlib import Path


def install(config,runtime):
    if not config.get('batch_media') or not config.get('isolated_deployment'):
        raise ValueError('quota adapter requires isolated v4 deployment')
    from sqlalchemy.engine import make_url
    url=make_url(os.environ['DATABASE_URL'])
    if (url.get_backend_name()!='postgresql' or url.host!='127.0.0.1'
            or not (url.database or '').startswith('if_line_bench_')):
        raise ValueError('quota adapter refuses non-disposable database')
    from app.application import usage_service as usage,task_service
    from app.models import User
    from app.models_v2 import UsageReservation
    from app.quota import reset_daily_quota_if_needed,quota_snapshot
    def ensure(db,task,amount):
        units=usage._legacy_units(usage._credits(amount))
        if not units: return
        user=db.query(User).filter_by(id=task.user_id).with_for_update().one()
        reset_daily_quota_if_needed(db,user)
        before=quota_snapshot(user)
        total=max(0,user.quota_used_total+units-user.quota_total)
        daily=max(0,user.quota_used_daily+units-user.quota_daily)
        if not total and not daily: return
        user.quota_total+=total;user.quota_daily+=daily;db.flush()
        record={'event_type':'internal_credit_topup','root_run_id':config['root_run_id'],
            'native_task_id':task.id,'native_user_id':user.id,'amount':{'total':total,'daily':daily},
            'native_request_credit_units':units,'before':before,'after':quota_snapshot(user),
            'reason':'remove_disposable_test_deployment_quota_gate_under_unlimited_policy',
            'native_amount_source':'app.application.usage_service._credits/_legacy_units',
            'native_reservation_service':'app.application.usage_service.reserve_usage/reopen_usage',
            'native_credit_grant_service':None,'provider_currency_amount':None,
            'transaction_scope':'same_native_reservation_transaction_commit_required'}
        with (Path(runtime)/'internal-credit-topups.jsonl').open('a') as stream:
            stream.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
    original_reserve=usage.reserve_usage
    @functools.wraps(original_reserve)
    def reserve(db,task,amount):
        ensure(db,task,amount)
        return original_reserve(db,task,amount)
    original_reopen=usage.reopen_usage
    @functools.wraps(original_reopen)
    def reopen(db,task):
        reservation=db.query(UsageReservation).filter_by(task_id=task.id).first()
        if not reservation or reservation.status in {'released','cancelled'}:
            amount=(reservation.reserved_amount if reservation else None) or task.estimated_cost or usage.ZERO
            ensure(db,task,amount)
        return original_reopen(db,task)
    usage.reserve_usage=task_service.reserve_usage=reserve
    usage.reopen_usage=task_service.reopen_usage=reopen
