#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Tool 节点处理器 — 从 flow.py 提取"""

from typing import TYPE_CHECKING, Any

from ..exceptions import FlowError, ToolExecutionError
from ..registry import registry
from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class ToolNodeHandler(NodeHandler):
    """Tool 节点：通过 registry 查找 tool → tool.run(context, inputs)"""

    node_type = "tool"

    def _create_tool(self, config: dict):
        tool_name = config.get("tool_name")
        if not tool_name:
            raise FlowError("MISSING_TOOL_NAME", 400)
        tool = registry.create_tool(tool_name)
        if not tool:
            raise FlowError("TOOL_NOT_FOUND", 404, {"tool_name": tool_name})
        return tool

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        resolved = self.resolve_inputs(config, context)
        tool = self._create_tool(config)
        result = await tool.execute_async(context, inputs=resolved)
        if not result.success:
            raise ToolExecutionError("TOOL_EXECUTION_FAILED", args={"tool_name": tool.name, "error": result.error})
        return result.result
