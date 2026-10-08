#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""上下文溢出：三态判定 + 一次 compact-and-retry（agstack-as-harness.md §3.1 B3；3.0）

模型请求把上下文窗口撑爆有三种表现，这里统一判定（机制），压缩怎么做由应用给（策略）：

- **error**：后端直接报错——消息里带「context length / maximum context / prompt is too long / too many tokens …」；
  限流（429 / rate limit）不算溢出，交给普通重试；
- **silent**：请求成功、``finish_reason`` 正常，但实报 ``prompt_tokens`` 已超过窗口——网关静默截掉了前文，
  模型看到的是残缺上下文；
- **length**：``finish_reason="length"`` 且几乎没有输出（``completion_tokens`` ≈ 0），输入又占满了窗口的 99%——
  输出预算被输入吃光。

:class:`OverflowPolicy` 把判定接到 ``Agent.stream``：命中后先 yield 一条 ``agent_overflow`` CUSTOM 事件，再调应用的
``compact(context, kind)`` 协程压缩 ``context.history`` / 本 agent 消息（折叠、摘要、遮蔽都由应用做），压缩有成效即以同
一轮重发请求；每次运行最多恢复 ``max_recoveries`` 次（缺省 1），再次溢出按原路报错。只在本次尝试尚未向用户放出任何
文字 / 工具调用时才恢复——流式文字已出就不能回收，重发会重复。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from ..flow.context import FlowContext

#: 溢出三态
OVERFLOW_ERROR = "error"
OVERFLOW_SILENT = "silent"
OVERFLOW_LENGTH = "length"

#: 应用把恢复动作写执行记录时建议的 ``tool_name``（审计呈现）
OVERFLOW_RECORD = "overflow_recovery"

#: 后端溢出报错的常见措辞（OpenAI / vLLM / Anthropic / Bedrock / 国内网关）
_OVERFLOW_RE = re.compile(
    r"context[ _-]?(length|window)|maximum context|context_length_exceeded"
    r"|max(imum)?[ _-]?(input[ _-]?)?tokens?|too many tokens|token limit|input length"
    r"|prompt is too long|input is too long|exceeds? the (maximum|limit|context)"
    r"|reduce (the length of )?(the|your) (messages|prompt|input)",
    re.IGNORECASE,
)
#: 限流措辞：命中即不算溢出
_RATE_LIMIT_RE = re.compile(r"rate[ _-]?limit|too many requests|\b429\b|quota", re.IGNORECASE)

#: 「length 态」要求输入至少占窗口的比例
LENGTH_WINDOW_RATIO = 0.99

Compactor = Callable[["FlowContext", str], Awaitable[bool]]
WindowResolver = Callable[["FlowContext"], "int | None"]


def classify_overflow(
    *,
    error: BaseException | str | None = None,
    finish_reason: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    context_length: int | None = None,
    window_ratio: float = LENGTH_WINDOW_RATIO,
) -> str | None:
    """判定一次模型请求是否溢出，返回三态之一或 None

    有 ``error`` 时只看错误文本（限流优先排除）；否则按 usage 与窗口判 ``length`` / ``silent``，缺窗口或缺实报即 None。
    """
    if error is not None:
        text = str(error)
        if _RATE_LIMIT_RE.search(text):
            return None
        return OVERFLOW_ERROR if _OVERFLOW_RE.search(text) else None
    if not context_length or prompt_tokens is None:
        return None
    if finish_reason == "length" and not completion_tokens and prompt_tokens >= int(context_length * window_ratio):
        return OVERFLOW_LENGTH
    if prompt_tokens > context_length:
        return OVERFLOW_SILENT
    return None


@dataclass(frozen=True)
class OverflowPolicy:
    """溢出恢复策略（应用构造，挂到 ``Agent.overflow``）

    :param compact: ``async (context, kind) -> bool``：压缩 ``context.history`` / ``context.messages[agent]``，
        返回是否有成效（False 即不重发、按原路报错）
    :param context_length: 窗口 token 数，或 ``(context) -> int | None`` 的解析函数（如读 flow 变量）；
        None 则只能判 error 态
    :param max_recoveries: 一次 ``Agent.stream`` 运行内最多恢复几次
    """

    compact: Compactor
    context_length: int | WindowResolver | None = None
    max_recoveries: int = 1

    def window(self, context: FlowContext) -> int | None:
        """本次运行的窗口 token 数"""
        if callable(self.context_length):
            value = self.context_length(context)
        else:
            value = self.context_length
        return int(value) if value else None


def usage_tokens(usage: Any) -> tuple[int | None, int | None]:
    """从 usage 对象 / dict 取 ``(prompt_tokens, completion_tokens)``（缺失为 None）"""
    if usage is None:
        return None, None
    if isinstance(usage, dict):
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    else:
        prompt, completion = getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
    return (int(prompt) if prompt is not None else None), (int(completion) if completion is not None else None)


__all__ = [
    "LENGTH_WINDOW_RATIO",
    "OVERFLOW_ERROR",
    "OVERFLOW_LENGTH",
    "OVERFLOW_RECORD",
    "OVERFLOW_SILENT",
    "Compactor",
    "OverflowPolicy",
    "WindowResolver",
    "classify_overflow",
    "usage_tokens",
]
