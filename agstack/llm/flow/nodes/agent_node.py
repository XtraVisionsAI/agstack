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
    """Agent 节点：通过 registry 查找 agent → ag.stream(context, inputs)"""

    node_type = "agent"

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
        resolved = self.resolve_inputs(config, context)
        ag = self._create_agent(config)
        return await ag.run(context, inputs=resolved)

    async def stream(self, node: dict, context: "FlowContext", node_id: str) -> AsyncIterator[dict[str, Any]]:
        config = node.get("config", {})
        step_name = self.get_step_name(node, node_id)

        yield event.step_started(step_name=step_name)
        resolved = self.resolve_inputs(config, context)
        ag = self._create_agent(config)
        async for evt in ag.stream(context, inputs=resolved):
            yield evt
        result = context.get_last_output(ag.name) or ""
        context.set_output(node_id, {"result": result})
        yield event.step_finished(step_name=step_name)
