#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Agent 节点处理器 — 从 flow.py 提取"""

from typing import TYPE_CHECKING, Any, AsyncIterator

from .. import event
from ..exceptions import FlowError
from ..registry import registry
from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class AgentNodeHandler(NodeHandler):
    """Agent 节点：通过 registry 查找 agent → ag.stream(context)"""

    node_type = "agent"

    def _set_parameters(self, config: dict, context: "FlowContext") -> None:
        parameters = config.get("parameters", {})
        for key, value in parameters.items():
            resolved = context.resolve_reference(value) if isinstance(value, str) else value
            context.set_variable(key, resolved)

    def _create_agent(self, config: dict):
        agent_name = config.get("agent_name")
        if not agent_name:
            raise FlowError("MISSING_AGENT_NAME", 400)
        agent = registry.create_agent(agent_name)
        if not agent:
            raise FlowError("AGENT_NOT_FOUND", 404, {"agent_name": agent_name})
        return agent

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        self._set_parameters(config, context)
        ag = self._create_agent(config)
        return await ag.run(context)

    async def stream(self, node: dict, context: "FlowContext", node_id: str) -> AsyncIterator[dict[str, Any]]:
        config = node.get("config", {})
        step_name = self.get_step_name(node, node_id)

        yield event.step_started(step_name=step_name)
        self._set_parameters(config, context)
        ag = self._create_agent(config)
        async for evt in ag.stream(context):
            yield evt
        result = context.get_last_output(ag.name) or ""
        context.set_node_result(node_id, result)
        self.map_outputs(config, context, {"result": result})
        yield event.step_finished(step_name=step_name)
