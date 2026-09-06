"""SQLite 兼容层。

models_v2 的 AgentMessage/AgentEvent/AgentTraceEvent 用 BigInteger 主键自增。
SQLite 只对「INTEGER PRIMARY KEY」自动填充 rowid, BIGINT 主键插入会报
NOT NULL(见 evals 首次运行)。这里把 BigInteger 在 sqlite 方言下编译成
INTEGER(SQLite 的 INTEGER 本身就是 64 位), 仅影响 evals 进程, 不动应用代码。

PostgreSQL 下本模块无效(compiler 只挂 sqlite 方言)。
"""
from __future__ import annotations

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.types import BigInteger


@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer(type_, compiler, **kw):  # noqa: ARG001
    return "INTEGER"
