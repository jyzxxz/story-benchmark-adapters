"""
Pytest 全局配置。

测试运行时固定独立数据库、无真实供应商密钥的 TTS 配置，以及背景校验开关，
避免任何测试继承开发 ``.env`` 或连接开发数据库。

实施：在 dotenv 加载之前设置环境变量，并 monkeypatch dotenv.load_dotenv 让它
不再覆盖已存在的环境变量（默认行为是覆盖）。
"""
import os
import re
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def _validate_test_database_url(value: str) -> str:
    """Accept only an explicitly named disposable PostgreSQL database."""
    try:
        url = make_url(value)
    except ArgumentError as exc:
        raise RuntimeError("TEST_DATABASE_URL must be a valid SQLAlchemy URL") from exc
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("TEST_DATABASE_URL must use PostgreSQL")
    database_name = (url.database or "").lower()
    if re.fullmatch(r"if_line_pytest_[a-z0-9_]+", database_name) is None:
        raise RuntimeError(
            "TEST_DATABASE_URL database name must match if_line_pytest_*"
        )
    return value


# 在任何应用模块 import 之前固定测试运行时。测试绝不能继承开发环境的
# DATABASE_URL 或供应商密钥；需要 PostgreSQL 时必须显式提供 TEST_DATABASE_URL。
_explicit_test_database_url = os.getenv("TEST_DATABASE_URL", "").strip()
if _explicit_test_database_url:
    _test_database_url = _validate_test_database_url(_explicit_test_database_url)
else:
    _test_database = Path(tempfile.gettempdir()) / f"if-line-pytest-{os.getpid()}.sqlite3"
    _test_database.unlink(missing_ok=True)
    _test_database_url = f"sqlite:///{_test_database.as_posix()}"

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = _test_database_url
os.environ["CHECK_DEPENDENCIES_ON_STARTUP"] = "false"
os.environ["AUTH_RATELIMIT_BACKEND"] = "memory"
os.environ["TTS_ENABLED"] = "false"
os.environ["TTS_ENGINE"] = "aliyun"
os.environ["TTS_FALLBACK_ENABLED"] = "false"

# Empty values take precedence over both the developer shell and backend/.env.
# Tests that exercise a provider must inject a fake key in their own fixture.
_PROVIDER_CREDENTIAL_ENV_VARS = (
    "AI_FLAVOR_API_KEY",
    "AI_FLAVOR_API_KEYS",
    "AI_IMAGE_API_KEY",
    "AI_IMAGE_API_KEYS",
    "DASHSCOPE_API_KEY",
    "ALIYUN_DASHSCOPE_API_KEY",
    "ALIYUN_API_KEY",
    "BG_ANALYZER_API_KEY",
    "BG_ANALYZER_API_KEYS",
    "BG_STYLE_CLASSIFIER_API_KEY",
    "BG_STYLE_CLASSIFIER_API_KEYS",
    "BG_VISION_API_KEY",
    "BG_VISION_API_KEYS",
    "CANONICAL_CHARACTER_RECOGNITION_API_KEY",
    "CANONICAL_CHARACTER_RECOGNITION_API_KEYS",
    "CHAPTER_VOICE_API_KEY",
    "CHAPTER_VOICE_API_KEYS",
    "KEYFRAME_MOMENT_API_KEY",
    "KEYFRAME_MOMENT_API_KEYS",
    "MINIMAX_TTS_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_KEYS",
    "PORTRAIT_DEMAND_API_KEY",
    "PORTRAIT_DEMAND_API_KEYS",
    "REWRITER_API_KEY",
    "REWRITER_API_KEYS",
    "SEGMENTER_API_KEY",
    "SEGMENTER_API_KEYS",
    "STYLE_CLASSIFIER_API_KEY",
    "STYLE_CLASSIFIER_API_KEYS",
    "XUNFEI_TTS_APPID",
    "XUNFEI_TTS_API_KEY",
    "XUNFEI_TTS_API_SECRET",
)
for _credential_name in _PROVIDER_CREDENTIAL_ENV_VARS:
    os.environ[_credential_name] = ""
os.environ["BG_VALIDATE_ENABLED"] = "true"
os.environ["BG_NO_RETRY"] = "0"

# 让 load_dotenv 不覆盖已有 env var（override=False）
import dotenv
_orig_load = dotenv.load_dotenv


def _no_override_load(*args, **kwargs):
    kwargs.setdefault("override", False)
    return _orig_load(*args, **kwargs)


dotenv.load_dotenv = _no_override_load
