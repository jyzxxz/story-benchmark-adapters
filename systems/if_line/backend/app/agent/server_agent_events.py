"""
服务端 Agent 事件订阅分发。
"""
import asyncio
import uuid
from typing import Any, Dict, List

from app.utils import logging as xlog


class ServerAgentEventBus:
    """单个 thread 的内存事件订阅器。"""

    def __init__(self, thread_id: str, event_history: List[Dict[str, Any]]):
        self.thread_id = thread_id
        self.event_history = event_history
        self._subscribers: Dict[str, Dict[str, Any]] = {}

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self, after_seq: int = 0, turn_id: str | None = None) -> tuple[str, asyncio.Queue]:
        subscriber_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        for event in self.event_history:
            if turn_id and event.get("turn_id") != turn_id:
                continue
            if int(event.get("seq") or 0) > after_seq:
                queue.put_nowait(event)
        self._subscribers[subscriber_id] = {
            "queue": queue,
            "turn_id": turn_id,
        }
        xlog.info(
            self.thread_id,
            "[server-agent] subscribe events turn_id=%s after_seq=%d subscriber_id=%s count=%d",
            turn_id or "",
            after_seq,
            subscriber_id,
            len(self._subscribers),
        )
        return subscriber_id, queue

    def unsubscribe(self, subscriber_id: str) -> None:
        if self._subscribers.pop(subscriber_id, None) is not None:
            xlog.info(
                self.thread_id,
                "[server-agent] unsubscribe events subscriber_id=%s count=%d",
                subscriber_id,
                len(self._subscribers),
            )

    def publish(self, event: Dict[str, Any]) -> None:
        for item in list(self._subscribers.values()):
            turn_id = item.get("turn_id")
            if turn_id and turn_id != event.get("turn_id"):
                continue
            queue = item["queue"]
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
