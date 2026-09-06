"""OpenTelemetry 公共初始化。

这里不承载任何 Agent 业务语义，只负责把 trace / log exporter 装好。
业务模块需要 span 时调用 get_tracer；项目自有日志系统需要导出 OTLP
日志时调用 emit_log_record。
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse


_LOCK = threading.RLock()
_INITIALIZED = False
_TRACING_ENABLED = False
_LOGGING_ENABLED = False
_TRACER_PROVIDER: Any = None
_LOGGER_PROVIDER: Any = None
_OTEL_LOGGER: Any = None
_WARNED_LOG_ERROR = False
_REPO_ROOT = Path(__file__).resolve().parents[3]


def setup_opentelemetry() -> None:
    """按环境变量初始化 OTel trace / log。

    支持标准变量:
    - OTEL_EXPORTER_OTLP_ENDPOINT
    - OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
    - OTEL_EXPORTER_OTLP_LOGS_ENDPOINT
    - OTEL_SERVICE_NAME

    需要独立 service.name 的模块可以单独创建 provider，但出口仍复用
    这里解析出来的 Collector endpoint。
    """
    with _LOCK:
        global _INITIALIZED
        if _INITIALIZED:
            return
        _INITIALIZED = True

        _setup_tracing()
        _setup_logging()


def shutdown_opentelemetry() -> None:
    """进程退出时刷出缓存中的 span / log。"""
    with _LOCK:
        for provider in (_TRACER_PROVIDER, _LOGGER_PROVIDER):
            if provider is None:
                continue
            try:
                provider.shutdown()
            except Exception:
                pass


def get_tracer(name: str) -> Any:
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception:
        return None


def emit_log_record(entry: dict[str, Any]) -> None:
    """把项目自有日志追加导出为 OTLP log。

    这个函数是 xlog 的附加出口。OTel 没启用或导出失败时，不影响原有
    文件日志 / stderr 日志。
    """
    if not _LOGGING_ENABLED:
        return

    try:
        logger = _OTEL_LOGGER
        if logger is None:
            return
        logger.emit(
            timestamp=_to_unix_nanos(entry.get("time")),
            severity_number=_severity_number(entry.get("level")),
            severity_text=str(entry.get("level") or "INFO"),
            body=_log_body(entry),
            attributes=_log_attributes(entry),
            exception=entry.get("err"),
        )
    except Exception:
        global _WARNED_LOG_ERROR
        if not _WARNED_LOG_ERROR:
            _WARNED_LOG_ERROR = True


def tracing_enabled() -> bool:
    return _TRACING_ENABLED


def logging_enabled() -> bool:
    return _LOGGING_ENABLED


@contextmanager
def provider_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """为外部供应商调用创建 span。

    调用方只传业务安全字段；不要把 prompt、正文、API key、URL 签名参数放进
    attributes。
    """
    if not tracing_enabled():
        with nullcontext(None) as span:
            yield span
        return
    tracer = get_tracer("if-line.provider")
    if tracer is None:
        with nullcontext(None) as span:
            yield span
        return
    try:
        from opentelemetry import context, trace
    except Exception:
        with nullcontext(None) as span:
            yield span
        return

    span = tracer.start_span(name, attributes=safe_attributes(attributes or {}))
    token = None
    try:
        token = context.attach(trace.set_span_in_context(span))
        yield span
    except Exception as exc:
        _mark_span_failed(span, exc)
        raise
    finally:
        if token is not None:
            try:
                context.detach(token)
            except Exception:
                pass
        try:
            span.end()
        except Exception:
            pass


def json_value(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def json_preview(value: Any, limit: int = 8000) -> str:
    text = json_value(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[已截断]"


def safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in attributes.items():
        safe_value = safe_attribute_value(value)
        if safe_value is not None:
            result[key] = safe_value
    return result


def safe_attribute_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _setup_tracing() -> None:
    endpoint = _trace_endpoint()
    if not endpoint:
        return

    try:
        import requests
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:
        return

    resource = _resource(Resource)
    provider = TracerProvider(resource=resource)
    session = requests.Session()
    session.trust_env = _trust_proxy_env()
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=endpoint,
                session=session,
                timeout=_timeout_seconds(),
            )
        )
    )

    try:
        trace.set_tracer_provider(provider)
    except Exception:
        return

    global _TRACING_ENABLED, _TRACER_PROVIDER
    _TRACING_ENABLED = True
    _TRACER_PROVIDER = provider


def _setup_logging() -> None:
    endpoint = _logs_endpoint()
    if not endpoint:
        return

    try:
        import requests
        from opentelemetry import _logs
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
    except Exception:
        return

    resource = _resource(Resource)
    provider = LoggerProvider(resource=resource)
    session = requests.Session()
    session.trust_env = _trust_proxy_env()
    provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(
                endpoint=endpoint,
                session=session,
                timeout=_timeout_seconds(),
            )
        )
    )

    try:
        _logs.set_logger_provider(provider)
    except Exception:
        return

    global _LOGGING_ENABLED, _LOGGER_PROVIDER, _OTEL_LOGGER
    _LOGGING_ENABLED = True
    _LOGGER_PROVIDER = provider
    _OTEL_LOGGER = _logs.get_logger("if-line.backend")


def _resource(resource_cls: Any) -> Any:
    return resource_cls.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", "if-line-backend"),
            "service.namespace": os.getenv("OTEL_SERVICE_NAMESPACE", "if-line"),
            "deployment.environment": os.getenv("APP_ENV", "development"),
        }
    )


def _trace_endpoint() -> str:
    return otlp_trace_endpoint()


def otlp_trace_endpoint() -> str:
    return os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip() or _join_otlp_endpoint("v1/traces")


def _logs_endpoint() -> str:
    return os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "").strip() or _join_otlp_endpoint("v1/logs")


def _join_otlp_endpoint(suffix: str) -> str:
    base = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not base:
        return ""
    parsed = urlparse(base)
    if parsed.path.endswith("/v1/traces") or parsed.path.endswith("/v1/logs"):
        return base
    return base.rstrip("/") + "/" + suffix


def _timeout_seconds() -> float:
    return otlp_timeout_seconds()


def otlp_timeout_seconds() -> float:
    return float(os.getenv("OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS", "10"))


def _trust_proxy_env() -> bool:
    return otlp_trust_proxy_env()


def otlp_trust_proxy_env() -> bool:
    return os.getenv("OTEL_EXPORTER_OTLP_TRUST_ENV", "false").strip().lower() in {"1", "true", "yes"}


def _to_unix_nanos(value: Any) -> int | None:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000_000)
    return None


def _severity_number(level: Any) -> Any:
    try:
        from opentelemetry._logs import SeverityNumber
    except Exception:
        return None

    normalized = str(level or "INFO").upper()
    if normalized == "TRACE":
        return SeverityNumber.TRACE
    if normalized == "DEBUG":
        return SeverityNumber.DEBUG
    if normalized in {"WARN", "WARNING"}:
        return SeverityNumber.WARN
    if normalized == "ERROR":
        return SeverityNumber.ERROR
    return SeverityNumber.INFO


def _log_attributes(entry: dict[str, Any]) -> dict[str, Any]:
    attributes = {
        "code.function": entry.get("function"),
        "code.filepath": entry.get("file"),
        "code.lineno": entry.get("line"),
        "code.location": _code_location(entry.get("file"), entry.get("line")),
    }
    attributes.update(_current_trace_attributes())
    err = entry.get("err")
    if err is not None:
        attributes["exception.type"] = type(err).__name__
        attributes["exception.message"] = str(err)
    return safe_attributes(attributes)


def _log_body(entry: dict[str, Any]) -> str:
    """VictoriaLogs 的正文区默认只展示 body。

    关联 key 按本地文本日志的形状放在正文开头，避免无语义的 ifline.uuid
    作为 label 干扰检索。
    """
    body = str(entry.get("message") or "")
    err = entry.get("err")
    if err is not None:
        body += f" ERR[{err}]"
    return f"UUID[{_default_log_key(entry.get('uuid'))}] {body}"


def _default_log_key(value: Any) -> Any:
    return 0 if value is None else value


def _code_location(file: Any, line: Any) -> str | None:
    if not file or not line:
        return None
    path = _relative_code_path(str(file))
    return f"{path}:{line}"


def _relative_code_path(file: str) -> str:
    try:
        return str(Path(file).resolve().relative_to(_REPO_ROOT.resolve()))
    except Exception:
        marker = "/backend/"
        index = file.find(marker)
        if index >= 0:
            return file[index + 1 :]
        return file


def _current_trace_attributes() -> dict[str, str]:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if not ctx or not ctx.is_valid:
            return {}
        return {
            "trace_id": f"{ctx.trace_id:032x}",
            "span_id": f"{ctx.span_id:016x}",
        }
    except Exception:
        return {}


def _mark_span_failed(span: Any, exc: Exception) -> None:
    if span is None:
        return
    try:
        span.record_exception(exc)
        span.set_attribute("error.type", type(exc).__name__)
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
    except Exception:
        pass
