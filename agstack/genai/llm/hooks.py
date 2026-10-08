#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""LLM 调用钩子链（F6）：请求发出前拿到完整消息列表并可改写，响应回来后观察

与 :class:`agstack.genai.flow.tool.ToolHook` 同形：模块级全局链 + registry 转发注册，``prepend`` 抢链头。
织入点在 :meth:`agstack.genai.llm.client.LLMClient.chat`（异步，含流式与 vision 转发），所有经 LLMClient 的
聊天请求都穿链；``chat_sync`` 在线程池同步执行，不穿链。

- ``before_call`` 按注册顺序执行，可改写（替换 / 压缩 / 注入）消息列表；抛异常＝整次调用失败
  （fail closed：改写消息的钩子出错时不能让模型看到半成品上下文）。
- ``after_call`` 按逆序执行，纯观察（锚点计量、溢出判定、审计）；抛异常记日志放行（fail open）。
  非流式收到 openai ``ChatCompletion``；流式收到 :class:`StreamSummary`（usage 与 finish_reason），
  正文增量不在此重放——消费流的调用方自己已经拿到。

请求参数类的覆盖（temperature / max_tokens / thinking）走 :meth:`Agent.request_overrides`，不在此重复开缝。
"""

import logging
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CallMeta:
    """一次聊天请求的元信息（钩子只读）

    :param model: 模型名
    :param kind: 调用类型（chat / chat_stream / vision …，与 UsageEvent.kind 同口径）
    :param stream: 是否流式
    :param request_id: 请求链路 ID
    :param extra: 调用方附带的上下文（Agent 传 ``agent`` / ``turn`` / ``retry`` / ``context``），键由调用方约定
    """

    model: str
    kind: str
    stream: bool
    request_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamSummary:
    """流式调用结束时交给 ``after_call`` 的摘要（流本身已被调用方消费）"""

    usage: Any | None
    finish_reason: str | None


class LLMCallHook:
    """LLM 调用钩子基类（全局链，经 registry.register_llm_hook 注册）；子类覆写其一或两者，必须是 async def"""

    async def before_call(self, messages: list[Any], tools: list[Any] | None, meta: CallMeta) -> list[Any]:
        """请求发出前：返回（可改写的）消息列表；抛异常＝本次调用失败（fail closed）"""
        return messages

    async def after_call(self, response: Any, meta: CallMeta) -> None:
        """响应返回后：纯观察；抛异常记日志放行（fail open）"""
        return None


_LLM_HOOKS: list[LLMCallHook] = []


def register_llm_hook(hook: LLMCallHook, *, prepend: bool = False) -> None:
    """注册全局 LLM 调用钩子；prepend=True 抢链头（其 before_call 最先改写、after_call 最后观察）"""
    if prepend:
        _LLM_HOOKS.insert(0, hook)
    else:
        _LLM_HOOKS.append(hook)


def clear_llm_hooks() -> None:
    """清空全局 LLM 调用钩子（测试隔离用）"""
    _LLM_HOOKS.clear()


def has_llm_hooks() -> bool:
    return bool(_LLM_HOOKS)


async def run_before_call(messages: list[Any], tools: list[Any] | None, meta: CallMeta) -> list[Any]:
    """按注册顺序穿 before 链；任一钩子抛异常即向上抛（fail closed）"""
    for hook in _LLM_HOOKS:
        revised = await hook.before_call(messages, tools, meta)
        if not isinstance(revised, list):
            raise TypeError(f"LLM hook {type(hook).__name__}.before_call must return a message list")
        messages = revised
    return messages


async def run_after_call(response: Any, meta: CallMeta) -> None:
    """逆序穿 after 链；钩子异常记日志放行（fail open）"""
    for hook in reversed(_LLM_HOOKS):
        try:
            await hook.after_call(response, meta)
        except Exception as e:
            logger.warning("LLM hook after_call failed in %s: %s", type(hook).__name__, e, exc_info=True)


__all__ = [
    "CallMeta",
    "LLMCallHook",
    "StreamSummary",
    "clear_llm_hooks",
    "has_llm_hooks",
    "register_llm_hook",
    "run_after_call",
    "run_before_call",
]
