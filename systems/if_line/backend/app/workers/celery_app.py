from __future__ import annotations

from celery import Celery
from celery import signals
from dotenv import load_dotenv

from app.core.config import BACKEND_ROOT, get_settings
from app.observability import get_tracer, setup_opentelemetry, tracing_enabled


load_dotenv(BACKEND_ROOT / ".env")
setup_opentelemetry()
settings = get_settings()
celery_app = Celery("if_line", broker=settings.resolved_celery_broker_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_store_errors_even_if_ignored=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "if_line.run_generation_task": {"queue": "text"},
        "if_line.generate_branch_candidates": {"queue": "text"},
        "if_line.generate_reading_continuation": {"queue": "text"},
        "if_line.run_asset_task": {"queue": "image"},
        "if_line.voice_slice": {"queue": "audio"},
        "if_line.tts_render": {"queue": "audio"},
        "if_line.vngraph_compile": {"queue": "compile"},
        "if_line.complete_selected_branch": {"queue": "compile"},
        "if_line.dispatch_outbox": {"queue": "maintenance"},
        "if_line.recover_stale_tasks": {"queue": "maintenance"},
        "if_line.purge_deleted_media": {"queue": "maintenance"},
    },
    beat_schedule={
        "dispatch-durable-outbox": {
            "task": "if_line.dispatch_outbox",
            "schedule": 1.0,
        },
        "recover-stale-generation-tasks": {
            "task": "if_line.recover_stale_tasks",
            "schedule": 60.0,
        },
        "purge-deleted-media": {
            "task": "if_line.purge_deleted_media",
            "schedule": 3600.0,
        },
    },
)
celery_app.autodiscover_tasks(["app.workers"])


@signals.task_prerun.connect
def _start_task_span(task_id=None, task=None, args=None, kwargs=None, **_):
    if task is None or not tracing_enabled():
        return
    tracer = get_tracer("if-line.celery")
    if tracer is None:
        return
    span = tracer.start_span(
        f"celery {task.name}",
        attributes={
            "messaging.system": "celery",
            "messaging.operation": "process",
            "messaging.message.id": task_id or "",
            "celery.task.name": task.name,
            "celery.task.args_count": len(args or ()),
            "celery.task.kwargs_count": len(kwargs or {}),
        },
    )
    try:
        from opentelemetry import context, trace

        token = context.attach(trace.set_span_in_context(span))
        setattr(task.request, "_otel_context_token", token)
    except Exception:
        pass
    setattr(task.request, "_otel_span", span)


@signals.task_postrun.connect
def _finish_task_span(task_id=None, task=None, state=None, retval=None, **_):
    if task is None:
        return
    span = getattr(task.request, "_otel_span", None)
    if span is None:
        return
    try:
        span.set_attribute("messaging.message.id", task_id or "")
        span.set_attribute("celery.task.state", state or "")
        if state == "FAILURE":
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, "Celery task failed"))
        if isinstance(retval, Exception):
            span.record_exception(retval)
    except Exception:
        pass
    finally:
        token = getattr(task.request, "_otel_context_token", None)
        if token is not None:
            try:
                from opentelemetry import context

                context.detach(token)
            except Exception:
                pass
        span.end()
