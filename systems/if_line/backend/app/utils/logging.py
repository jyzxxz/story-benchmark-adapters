"""
项目日志封装。
"""
import inspect
import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TextIO


_LEVEL_TRACE = "TRACE"
_LEVEL_INFO = "INFO"
_LEVEL_DEBUG = "DEBUG"
_LEVEL_WARN = "WARN"
_LEVEL_ERROR = "ERROR"
_LEVEL_ORDER = {
    _LEVEL_TRACE: 10,
    _LEVEL_DEBUG: 20,
    _LEVEL_INFO: 30,
    _LEVEL_WARN: 40,
    _LEVEL_ERROR: 50,
}
_LOCK = threading.RLock()
_SINKS: list["_Sink"] = []
_MIN_LEVEL = _LEVEL_INFO
_DEFAULT_CONFIG = {
    "level": _LEVEL_INFO,
    "file": {
        "enabled": True,
        "path": "python-backend.local.log",
    },
    "json_stderr": False,
}


class _Sink:
    def write_log(self, entry: dict[str, Any]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None


class _TextSink(_Sink):
    def __init__(self, fp: TextIO):
        self.fp = fp
        self.mu = threading.Lock()

    def write_log(self, entry: dict[str, Any]) -> None:
        with self.mu:
            self.fp.write(_format_text(entry))
            self.fp.flush()

    def close(self) -> None:
        if self.fp not in (sys.stdout, sys.stderr):
            self.fp.close()


class _JSONSink(_Sink):
    def __init__(self, fp: TextIO):
        self.fp = fp
        self.mu = threading.Lock()

    def write_log(self, entry: dict[str, Any]) -> None:
        payload = {
            "time": entry["time"].astimezone().isoformat(timespec="milliseconds"),
            "level": entry["level"],
            "uuid": _default_uuid(entry["uuid"]),
            "message": entry["message"],
            "function": entry["function"],
            "file": entry["file"],
            "line": entry["line"],
        }
        if entry["err"] is not None:
            payload["error"] = str(entry["err"])
        data = json.dumps(payload, ensure_ascii=False)
        with self.mu:
            self.fp.write(data + "\n")
            self.fp.flush()


def setup_logger(root: Optional[Path] = None) -> None:
    """按 Go 后端的默认形状初始化日志输出。"""
    root = root or Path(__file__).resolve().parents[3]
    cfg = _load_config()

    with _LOCK:
        global _MIN_LEVEL
        _MIN_LEVEL = _normalize_level(cfg.get("level"))
        close()
        file_cfg = cfg.get("file", {})
        if file_cfg.get("enabled", True):
            log_file = _resolve_path(root, str(file_cfg.get("path") or "python-backend.local.log"))
            log_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                _SINKS.append(_TextSink(log_file.open("a", encoding="utf-8")))
            except OSError:
                _SINKS.append(_TextSink(sys.stderr))
        if cfg.get("json_stderr") is True:
            _SINKS.append(_JSONSink(sys.stderr))


def close() -> None:
    with _LOCK:
        old_sinks = list(_SINKS)
        _SINKS.clear()
    for sink in old_sinks:
        sink.close()


def trace(uuid: Any, msg: str, *args: Any) -> None:
    _write(_LEVEL_TRACE, uuid, _format_msg(msg, args), None, 2)


def info(uuid: Any, msg: str, *args: Any) -> None:
    _write(_LEVEL_INFO, uuid, _format_msg(msg, args), None, 2)


def debug(uuid: Any, msg: str, *args: Any) -> None:
    _write(_LEVEL_DEBUG, uuid, _format_msg(msg, args), None, 2)


def warn(uuid: Any, msg: str, *args: Any) -> None:
    _write(_LEVEL_WARN, uuid, _format_msg(msg, args), None, 2)


def error(uuid: Any, err: BaseException, msg: str, *args: Any) -> None:
    _write(_LEVEL_ERROR, uuid, _format_msg(msg, args), err, 2)


def _write(level: str, uuid: Any, msg: str, err: Optional[BaseException], caller_skip: int) -> None:
    if not _should_write(level):
        return

    if not _SINKS:
        setup_logger()

    function, file, line = _caller(caller_skip)
    entry = {
        "time": datetime.now().astimezone(),
        "level": level,
        "uuid": uuid,
        "message": msg,
        "function": function,
        "file": file,
        "line": line,
        "err": err,
    }

    with _LOCK:
        sinks = list(_SINKS)
    for sink in sinks:
        try:
            sink.write_log(entry)
        except Exception:
            pass

    try:
        from app.observability import emit_log_record

        emit_log_record(entry)
    except Exception:
        pass


def _caller(skip: int) -> tuple[str, str, int]:
    # inspect.stack() 成本比 runtime.Caller 高，只在业务日志点使用，不放进高频循环。
    stack = inspect.stack()
    index = min(skip + 1, len(stack) - 1)
    frame = stack[index]
    return frame.function, frame.filename, frame.lineno


def _format_text(entry: dict[str, Any]) -> str:
    level = str(entry["level"])
    if len(level) < 5:
        level += " " * (5 - len(level))

    message = entry["message"]
    if entry["err"] is not None:
        message += f" ERR[{entry['err']}]"

    timestamp = entry["time"].strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return (
        f"[{level} | {timestamp}] | UUID[{_default_uuid(entry['uuid'])}] "
        f"{message} FUNC[{entry['function']}] FILE[{entry['file']}:{entry['line']}]\n"
    )


def _default_uuid(value: Any) -> Any:
    return 0 if value is None else value


def _format_msg(msg: str, args: tuple[Any, ...]) -> str:
    if not args:
        return msg
    try:
        return msg % args
    except TypeError:
        return msg.format(*args)


def _normalize_level(value: Any) -> str:
    level = str(value or _LEVEL_INFO).upper()
    if level == "WARNING":
        level = _LEVEL_WARN
    if level not in _LEVEL_ORDER:
        return _LEVEL_INFO
    return level


def _should_write(level: str) -> bool:
    current = _normalize_level(level)
    minimum = _MIN_LEVEL
    return _LEVEL_ORDER[current] >= _LEVEL_ORDER[minimum]


def _load_config() -> dict[str, Any]:
    config_path = Path(__file__).resolve().parents[1] / "config" / "logging.json"
    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT_CONFIG)

    if not isinstance(data, dict):
        return dict(_DEFAULT_CONFIG)

    cfg = dict(_DEFAULT_CONFIG)
    if "level" in data:
        cfg["level"] = data["level"]
    file_cfg = dict(_DEFAULT_CONFIG["file"])
    if isinstance(data.get("file"), dict):
        file_cfg.update(data["file"])
    cfg["file"] = file_cfg
    if isinstance(data.get("json_stderr"), bool):
        cfg["json_stderr"] = data["json_stderr"]
    return cfg


def _resolve_path(root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return root / path
