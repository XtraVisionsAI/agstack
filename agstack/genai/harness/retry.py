#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""模型请求的瞬时故障重发（3.4）

``Agent.stream`` 的一次模型请求在**尚未放出任何内容或工具调用**时失败，且错误属于瞬时故障（超时 / 连接中断 /
限流 / 上游 5xx），按 :class:`TransientRetry` 等待后同轮重发；已放出内容的不重发（调用方已看到半截，重发会重复）。
与上下文溢出恢复（:mod:`.overflow`）分开计数——溢出要先压缩再发，瞬时故障只需再发一次。

非流式 chat / embed / rerank 的超时重试在 :mod:`agstack.genai.llm.client` 内；流式作答此前一断即整轮失败，
已取到的工具结果随之丢失，这里补上。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from ..flow.context import FlowContext

#: 执行记录种类（写进当前节点的 ``tool_calls``，随 trace 持久化）
RETRY_RECORD = "llm_transient_retry"
#: ``Agent.stream`` 发出的自定义事件名
RETRY_EVENT = "agent_retry"

#: 视为瞬时故障的异常类名（按类名判定，不 import 各客户端库）
TRANSIENT_ERROR_TYPES = frozenset(
    {
        "TimeoutError",
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionRefusedError",
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "ConnectError",
        "ReadError",
        "RemoteProtocolError",
        "APITimeoutError",
        "APIConnectionError",
        "RateLimitError",
        "InternalServerError",
        "ConnectionTimeout",
        "TransportError",
    }
)
_TRANSIENT_TEXT_RE = re.compile(
    r"\b(429|502|503|504)\b|timed? ?out|too many requests|temporarily unavailable|connection (reset|aborted)", re.I
)


def is_transient_error(exc: BaseException) -> bool:
    """异常链（``__cause__`` / ``__context__``）上有超时 / 连接中断 / 限流 / 上游 5xx 即视为瞬时故障"""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ in TRANSIENT_ERROR_TYPES or _TRANSIENT_TEXT_RE.search(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


@dataclass
class TransientRetry:
    """瞬时故障重发策略（应用构造，挂到 ``Agent.retry``）

    :param max_retries: 一次 ``Agent.stream`` 运行内最多重发几次
    :param delay: 重发前等待秒数（第 n 次等 ``delay * n``）
    :param classify: 判定函数，缺省 :func:`is_transient_error`
    """

    max_retries: int = 1
    delay: float = 1.0
    classify: Callable[[BaseException], bool] = field(default=is_transient_error)

    def should_retry(self, error: BaseException, attempts: int) -> bool:
        """第 ``attempts`` 次（从 0 起）失败后是否再发"""
        return attempts < self.max_retries and bool(self.classify(error))

    def backoff(self, attempts: int) -> float:
        return self.delay * (attempts + 1)


def record_retry(context: FlowContext, agent: str, turn: int, error: BaseException) -> None:
    """写一条重发执行记录（与工具管线写的记录同形）"""
    context.execution_records.append(
        {
            "agent_call_id": context.get_variable("_agent_call_id"),
            "tool_name": RETRY_RECORD,
            "tool_args": {"agent": agent, "turn": turn, "error": type(error).__name__},
            "success": True,
            "result": str(error)[:200],
            "error": None,
            "duration_ms": 0,
            "summary": "模型请求瞬时故障（尚未输出），已同轮重发",
        }
    )


def retry_payload(agent: str, turn: int, attempt: int, error: BaseException) -> dict[str, Any]:
    """``agent_retry`` 事件的 value"""
    return {"agentName": agent, "turn": turn, "attempt": attempt, "error": type(error).__name__}


__all__ = [
    "RETRY_EVENT",
    "RETRY_RECORD",
    "TRANSIENT_ERROR_TYPES",
    "TransientRetry",
    "is_transient_error",
    "record_retry",
    "retry_payload",
]
