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
    ):
        """初始化工具

        :param name: 工具名称
        :param description: 工具描述
        :param function: 工具函数，签名 fn(context, inputs) -> dict[str, Any]
        :param parameters: JSON Schema 参数定义（用于 LLM 调用）
        :param label: 面向用户的展示名称（控制 STEP/TOOL_CALL 进度事件可见性）
        :param echo: 是否转发 TEXT_MESSAGE 给用户
        """
        self.name = name
        self.description = description
        self.function = function
        self.parameters = parameters or {"type": "object", "properties": {}, "required": []}
        self.label = label
        self.echo = echo

    async def execute_async(self, context: "FlowContext", inputs: dict[str, Any] | None = None) -> ToolResult:
        """异步执行工具（包含计时和可观测性记录）"""
        args = inputs or {}
        _t0 = time.perf_counter()
        result = await self._execute(context, args)
        _duration_ms = int((time.perf_counter() - _t0) * 1000)

        result_content = json.dumps(result.result) if result.success else json.dumps({"error": result.error})
        context.execution_records.append({
            "agent_call_id": context.get_variable("_agent_call_id"),
            "tool_name": self.name,
            "tool_args": args,
            "success": result.success,
            "result": result_content,
            "error": result.error,
            "duration_ms": _duration_ms,
        })

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
