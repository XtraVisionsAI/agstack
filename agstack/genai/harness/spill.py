#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""超长工具结果落盘（spill）：ToolHook post 钩子

纪律（借 dsh spill-policy）：``prepend`` 挂链头使其成为最外层 post；按 ``max_inline_tokens`` 判定；全文经
:class:`~.ports.SpillStore` 落盘；上下文保头尾 + 固定格式通知（含 locator，模型可用应用提供的读取工具回读）；
read 类工具排除（回读结果再落盘会死循环）；**存储失败只 warn 保留内联**（可用性优先于预算）。

与截断的关系：clamp 是「丢弃」，spill 是「移位」，两者并存——先 spill 保全文，再 clamp 决定内联多少。
钩子作用于 ``ToolResult.content``（喂给模型的字符串，Tool 在 post 链前已算好），不改 ``result.result`` 的形状；
落盘引用写进 ``ToolResult.metadata["spill"]``。
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..flow.tool import ToolHook, ToolResult
from .ports import SpillOwner, SpillRef, SpillSource, get_ports
from .truncation import TokenCounter


if TYPE_CHECKING:
    from ..flow.context import FlowContext
    from ..flow.tool import Tool

logger = logging.getLogger(__name__)

#: 通知模板；{tokens} / {chars} 原文规模，{locator} 回读定位符，{name} 建议文件名
DEFAULT_NOTICE = (
    "\n[... 工具结果约 {tokens} tokens / {chars} 字符，超出内联上限，全文已存为 {name}（locator={locator}）；"
    "此处只保留开头与结尾，需要细节时按 locator 分段读取 ...]\n"
)


@dataclass(slots=True)
class SpillPolicy:
    """落盘策略（数值与名单由应用给）

    :param max_inline_tokens: 内联上限；``content`` 超过即落盘
    :param exclude_tools: 不落盘的工具名（read 类回读工具必须在列）
    :param head_chars / tail_chars: 内联保留的头尾字符数
    :param count_tokens: token 计数函数（应用按模型绑定）
    :param owner_of: 从 FlowContext 解析归属（用户 / 会话 / 任务），实现据此定目录与密级
    :param model_of: 从 FlowContext 解析模型名（仅供日志）
    :param notice: 通知模板
    """

    max_inline_tokens: int
    count_tokens: TokenCounter
    owner_of: Callable[["FlowContext"], SpillOwner]
    exclude_tools: frozenset[str] = field(default_factory=frozenset)
    head_chars: int = 1200
    tail_chars: int = 400
    notice: str = DEFAULT_NOTICE


class SpillHook(ToolHook):
    """post 钩子：content 超内联上限时经 SpillStore 落盘并替换为头尾 + 通知；未注册 SpillStore 时为空操作"""

    def __init__(self, policy: SpillPolicy):
        self.policy = policy

    async def post_execute(self, context: "FlowContext", tool: "Tool", result: ToolResult) -> ToolResult:
        policy = self.policy
        if tool.name in policy.exclude_tools or not result.success:
            return result
        content = result.content
        if not content:
            return result
        store = get_ports().spill
        if store is None:
            return result
        tokens = policy.count_tokens(content)
        if tokens <= policy.max_inline_tokens:
            return result
        if len(content) <= policy.head_chars + policy.tail_chars:
            return result
        name = f"{tool.name}-{context.get_variable('_agent_call_id') or context.context_id[:8]}.txt"
        try:
            ref = await store.save_text(
                policy.owner_of(context),
                SpillSource(tool=tool.name, call_id=context.get_variable("_agent_call_id"), arguments=result.arguments),
                name,
                content,
            )
        except Exception as e:  # 存储失败只 warn，保留内联
            logger.warning("spill store failed for tool %s, keeping inline content: %s", tool.name, e, exc_info=True)
            return result
        result.content = render_spilled(content, ref, policy)
        result.metadata["spill"] = {"locator": ref.locator, "chars": ref.chars, "tokens": ref.tokens, "name": ref.name}
        return result


def render_spilled(content: str, ref: SpillRef, policy: SpillPolicy) -> str:
    """头尾保留 + 固定格式通知"""
    head = content[: policy.head_chars]
    tail = content[len(content) - policy.tail_chars :]
    notice = policy.notice.format(tokens=ref.tokens, chars=ref.chars, locator=ref.locator, name=ref.name)
    return head + notice + tail


__all__ = ["DEFAULT_NOTICE", "SpillHook", "SpillPolicy", "render_spilled"]
