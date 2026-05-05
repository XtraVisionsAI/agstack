#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Subflow 节点 — 引用并执行另一个 Flow 配置，实现流程复用"""

from typing import TYPE_CHECKING, Any, AsyncIterator

from ..exceptions import FlowError
from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class SubflowNodeHandler(NodeHandler):
    """子流程节点

    通过 flow_name 加载另一个 Flow 实例，在当前 FlowContext 上执行。
    子 flow 与父 flow 共享同一个 FlowContext。
    """

    node_type = "subflow"

    def _load_subflow(self, config: dict):
        """加载子 flow 实例"""
        from ..loader import FlowLoader
        from ..registry import registry

        flow_name = config.get("flow_name", "")

        # 优先从 registry 获取已注册的 flow
        sub_flow = registry.create_flow(flow_name)
        if sub_flow is not None:
            return sub_flow

        # 其次从内联配置加载
        flow_config = config.get("flow_config")
        if flow_config is not None:
            return FlowLoader.load_from_dict(flow_config)

        raise FlowError("SUBFLOW_NOT_FOUND", args={"flow_name": flow_name})

    def _resolve_and_apply_inputs(self, config: dict, context: "FlowContext") -> None:
        """解析 inputs 并更新 context.variables"""
        inputs_spec = config.get("inputs", {})
        for key, ref in inputs_spec.items():
            value = context.resolve_reference(ref) if isinstance(ref, str) else ref
            context.set_variable(key, value)

    def _get_last_node_output(self, sub_flow, context: "FlowContext") -> Any:
        """获取子 flow 最后一个节点的输出"""
        if sub_flow.nodes:
            last_node_id = sub_flow.nodes[-1].get("id")
            if last_node_id and last_node_id in context.outputs:
                return context.outputs[last_node_id]
        return context.outputs

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        self._resolve_and_apply_inputs(config, context)
        sub_flow = self._load_subflow(config)
        await sub_flow.run(context)
        return self._get_last_node_output(sub_flow, context)

    async def stream(self, node: dict, context: "FlowContext", node_id: str) -> AsyncIterator[dict[str, Any]]:
        config = node.get("config", {})
        self._resolve_and_apply_inputs(config, context)
        sub_flow = self._load_subflow(config)

        async for evt in sub_flow.stream(context):
            yield evt

        result = self._get_last_node_output(sub_flow, context)
        context.set_output(node_id, result)
