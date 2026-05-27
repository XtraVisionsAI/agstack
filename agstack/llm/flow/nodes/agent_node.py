#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Agent 节点处理器 — 从 flow.py 提取"""

from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from .. import event
from ..exceptions import FlowError
from ..registry import registry
from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class AgentNodeHandler(NodeHandler):
    """Agent 节点：通过 registry 查找 agent → ag.stream(context, inputs)"""

    node_type = "agent"

    _NODE_KEYS = frozenset({"agent_name", "inputs", "label", "echo"})

    def _create_agent(self, config: dict, context: "FlowContext"):
        agent_name = config.get("agent_name")
        if not agent_name:
            raise FlowError("MISSING_AGENT_NAME", 400)
        agent_kwargs = {
            k: context.resolve_reference(v) if isinstance(v, str) else v
            for k, v in config.items()
            if k not in self._NODE_KEYS
        }
        agent = registry.create_agent(agent_name, **agent_kwargs)
        if not agent:
            raise FlowError("AGENT_NOT_FOUND", 404, {"agent_name": agent_name})
        return agent

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        resolved = self.resolve_inputs(config, context)
        ag = self._create_agent(config, context)
        result = await ag.run(context, inputs=resolved)
        structured = context.outputs.pop(ag.name, None)
        return structured if structured is not None else result

    async def stream(self, node: dict, context: "FlowContext", node_id: str) -> AsyncIterator[dict[str, Any]]:
        config = node.get("config", {})
        step_name = self.get_step_name(node, node_id)
        sid = str(uuid4())

        yield event.step_started(step_name=step_name, step_id=sid)
        resolved = self.resolve_inputs(config, context)
        ag = self._create_agent(config, context)
        async for evt in ag.stream(context, inputs=resolved):
            yield evt
        structured = context.outputs.pop(ag.name, None)
        if structured is not None:
            context.set_output(node_id, structured)
        else:
            result = context.get_last_output(ag.name) or ""
            context.set_output(node_id, {"result": result})

        # 一次性收集累积的 tool 执行记录，供 flow 引擎存入 NodeTrace
        tool_calls = context.pop_execution_records()
        context.set_variable("_last_node_tool_calls", tool_calls)

        yield event.step_finished(step_name=step_name, step_id=sid)
