from __future__ import annotations


class NonRetryableTaskError(RuntimeError):
    """任务输入或业务前置条件不满足，不应由 worker 自动重试。"""

    def __init__(self, *, code: str, safe_detail: str, diagnostic: str | None = None) -> None:
        super().__init__(diagnostic or safe_detail)
        self.code = code
        self.safe_detail = safe_detail
        self.diagnostic = diagnostic or safe_detail
