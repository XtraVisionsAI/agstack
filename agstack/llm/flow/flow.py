#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""Flow 定义和执行"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

from .events import EventType
from .exceptions import FlowError
from .registry import registry


if TYPE_CHECKING:
    from .context import FlowContext


@dataclass
class Flow:
    """Flow 配置定义"""

    flow_id: str
    name: str
    description: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)

    async def run(self, context: "FlowContext") -> dict[str, Any]:
        """执行 Flow"""
        # 简单的顺序执行（可扩展为拓扑排序）
        for node in self.nodes:
            node_id = node.get("id")
            if not node_id:
                continue

            context.current_node = node_id
            result = await self._execute_node(node, context)
            context.set_node_result(node_id, result)

        return context.node_results

    async def stream(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """流式执行 Flow（输出 AG-UI 标准事件）"""
        # 发送开始事件
        yield {"type": EventType.TEXT_MESSAGE_CONTENT, "message_id": "", "delta": f"**开始执行流程**: {self.name}\n\n"}

        # 按顺序执行节点
        for node in self.nodes:
            node_id = node.get("id")
            if not node_id:
                continue

            context.current_node = node_id

            # 发送节点开始事件
            yield {"type": EventType.TEXT_MESSAGE_CONTENT, "message_id": "", "delta": f"**执行节点**: {node_id}\n"}

            # 执行节点
            if node.get("type") == "agent":
                # Agent 节点 - 流式执行
                agent_name = node.get("config", {}).get("agent_name", "")
                yield {
                    "type": EventType.TEXT_MESSAGE_CONTENT,
                    "message_id": "",
                    "delta": f"正在调用智能体 {agent_name}...\n\n",
                }

                # 设置参数
                self._set_parameters(node.get("config", {}), context)

                # 创建并流式执行 Agent
                agent = self._create_agent(node.get("config", {}))
                async for event in agent.stream(context):
                    yield event

                # 获取最终结果
                result = context.messages[-1]["content"] if context.messages else ""
                context.set_node_result(node_id, result)

                yield {"type": EventType.TEXT_MESSAGE_CONTENT, "message_id": "", "delta": "\n\n智能体执行完成\n\n"}

            else:
                # Tool 节点 - 非流式执行
                tool_name = node.get("config", {}).get("tool_name", "")
                yield {
                    "type": EventType.TEXT_MESSAGE_CONTENT,
                    "message_id": "",
                    "delta": f"正在调用工具 {tool_name}...\n",
                }

                result = await self._execute_node(node, context)
                context.set_node_result(node_id, result)

                yield {"type": EventType.TEXT_MESSAGE_CONTENT, "message_id": "", "delta": "工具执行完成\n\n"}

        # 发送完成事件
        yield {"type": EventType.TEXT_MESSAGE_CONTENT, "message_id": "", "delta": f"**流程执行完成**: {self.name}"}
        yield {"type": EventType.TEXT_MESSAGE_END, "message_id": ""}

    async def _execute_node(self, node_config: dict, context: "FlowContext") -> Any:
        """执行节点"""
        node_type = node_config.get("type")
        config = node_config.get("config", {})

        # 设置参数到 context
        self._set_parameters(config, context)

        # 创建并执行 runnable
        if node_type == "agent":
            runnable = self._create_agent(config)
        elif node_type == "tool":
            runnable = self._create_tool(config)
        else:
            raise FlowError("UNKNOWN_NODE_TYPE", 400, {"type": node_type})

        return await runnable.run(context)

    def _set_parameters(self, config: dict, context: "FlowContext") -> None:
        """设置参数到 context"""
        parameters = config.get("parameters", {})

        for key, value in parameters.items():
            resolved_value = context.resolve_reference(value) if isinstance(value, str) else value
            context.set_variable(key, resolved_value)

    def _create_agent(self, config: dict):
        """创建 Agent"""
        agent_name = config.get("agent_name")
        if not agent_name:
            raise FlowError("MISSING_AGENT_NAME", 400)

        agent = registry.create_agent(agent_name)
        if not agent:
            raise FlowError("AGENT_NOT_FOUND", 404, {"agent_name": agent_name})

        return agent

    def _create_tool(self, config: dict):
        """创建 Tool"""
        tool_name = config.get("tool_name")
        if not tool_name:
            raise FlowError("MISSING_TOOL_NAME", 400)

        tool = registry.create_tool(tool_name)
        if not tool:
            raise FlowError("TOOL_NOT_FOUND", 404, {"tool_name": tool_name})

        return tool

    def get_node_config(self, node_id: str) -> dict[str, Any] | None:
        """获取节点配置"""
        for node in self.nodes:
            if node.get("id") == node_id:
                return node
        return None
