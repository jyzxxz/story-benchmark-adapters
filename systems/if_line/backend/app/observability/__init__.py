"""观测能力公共入口。"""

from app.observability.otel import (
    emit_log_record,
    get_tracer,
    logging_enabled,
    provider_span,
    setup_opentelemetry,
    shutdown_opentelemetry,
    tracing_enabled,
)
from app.observability.agent_otel import setup_agent_otel, shutdown_agent_otel

__all__ = [
    "emit_log_record",
    "get_tracer",
    "logging_enabled",
    "provider_span",
    "setup_opentelemetry",
    "setup_agent_otel",
    "shutdown_agent_otel",
    "shutdown_opentelemetry",
    "tracing_enabled",
]
