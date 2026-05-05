#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Switch 节点 — 纯变量匹配路由，零 LLM 调用开销"""

from typing import TYPE_CHECKING, Any

from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class SwitchNodeHandler(NodeHandler):
    """条件路由节点

    读取 flow 变量值，与 cases 映射表匹配，返回路由键。
    用于根据系统配置在运行时选择不同执行路径。

    输出：{"choice": "<matched_case>"}
    """

    node_type = "switch"

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        variable_ref = config.get("variable", "")
        cases: dict[str, str] = config.get("cases", {})
        default = config.get("default")

        value = context.resolve_reference(variable_ref)
        value_str = str(value) if value is not None else ""

        if value_str in cases:
            choice = value_str
        elif default is not None:
            choice = default
        else:
            choice = value_str

        return {"choice": choice}
