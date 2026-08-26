#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""工具定义和执行"""

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable


if TYPE_CHECKING:
    from .context import FlowContext

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """工具执行结果"""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    success: bool
    error: str | None = None
    content: str | None = None
    summary: str | None = None


class Deny:
    """pre_execute 的拒绝决策：工具本体不执行，reason 作为失败结果反馈给模型"""

    def __init__(self, reason: str):
        self.reason = reason


class ToolHook:
    """工具执行钩子基类（全局链，经 registry.register_tool_hook 注册）

    子类覆写其一或两者；方法必须是 async def。
    pre_execute 按注册顺序、post_execute 按逆序执行（洋葱模型）。
    F3 并行工具落地后钩子会被并发调用：实现必须无状态或自行同步
    （与 Tool 单例的既有纪律同构）。
    """

    async def pre_execute(
        self, context: "FlowContext", tool: "Tool", inputs: dict[str, Any]
    ) -> "dict[str, Any] | Deny":
        """工具入参进入工具函数前调用

        返回（可改写的）inputs 继续执行；返回 Deny 拒绝执行；
        抛异常按 Deny 处理（fail closed：权限门自己出错时不放行）。
        """
        return inputs

    async def post_execute(self, context: "FlowContext", tool: "Tool", result: "ToolResult") -> "ToolResult":
        """结果落入上下文前调用，原样返回＝纯观察

        可替换/截断/落盘换 locator；改写对 LLM 消费内容、用户摘要、
        execution_records 三个出口同时生效。抛异常记日志并放行原结果
        （fail open：审计钩子的 bug 不毁掉主流程）。
        Deny 产生的失败结果同样穿过 post 链（审计要看到被拒绝的调用）。
        """
        return result


_TOOL_HOOKS: list[ToolHook] = []


def register_tool_hook(hook: ToolHook, *, prepend: bool = False) -> None:
    """注册全局工具执行钩子，对所有 Tool.execute_async 生效

    prepend=True 抢占链头（其 post_execute 成为最外层，适合 spill/截断类钩子）。
    """
    if prepend:
        _TOOL_HOOKS.insert(0, hook)
    else:
        _TOOL_HOOKS.append(hook)


def clear_tool_hooks() -> None:
    """清空全局工具钩子（测试隔离用）"""
    _TOOL_HOOKS.clear()


class Tool:
    """工具定义"""

    def __init__(
        self,
        name: str,
        description: str,
        function: Callable,
        parameters: dict[str, Any] | None = None,
        *,
        label: str | None = None,
        echo: bool = False,
        category: str | None = None,
        concurrency_safe: bool = False,
        summary_fn: Callable[["ToolResult"], str | None] | None = None,
        result_formatter: Callable[["ToolResult"], str] | None = None,
        progress_label_fn: Callable[[dict[str, Any]], str] | None = None,
    ):
        """初始化工具

        :param name: 工具名称
        :param description: 工具描述
        :param function: 工具函数，签名 fn(context, inputs) -> dict[str, Any]
        :param parameters: JSON Schema 参数定义（用于 LLM 调用）
        :param label: 面向用户的展示名称（控制 STEP/TOOL_CALL 进度事件可见性）
        :param echo: 是否转发 TEXT_MESSAGE 给用户
        :param category: 工具分类（retrieval / analysis / action / utility）
        :param concurrency_safe: 声明该工具可与其它 concurrency_safe 工具并发执行（默认串行）
        :param summary_fn: 生成面向用户摘要的函数 (ToolResult) -> str | None
        :param result_formatter: 自定义 LLM 内容格式化函数 (ToolResult) -> str
        :param progress_label_fn: 基于调用参数生成动态进度描述 (args) -> str
        """
        self.name = name
        self.description = description
        self.function = function
        self.parameters = parameters or {"type": "object", "properties": {}, "required": []}
        self.label = label
        self.echo = echo
        self.category = category
        self.concurrency_safe = concurrency_safe
        self.summary_fn = summary_fn
        self.result_formatter = result_formatter
        self.progress_label_fn = progress_label_fn

    def get_progress_label(self, args: dict[str, Any]) -> str | None:
        """基于调用参数生成面向用户的动态进度描述

        返回 None 时不发送进度事件。
        """
        if self.progress_label_fn:
            try:
                return self.progress_label_fn(args)
            except Exception:
                pass
        return self.label

    async def execute_async(self, context: "FlowContext", inputs: dict[str, Any] | None = None) -> ToolResult:
        """异步执行工具（包含钩子链、计时、摘要生成、结果格式化、可观测性记录）"""
        args = inputs or {}
        _t0 = time.perf_counter()

        # pre 钩子链（注册顺序）：可改写入参；返回 Deny 或抛异常＝拒绝执行（fail closed）
        result: ToolResult | None = None
        for hook in _TOOL_HOOKS:
            try:
                outcome = await hook.pre_execute(context, self, args)
            except Exception as e:
                logger.warning("Tool hook pre_execute failed for %s: %s", self.name, e, exc_info=True)
                outcome = Deny(f"tool hook error: {e}")
            if isinstance(outcome, Deny):
                result = ToolResult(name=self.name, arguments=args, result={}, success=False, error=outcome.reason)
                break
            args = outcome

        if result is None:
            result = await self._execute(context, args)

        # post 钩子链（逆序）：可改写结果；抛异常＝放行原结果（fail open）。
        # Deny 的失败结果同样穿过 post 链，审计钩子能看到被拒绝的调用。
        for hook in reversed(_TOOL_HOOKS):
            try:
                revised = await hook.post_execute(context, self, result)
            except Exception as e:
                logger.warning("Tool hook post_execute failed for %s: %s", self.name, e, exc_info=True)
                continue
            if isinstance(revised, ToolResult):
                result = revised
            else:
                logger.warning("Tool hook post_execute for %s returned %r, ignored", self.name, type(revised))

        _duration_ms = int((time.perf_counter() - _t0) * 1000)

        # 计算 LLM 消费内容
        if self.result_formatter:
            try:
                result.content = self.result_formatter(result)
            except Exception:
                result.content = (
                    json.dumps(result.result, ensure_ascii=False)
                    if result.success
                    else json.dumps({"error": result.error}, ensure_ascii=False)
                )
        else:
            result.content = (
                json.dumps(result.result, ensure_ascii=False)
                if result.success
                else json.dumps({"error": result.error}, ensure_ascii=False)
            )

        # 生成面向用户的摘要
        if self.summary_fn:
            try:
                result.summary = self.summary_fn(result)
            except Exception:
                result.summary = None

        context.execution_records.append(
            {
                "agent_call_id": context.get_variable("_agent_call_id"),
                "tool_name": self.name,
                "tool_args": args,
                "success": result.success,
                "result": result.content,
                "error": result.error,
                "duration_ms": _duration_ms,
                "summary": result.summary,
            }
        )

        return result

    async def _execute(self, context: "FlowContext", inputs: dict[str, Any]) -> ToolResult:
        """实际执行逻辑，子类应覆写此方法"""
        try:
            result = self.function(context, inputs)
            if hasattr(result, "__await__"):
                result = await result

            return ToolResult(name=self.name, arguments=inputs, result=result, success=True)
        except Exception as e:
            logger.warning("Tool %s failed: %s", self.name, e, exc_info=True)
            return ToolResult(
                name=self.name,
                arguments=inputs,
                result={},
                success=False,
                error=str(e),
            )

    async def run(self, context: "FlowContext", inputs: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """执行工具"""
        result = await self.execute_async(context, inputs)
        return result.result if result.success else None

    def to_openai_tool(self) -> dict[str, Any]:
        """转换为 OpenAI 工具格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
