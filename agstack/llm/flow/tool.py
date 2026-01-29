#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""工具定义和执行"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable


if TYPE_CHECKING:
    from .context import FlowContext


@dataclass
class ToolResult:
    """工具执行结果"""

    name: str
    arguments: dict[str, Any]
    result: Any
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
    ):
        """初始化工具

        :param name: 工具名称
        :param description: 工具描述
        :param function: 工具函数（约定使用 context 作为唯一参数）
        :param parameters: JSON Schema 参数定义（用于 LLM 调用）
        """
        self.name = name
        self.description = description
        self.function = function
        self.parameters = parameters or {"type": "object", "properties": {}, "required": []}

    async def execute_async(self, context: "FlowContext") -> ToolResult:
        """异步执行工具"""
        try:
            # 更可靠的异步检测（Nuitka 兼容）
            result = self.function(context)
            if hasattr(result, "__await__"):
                result = await result

            return ToolResult(name=self.name, arguments={}, result=result, success=True)
        except Exception as e:
            return ToolResult(
                name=self.name,
                arguments={},
                result=None,
                success=False,
                error=str(e),
            )

    async def run(self, context: "FlowContext") -> Any:
        """执行工具"""
        result = await self.execute_async(context)
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
