#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""NodeHandler 基类 — 所有执行类节点的公共接口"""

from typing import TYPE_CHECKING, Any, AsyncIterator

from .. import event


if TYPE_CHECKING:
    from ..context import FlowContext


class NodeHandler:
    """内置节点处理器基类

    所有执行类节点（agent / tool / python / llm_chat / llm_embed / llm_rerank / detect）
    都继承此基类，由 Flow 引擎统一分发。
    """

    node_type: str  # 节点类型标识，子类必须设置

    def get_step_name(self, node: dict, node_id: str) -> str:
        """step 事件标签，子类可覆盖"""
        config = node.get("config", {})
        label = config.get("agent_name") or config.get("tool_name") or node_id
        return f"{self.node_type}:{label}"

    def resolve_inputs(self, config: dict, context: "FlowContext") -> dict[str, Any]:
        """解析输入变量引用"""
        inputs_spec = config.get("inputs", {})
        return {k: context.resolve_reference(v) if isinstance(v, str) else v for k, v in inputs_spec.items()}

    def map_outputs(self, config: dict, context: "FlowContext", result: dict) -> None:
        """将结果映射到 context.variables"""
        for key in config.get("outputs", {}):
            if isinstance(result, dict) and key in result:
                context.set_variable(key, result[key])

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        """执行节点，返回结果（将存入 node_results）

        子类必须实现此方法。
        """
        raise NotImplementedError

    async def stream(self, node: dict, context: "FlowContext", node_id: str) -> AsyncIterator[dict[str, Any]]:
        """流式执行，产出 AG-UI 事件

        默认实现：产出 step_started，调 execute()，产出 step_finished。
        需要流式输出的节点（如 agent, llm_chat）应覆盖此方法。
        """
        step_name = self.get_step_name(node, node_id)
        yield event.step_started(step_name=step_name)
        result = await self.execute(node, context)
        context.set_node_result(node_id, result)
        config = node.get("config", {})
        self.map_outputs(config, context, result)
        yield event.step_finished(step_name=step_name)
