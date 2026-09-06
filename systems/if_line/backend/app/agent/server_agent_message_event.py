"""
服务端 Agent 的 Message 窗口和 Event 日志。

这里不放 Agent 主流程，只收拢两组容易散落的运行时状态：
- ServerAgentMessageWindow：模型 messages 窗口与数据库 message_index 的对应关系。
- ServerAgentEventLog：thread 事件列表、递增 seq，以及 SSE 订阅者。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from app.agent.server_agent_events import ServerAgentEventBus
from app.agent.server_agent_model_message import filter_model_message_fields


class ServerAgentMessageWindow:
    """
    管理当前喂给模型的 messages 窗口，以及它们对应的数据库 message_index。

    这里分两层看：
      - 数据库 agent_messages 是审计账本，永远按创建顺序追加；
      - 内存 messages 是当前喂给模型的窗口，压缩后会按“模型阅读顺序”重排。

    压缩前，内存和数据库基本一致：
      内存 messages:
        0 system-prompt
        1 第一轮 user
        2 第一轮 assistant
        3 第二轮 user
      数据库 agent_messages:
        0 system-prompt
        1 第一轮 user
        2 第一轮 assistant
        3 第二轮 user

    第二轮请求模型前触发压缩时，后端把 1、2 压成摘要，并追加一条新消息：
      数据库 agent_messages:
        0 system-prompt
        1 第一轮 user              旧历史，保留用于审计
        2 第一轮 assistant         旧历史，保留用于审计
        3 第二轮 user              最近窗口，原文保留
        4 context_compaction 摘要  新增

    随后运行时重建内存窗口：
      内存 messages:
        0 system-prompt
        4 context_compaction 摘要
        3 第二轮 user

    注意：这里 4 排在 3 前面是刻意的。message_index 只表示数据库写入顺序；
    内存窗口表示模型阅读顺序，必须让“旧历史摘要”出现在“最近用户输入”之前。

    split-turn 是同一套恢复规则的扩展。比如一个大 turn 里已经发生多次 toolcall：
      数据库 agent_messages:
        0 system-prompt
        1 第一轮 user
        2 第一轮 assistant
        3 第二轮 user：请连续查很多东西
        4 assistant tool_call A
        5 tool A result
        6 assistant tool_call B       最近窗口从这里开始
        7 tool B result
        8 context_compaction 摘要     新增，摘要里包含 1/2 和 3/4/5

    恢复出的内存窗口是：
      0 system-prompt
      8 context_compaction 摘要
      6 assistant tool_call B
      7 tool B result

    这里不会恢复成 0/1/2/3/4/5/6/7/8；否则压缩就失效了。
    """

    def __init__(
        self,
        active_messages: List[Dict[str, Any]],
        active_message_db_indexes: List[int],
        next_persisted_message_index: int,
    ):
        self.items = active_messages
        self.db_indexes = active_message_db_indexes
        self.next_index = next_persisted_message_index
        self.last_persisted_index = next_persisted_message_index - 1

    @classmethod
    def initial(cls, system_message: Dict[str, Any]) -> "ServerAgentMessageWindow":
        return cls(
            active_messages=[system_message],
            active_message_db_indexes=[0],
            next_persisted_message_index=1,
        )

    @classmethod
    def restored(
        cls,
        active_messages: List[Dict[str, Any]],
        active_message_db_indexes: List[int] | None,
        next_persisted_message_index: int | None,
    ) -> "ServerAgentMessageWindow":
        indexes = active_message_db_indexes or list(range(len(active_messages)))
        next_index = int(next_persisted_message_index or len(active_messages))
        return cls(
            active_messages=active_messages,
            active_message_db_indexes=indexes,
            next_persisted_message_index=next_index,
        )

    @property
    def active_count(self) -> int:
        return len(self.items)

    @property
    def persisted_count(self) -> int:
        return self.next_index

    def append(self, message: Dict[str, Any]) -> int:
        message_index = self.next_index
        self.items.append(message)
        self.db_indexes.append(message_index)
        self.next_index += 1
        self.last_persisted_index = message_index
        return message_index

    def apply_compaction(
        self,
        summary_message: Dict[str, Any],
        kept_messages: List[Dict[str, Any]],
        kept_db_indexes: List[int],
    ) -> int:
        summary_index = self.next_index
        self.next_index += 1
        self.last_persisted_index = summary_index
        self.items = [
            self.items[0],
            filter_model_message_fields(summary_message),
            *[filter_model_message_fields(message) for message in kept_messages],
        ]
        self.db_indexes = [
            self.db_indexes[0],
            summary_index,
            *kept_db_indexes,
        ]
        return summary_index


class ServerAgentEventLog:
    """管理 thread 事件列表、递增 seq，以及当前 SSE 订阅者。"""

    def __init__(
        self,
        thread_id: str,
        events: List[Dict[str, Any]] | None = None,
        next_event_seq: int | None = None,
    ):
        self.items = events or []
        self.next_seq = int(next_event_seq or (len(self.items) + 1))
        self.bus = ServerAgentEventBus(thread_id, self.items)

    @property
    def count(self) -> int:
        return self.next_seq - 1

    @property
    def subscriber_count(self) -> int:
        return self.bus.subscriber_count

    def assign_seq(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event["seq"] = self.next_seq
        self.next_seq += 1
        return event

    def append_and_publish(self, event: Dict[str, Any]) -> None:
        self.items.append(event)
        self.bus.publish(event)

    def subscribe(self, after_seq: int, turn_id: str | None) -> tuple[str, asyncio.Queue]:
        return self.bus.subscribe(after_seq=after_seq, turn_id=turn_id)

    def unsubscribe(self, subscriber_id: str) -> None:
        self.bus.unsubscribe(subscriber_id)
