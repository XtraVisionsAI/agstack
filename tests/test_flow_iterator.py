#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Iterator 节点及 Agent instructions 注入测试"""

import asyncio

from agstack.llm.flow.context import FlowContext
from agstack.llm.flow.flow import Flow
from agstack.llm.flow.nodes.iterator_node import IteratorNodeHandler
from agstack.llm.flow.registry import registry
from agstack.llm.flow.tool import Tool


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def collect_events(flow: Flow, context: FlowContext) -> list[dict]:
    events = []
    async for evt in flow.stream(context):
        events.append(evt)
    return events


# ── 辅助 mock tool ──


def _echo_tool_fn(context, inputs):
    return {"echo": inputs}


def _failing_tool_fn(context, inputs):
    raise RuntimeError("tool_failed")


def setup_module():
    """注册测试用 mock tools"""
    registry.register_tool(
        "test_echo",
        Tool(name="test_echo", description="Echo inputs", function=_echo_tool_fn),
    )
    registry.register_tool(
        "test_fail",
        Tool(name="test_fail", description="Always fails", function=_failing_tool_fn),
    )


# ── IteratorNodeHandler 单元测试 ──


class TestIteratorNodeHandler:
    """iterator 节点处理器单元测试"""

    def setup_method(self):
        self.handler = IteratorNodeHandler()

    def test_resolve_items_from_variable(self):
        ctx = FlowContext(variables={"tasks": [{"type": "a"}, {"type": "b"}]})
        config = {"items": "$v.tasks"}
        items = self.handler._resolve_items(config, ctx)
        assert items == [{"type": "a"}, {"type": "b"}]

    def test_resolve_items_from_output(self):
        ctx = FlowContext()
        ctx.set_output("planner", {"tasks": [1, 2, 3]})
        config = {"items": "$o.planner.tasks"}
        items = self.handler._resolve_items(config, ctx)
        assert items == [1, 2, 3]

    def test_resolve_items_none_returns_empty(self):
        ctx = FlowContext()
        config = {"items": "$v.missing"}
        items = self.handler._resolve_items(config, ctx)
        assert items == []

    def test_resolve_items_non_list_wraps(self):
        ctx = FlowContext(variables={"single": "value"})
        config = {"items": "$v.single"}
        items = self.handler._resolve_items(config, ctx)
        assert items == ["value"]

    def test_set_item_output(self):
        ctx = FlowContext()
        state = {"items": ["a", "b", "c"], "index": 1, "collected": ["prev"]}
        self.handler._set_item_output("loop", state, {"collect_to": "results"}, ctx)
        output = ctx.outputs["loop"]
        assert output["current_item"] == "b"
        assert output["index"] == 1
        assert output["count"] == 3
        assert output["done"] is False
        assert output["results"] == ["prev"]

    def test_set_done_output(self):
        ctx = FlowContext()
        state = {"items": ["a", "b"], "index": 2, "collected": ["r1", "r2"]}
        self.handler._set_done_output("loop", state, {"collect_to": "results"}, ctx)
        output = ctx.outputs["loop"]
        assert output["done"] is True
        assert output["count"] == 2
        assert output["results"] == ["r1", "r2"]

    def test_build_event_with_interpolation(self):
        config = {
            "events": {
                "on_start": {
                    "name": "iteration_started",
                    "value": {"total": "$count", "first": "$item"},
                }
            }
        }
        state = {"items": ["x", "y", "z"], "index": 0, "collected": []}
        evt = self.handler._build_event(config, "on_start", state)
        assert evt is not None
        assert evt["type"] == "CUSTOM"
        assert evt["name"] == "iteration_started"
        assert evt["value"]["total"] == 3
        assert evt["value"]["first"] == "x"

    def test_build_event_missing_config_returns_none(self):
        evt = self.handler._build_event({}, "on_start", {"items": [], "index": 0, "collected": []})
        assert evt is None

    def test_execute_returns_done(self):
        """execute() 返回 done 状态（用于非 edge-driven 场景）"""
        ctx = FlowContext(variables={"items": [1, 2, 3]})
        node = {"id": "loop", "type": "iterator", "config": {"items": "$v.items"}}
        result = run(self.handler.execute(node, ctx))
        assert result["done"] is True
        assert result["count"] == 3


# ── Flow 集成测试：基本迭代 ──


class TestIteratorFlowIntegration:
    """iterator 节点 + edge-driven flow 集成测试"""

    def test_basic_iteration(self):
        """遍历 3 个 items，每轮调用 echo tool，收集结果"""
        flow = Flow(
            flow_id="test",
            name="iter_test",
            nodes=[
                {
                    "id": "loop",
                    "type": "iterator",
                    "config": {
                        "items": "$v.tasks",
                        "collect_to": "results",
                    },
                },
                {
                    "id": "do_work",
                    "type": "tool",
                    "config": {
                        "tool_name": "test_echo",
                        "inputs": {"query": "$o.loop.current_item"},
                    },
                },
                {
                    "id": "done_node",
                    "type": "python",
                    "config": {
                        "code": "def main():\n    return {'status': 'done'}",
                    },
                },
            ],
            edges=[
                {"source": "loop", "target": "do_work"},
                {"source": "do_work", "target": "loop"},
                {"source": "loop", "target": "done_node", "condition": "$o.loop.done"},
            ],
        )
        ctx = FlowContext(variables={"tasks": ["task_a", "task_b", "task_c"]})
        run(collect_events(flow, ctx))

        # 验证迭代完成
        loop_output = ctx.outputs["loop"]
        assert loop_output["done"] is True
        assert loop_output["count"] == 3
        assert len(loop_output["results"]) == 3

        # 验证每轮结果是 echo tool 的输出
        for i, result in enumerate(loop_output["results"]):
            assert result == {"echo": {"query": f"task_{chr(ord('a') + i)}"}}

    def test_empty_items_goes_to_completion(self):
        """空数组直接走 completion edge"""
        flow = Flow(
            flow_id="test",
            name="iter_empty",
            nodes=[
                {
                    "id": "loop",
                    "type": "iterator",
                    "config": {"items": "$v.tasks", "collect_to": "results"},
                },
                {"id": "body", "type": "tool", "config": {"tool_name": "test_echo", "inputs": {}}},
                {
                    "id": "end",
                    "type": "python",
                    "config": {"code": "def main():\n    return {'status': 'done'}"},
                },
            ],
            edges=[
                {"source": "loop", "target": "body"},
                {"source": "body", "target": "loop"},
                {"source": "loop", "target": "end", "condition": "$o.loop.done"},
            ],
        )
        ctx = FlowContext(variables={"tasks": []})
        run(collect_events(flow, ctx))

        loop_output = ctx.outputs["loop"]
        assert loop_output["done"] is True
        assert loop_output["results"] == []
        # end 节点应该被执行
        assert "end" in ctx.outputs

    def test_max_iterations_truncates(self):
        """max_iterations 截断循环"""
        flow = Flow(
            flow_id="test",
            name="iter_max",
            nodes=[
                {
                    "id": "loop",
                    "type": "iterator",
                    "config": {
                        "items": "$v.tasks",
                        "collect_to": "results",
                        "max_iterations": 2,
                    },
                },
                {
                    "id": "body",
                    "type": "tool",
                    "config": {"tool_name": "test_echo", "inputs": {"x": "$o.loop.current_item"}},
                },
                {
                    "id": "end",
                    "type": "python",
                    "config": {"code": "def main():\n    return {'status': 'truncated'}"},
                },
            ],
            edges=[
                {"source": "loop", "target": "body"},
                {"source": "body", "target": "loop"},
                {"source": "loop", "target": "end", "condition": "$o.loop.done"},
            ],
        )
        ctx = FlowContext(variables={"tasks": ["a", "b", "c", "d", "e"]})
        run(collect_events(flow, ctx))

        loop_output = ctx.outputs["loop"]
        assert loop_output["done"] is True
        assert len(loop_output["results"]) == 2

    def test_error_tolerance(self):
        """单项失败不中断循环，记录 error 后继续"""
        flow = Flow(
            flow_id="test",
            name="iter_error",
            nodes=[
                {
                    "id": "loop",
                    "type": "iterator",
                    "config": {"items": "$v.tasks", "collect_to": "results"},
                },
                {
                    "id": "body",
                    "type": "tool",
                    "config": {"tool_name": "test_fail", "inputs": {}},
                },
                {
                    "id": "end",
                    "type": "python",
                    "config": {"code": "def main():\n    return {'status': 'done'}"},
                },
            ],
            edges=[
                {"source": "loop", "target": "body"},
                {"source": "body", "target": "loop"},
                {"source": "loop", "target": "end", "condition": "$o.loop.done"},
            ],
        )
        ctx = FlowContext(variables={"tasks": ["a", "b"]})
        run(collect_events(flow, ctx))

        loop_output = ctx.outputs["loop"]
        assert loop_output["done"] is True
        assert len(loop_output["results"]) == 2
        # 每个结果都应该包含 error
        for result in loop_output["results"]:
            assert "error" in result

    def test_custom_events_emitted(self):
        """验证 CUSTOM 事件发射"""
        flow = Flow(
            flow_id="test",
            name="iter_events",
            nodes=[
                {
                    "id": "loop",
                    "type": "iterator",
                    "config": {
                        "items": "$v.tasks",
                        "collect_to": "results",
                        "events": {
                            "on_start": {"name": "loop_started", "value": {"total": "$count"}},
                            "on_item_start": {"name": "item_begin", "value": {"idx": "$index"}},
                            "on_item_end": {"name": "item_done", "value": {"idx": "$index"}},
                        },
                    },
                },
                {
                    "id": "body",
                    "type": "tool",
                    "config": {"tool_name": "test_echo", "inputs": {}},
                },
                {
                    "id": "end",
                    "type": "python",
                    "config": {"code": "def main():\n    return {'status': 'ok'}"},
                },
            ],
            edges=[
                {"source": "loop", "target": "body"},
                {"source": "body", "target": "loop"},
                {"source": "loop", "target": "end", "condition": "$o.loop.done"},
            ],
        )
        ctx = FlowContext(variables={"tasks": ["x", "y"]})
        events = run(collect_events(flow, ctx))

        custom_events = [e for e in events if e.get("type") == "CUSTOM"]
        custom_names = [e.get("name") for e in custom_events]

        assert "loop_started" in custom_names
        assert "item_begin" in custom_names
        assert "item_done" in custom_names

    def test_with_switch_routing(self):
        """与 switch 节点组合实现按类型分发"""

        # 注册两个不同的 tool
        def tool_upper(context, inputs):
            return {"result": inputs.get("text", "").upper()}

        def tool_lower(context, inputs):
            return {"result": inputs.get("text", "").lower()}

        registry.register_tool(
            "test_upper",
            Tool(name="test_upper", description="Uppercase", function=tool_upper),
        )
        registry.register_tool(
            "test_lower",
            Tool(name="test_lower", description="Lowercase", function=tool_lower),
        )

        flow = Flow(
            flow_id="test",
            name="iter_switch",
            nodes=[
                {
                    "id": "loop",
                    "type": "iterator",
                    "config": {"items": "$v.tasks", "collect_to": "results"},
                },
                {
                    "id": "route",
                    "type": "switch",
                    "config": {
                        "variable": "$o.loop.current_item.action",
                        "cases": {"upper": "upper", "lower": "lower"},
                    },
                },
                {
                    "id": "do_upper",
                    "type": "tool",
                    "config": {
                        "tool_name": "test_upper",
                        "inputs": {"text": "$o.loop.current_item.text"},
                    },
                },
                {
                    "id": "do_lower",
                    "type": "tool",
                    "config": {
                        "tool_name": "test_lower",
                        "inputs": {"text": "$o.loop.current_item.text"},
                    },
                },
                {
                    "id": "end",
                    "type": "python",
                    "config": {"code": "def main():\n    return {'status': 'done'}"},
                },
            ],
            edges=[
                {"source": "loop", "target": "route"},
                {"source": "route", "target": "do_upper", "condition": "$o.route.choice == upper"},
                {"source": "route", "target": "do_lower", "condition": "$o.route.choice == lower"},
                {"source": "do_upper", "target": "loop"},
                {"source": "do_lower", "target": "loop"},
                {"source": "loop", "target": "end", "condition": "$o.loop.done"},
            ],
        )
        ctx = FlowContext(
            variables={
                "tasks": [
                    {"action": "upper", "text": "hello"},
                    {"action": "lower", "text": "WORLD"},
                    {"action": "upper", "text": "foo"},
                ]
            }
        )
        run(collect_events(flow, ctx))

        loop_output = ctx.outputs["loop"]
        assert loop_output["done"] is True
        assert loop_output["results"] == [
            {"result": "HELLO"},
            {"result": "world"},
            {"result": "FOO"},
        ]


# ── Agent instructions 注入测试 ──


class TestAgentInstructionsInjection:
    """验证 Agent 节点 config.instructions 传递"""

    def test_instructions_passthrough_via_kwargs(self):
        """instructions 字段通过 _create_agent kwargs 机制传递到 Agent"""
        from agstack.llm.flow.agent import Agent
        from agstack.llm.flow.nodes.agent_node import AgentNodeHandler

        # 注册一个测试 agent
        class TestAgent(Agent):
            def __init__(self, **kwargs):
                super().__init__(name="test_agent", **kwargs)

        registry.register_agent("test_agent", TestAgent)

        handler = AgentNodeHandler()
        ctx = FlowContext(variables={"custom_prompt": "You are a custom assistant."})
        config = {
            "agent_name": "test_agent",
            "instructions": "$v.custom_prompt",
            "inputs": {"input": "hello"},
        }

        agent = handler._create_agent(config, ctx)
        assert agent.instructions == "You are a custom assistant."

    def test_instructions_literal_string(self):
        """instructions 字段为字面量字符串"""
        from agstack.llm.flow.agent import Agent
        from agstack.llm.flow.nodes.agent_node import AgentNodeHandler

        class LiteralAgent(Agent):
            def __init__(self, **kwargs):
                super().__init__(name="literal_agent", **kwargs)

        registry.register_agent("literal_agent", LiteralAgent)

        handler = AgentNodeHandler()
        ctx = FlowContext()
        config = {
            "agent_name": "literal_agent",
            "instructions": "Be concise and direct.",
            "inputs": {},
        }

        agent = handler._create_agent(config, ctx)
        assert agent.instructions == "Be concise and direct."

    def test_no_instructions_uses_default(self):
        """不提供 instructions 时使用 Agent 默认值"""
        from agstack.llm.flow.agent import Agent
        from agstack.llm.flow.nodes.agent_node import AgentNodeHandler

        class DefaultAgent(Agent):
            def __init__(self, **kwargs):
                kwargs.setdefault("instructions", "I am the default.")
                super().__init__(name="default_agent", **kwargs)

        registry.register_agent("default_agent", DefaultAgent)

        handler = AgentNodeHandler()
        ctx = FlowContext()
        config = {
            "agent_name": "default_agent",
            "inputs": {},
        }

        agent = handler._create_agent(config, ctx)
        assert agent.instructions == "I am the default."
