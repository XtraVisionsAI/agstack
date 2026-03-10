#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""Flow 定义和执行"""

import json as _json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from . import event
from .exceptions import FlowError
from .registry import registry


if TYPE_CHECKING:
    from .context import FlowContext


class _SafeFormatDict(dict):
    """安全的模板变量替换，缺失 key 时保留原始占位符"""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


@dataclass
class Flow:
    """Flow 配置定义"""

    flow_id: str
    name: str
    description: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)

    # ── 边驱动路由 ──

    def _resolve_next_node(self, current_id: str, result: str | None = None) -> str | None:
        """根据当前节点和执行结果，通过 edges 查找下一节点"""
        for edge in self.edges:
            if edge.get("source") == current_id:
                cond = edge.get("condition")
                if cond is None or cond == result:
                    return edge.get("target")
        return None

    # ── condition 节点 ──

    async def _evaluate_condition(self, node: dict, context: "FlowContext") -> str:
        """调用 LLM 判断条件是否匹配"""
        config = node.get("config", {})
        topic = config.get("topic", "")
        query = context.get_variable("query", "")

        prompt = (
            f"判断以下问题是否属于「{topic}」相关问题。\n"
            f"问题：{query}\n"
            f'仅回复 JSON：{{"result": "match"}} 或 {{"result": "reject"}}'
        )

        from ..client import get_llm_client

        client = get_llm_client()
        response = await client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=config.get("model", "gpt-4o-mini"),
            temperature=0,
        )
        text = response.choices[0].message.content or ""
        try:
            return _json.loads(text).get("result", "reject")
        except Exception:
            return "match" if "match" in text.lower() else "reject"

    # ── message 节点 ──

    async def _emit_message(self, node: dict, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """输出模板文本"""
        config = node.get("config", {})
        template = config.get("content", "")
        text = template.format_map(_SafeFormatDict(context.variables))
        msg_id = context.message_id or str(uuid4())
        yield event.text_message_start(message_id=msg_id, role="assistant")
        yield event.text_message_content(message_id=msg_id, delta=text)
        yield event.text_message_end(message_id=msg_id)

    # ── 执行入口 ──

    async def run(self, context: "FlowContext") -> dict[str, Any]:
        """执行 Flow"""
        if not self.edges:
            # 向后兼容：无 edges 时按 nodes 列表顺序执行
            for node in self.nodes:
                node_id = node.get("id")
                if not node_id:
                    continue
                context.current_node = node_id
                result = await self._execute_node(node, context)
                context.set_node_result(node_id, result)
        else:
            # edge 驱动执行
            current_node_id: str | None = self.nodes[0]["id"] if self.nodes else None
            while current_node_id:
                node = self.get_node_config(current_node_id)
                if not node:
                    break
                context.current_node = current_node_id
                node_type = node.get("type")

                if node_type == "condition":
                    result = await self._evaluate_condition(node, context)
                    context.set_node_result(current_node_id, result)
                    current_node_id = self._resolve_next_node(current_node_id, result)
                elif node_type == "message":
                    config = node.get("config", {})
                    template = config.get("content", "")
                    text = template.format_map(_SafeFormatDict(context.variables))
                    context.set_node_result(current_node_id, text)
                    current_node_id = self._resolve_next_node(current_node_id, "done")
                elif node_type in ("agent", "tool"):
                    result = await self._execute_node(node, context)
                    context.set_node_result(current_node_id, result)
                    current_node_id = self._resolve_next_node(current_node_id, "done")
                else:
                    break

        return context.node_results

    async def stream(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """流式执行 Flow（输出 AG-UI 标准事件）"""
        yield event.step_started(step_name=f"flow:{self.name}")

        if not self.edges:
            # 向后兼容：无 edges 时按 nodes 列表顺序执行（原有逻辑）
            async for evt in self._stream_sequential(context):
                yield evt
        else:
            # edge 驱动执行
            async for evt in self._stream_edge_driven(context):
                yield evt

        yield event.step_finished(step_name=f"flow:{self.name}")

    async def _stream_sequential(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """顺序流式执行（原有逻辑）"""
        for node in self.nodes:
            node_id = node.get("id")
            if not node_id:
                continue

            context.current_node = node_id
            yield event.step_started(step_name=f"node:{node_id}")

            if node.get("type") == "agent":
                agent_name = node.get("config", {}).get("agent_name", "")
                yield event.step_started(step_name=f"agent:{agent_name}")
                self._set_parameters(node.get("config", {}), context)
                ag = self._create_agent(node.get("config", {}))
                async for evt in ag.stream(context):
                    yield evt
                result = context.get_last_output(ag.name) or ""
                context.set_node_result(node_id, result)
                yield event.step_finished(step_name=f"agent:{agent_name}")
            else:
                tool_name = node.get("config", {}).get("tool_name", "")
                yield event.step_started(step_name=f"tool:{tool_name}")
                result = await self._execute_node(node, context)
                context.set_node_result(node_id, result)
                yield event.step_finished(step_name=f"tool:{tool_name}")

    async def _stream_edge_driven(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """边驱动流式执行"""
        current_node_id: str | None = self.nodes[0]["id"] if self.nodes else None

        while current_node_id:
            node = self.get_node_config(current_node_id)
            if not node:
                break

            context.current_node = current_node_id
            node_type = node.get("type")

            if node_type == "condition":
                result = await self._evaluate_condition(node, context)
                context.set_node_result(current_node_id, result)
                current_node_id = self._resolve_next_node(current_node_id, result)

            elif node_type == "message":
                async for evt in self._emit_message(node, context):
                    yield evt
                current_node_id = self._resolve_next_node(current_node_id, "done")

            elif node_type == "agent":
                agent_name = node.get("config", {}).get("agent_name", "")
                yield event.step_started(step_name=f"agent:{agent_name}")
                self._set_parameters(node.get("config", {}), context)
                ag = self._create_agent(node.get("config", {}))
                async for evt in ag.stream(context):
                    yield evt
                result = context.get_last_output(ag.name) or ""
                context.set_node_result(current_node_id, result)
                yield event.step_finished(step_name=f"agent:{agent_name}")
                current_node_id = self._resolve_next_node(current_node_id, "done")

            elif node_type == "tool":
                tool_name = node.get("config", {}).get("tool_name", "")
                yield event.step_started(step_name=f"tool:{tool_name}")
                result = await self._execute_node(node, context)
                context.set_node_result(current_node_id, result)
                yield event.step_finished(step_name=f"tool:{tool_name}")
                current_node_id = self._resolve_next_node(current_node_id, "done")

            else:
                break

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
