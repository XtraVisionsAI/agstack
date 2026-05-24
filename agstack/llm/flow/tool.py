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
        """异步执行工具（包含计时、摘要生成、结果格式化、可观测性记录）"""
        args = inputs or {}
        _t0 = time.perf_counter()
        result = await self._execute(context, args)
        _duration_ms = int((time.perf_counter() - _t0) * 1000)

        # 计算 LLM 消费内容
        if self.result_formatter:
            try:
                result.content = self.result_formatter(result)
            except Exception:
                result.content = json.dumps(result.result) if result.success else json.dumps({"error": result.error})
        else:
            result.content = json.dumps(result.result) if result.success else json.dumps({"error": result.error})

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
