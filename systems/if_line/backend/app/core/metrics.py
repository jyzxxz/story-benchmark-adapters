"""Small Prometheus metrics surface for HTTP and readiness observability."""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


HTTP_REQUESTS = Counter(
    "if_line_http_requests_total",
    "Total HTTP requests handled by the API.",
    labelnames=("method", "route", "status"),
)
HTTP_LATENCY = Histogram(
    "if_line_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    labelnames=("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
READINESS_CHECKS = Counter(
    "if_line_readiness_checks_total",
    "Readiness probes by result.",
    labelnames=("result",),
)
TASKS_BY_STATUS = Gauge(
    "if_line_generation_tasks",
    "Durable generation tasks by status.",
    labelnames=("status",),
)
OUTBOX_BACKLOG = Gauge(
    "if_line_outbox_events",
    "Durable outbox events by status.",
    labelnames=("status",),
)
PROVIDER_COST = Gauge(
    "if_line_provider_cost_total",
    "Recorded provider cost grouped by provider and currency.",
    labelnames=("provider", "currency"),
)
PROVIDER_CALLS = Gauge(
    "if_line_provider_calls_total",
    "Recorded provider calls grouped by provider and cache result.",
    labelnames=("provider", "cache_hit"),
)


def refresh_database_metrics() -> None:
    """Refresh DB-backed gauges without turning Redis/Celery into truth sources."""

    from sqlalchemy import func

    from app.database import SessionLocal
    from app.models_v2 import GenerationTask, OutboxEvent, ProviderUsageRecord

    session = SessionLocal()
    try:
        TASKS_BY_STATUS.clear()
        for status, count in session.query(GenerationTask.status, func.count(GenerationTask.id)).group_by(
            GenerationTask.status
        ):
            TASKS_BY_STATUS.labels(status).set(count)

        OUTBOX_BACKLOG.clear()
        for status, count in session.query(OutboxEvent.status, func.count(OutboxEvent.id)).group_by(
            OutboxEvent.status
        ):
            OUTBOX_BACKLOG.labels(status).set(count)

        PROVIDER_COST.clear()
        for provider, currency, amount in session.query(
            ProviderUsageRecord.provider,
            ProviderUsageRecord.cost_currency,
            func.coalesce(func.sum(ProviderUsageRecord.cost_amount), 0),
        ).group_by(ProviderUsageRecord.provider, ProviderUsageRecord.cost_currency):
            PROVIDER_COST.labels(provider, currency).set(float(amount or 0))

        PROVIDER_CALLS.clear()
        for provider, cache_hit, count in session.query(
            ProviderUsageRecord.provider,
            ProviderUsageRecord.cache_hit,
            func.count(ProviderUsageRecord.id),
        ).group_by(ProviderUsageRecord.provider, ProviderUsageRecord.cache_hit):
            PROVIDER_CALLS.labels(provider, str(bool(cache_hit)).lower()).set(count)
    finally:
        session.close()


def render_metrics(*, include_database: bool = True) -> tuple[bytes, str]:
    if include_database:
        refresh_database_metrics()
    return generate_latest(), CONTENT_TYPE_LATEST
