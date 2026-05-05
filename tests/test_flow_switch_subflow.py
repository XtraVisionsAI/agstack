#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Switch 和 Subflow 节点测试"""

import asyncio

import pytest

from agstack.llm.flow.context import FlowContext
from agstack.llm.flow.flow import Flow
from agstack.llm.flow.nodes.subflow_node import SubflowNodeHandler
from agstack.llm.flow.nodes.switch_node import SwitchNodeHandler


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Switch 节点 ──


class TestSwitchNode:
    """switch 节点单元测试"""

    def setup_method(self):
        self.handler = SwitchNodeHandler()

    def test_match_case(self):
        """正常匹配 cases 中的值"""
        ctx = FlowContext(variables={"model_tier": "strong"})
        node = {
            "id": "sw1",
            "type": "switch",
            "config": {
                "variable": "$v.model_tier",
                "cases": {"strong": "research_agent", "basic": "research_pipeline"},
            },
        }
        result = run(self.handler.execute(node, ctx))
        assert result == {"choice": "strong"}

    def test_no_match_with_default(self):
        """未匹配时使用 default"""
        ctx = FlowContext(variables={"model_tier": "unknown"})
        node = {
            "id": "sw1",
            "type": "switch",
            "config": {
                "variable": "$v.model_tier",
                "cases": {"strong": "research_agent", "basic": "research_pipeline"},
                "default": "basic",
            },
        }
        result = run(self.handler.execute(node, ctx))
        assert result == {"choice": "basic"}

    def test_no_match_no_default(self):
        """未匹配且无 default 时使用变量原始值"""
        ctx = FlowContext(variables={"model_tier": "medium"})
        node = {
            "id": "sw1",
            "type": "switch",
            "config": {
                "variable": "$v.model_tier",
                "cases": {"strong": "research_agent", "basic": "research_pipeline"},
            },
        }
        result = run(self.handler.execute(node, ctx))
        assert result == {"choice": "medium"}

    def test_none_variable(self):
        """变量值为 None 时转为空字符串"""
        ctx = FlowContext()
        node = {
            "id": "sw1",
            "type": "switch",
            "config": {
                "variable": "$v.missing",
                "cases": {"strong": "a"},
                "default": "fallback",
            },
        }
        result = run(self.handler.execute(node, ctx))
        assert result == {"choice": "fallback"}

    def test_integer_variable(self):
        """整数变量转为字符串后匹配"""
        ctx = FlowContext(variables={"level": 2})
        node = {
            "id": "sw1",
            "type": "switch",
            "config": {
                "variable": "$v.level",
                "cases": {"1": "low", "2": "mid", "3": "high"},
            },
        }
        result = run(self.handler.execute(node, ctx))
        assert result == {"choice": "2"}

    def test_node_type(self):
        assert self.handler.node_type == "switch"


# ── Subflow 节点 ──


class TestSubflowNode:
    """subflow 节点单元测试"""

    def setup_method(self):
        self.handler = SubflowNodeHandler()

    def test_subflow_executes_child_flow(self):
        """子 flow 正常执行并返回结果"""
        child_flow = Flow(
            flow_id="child_1",
            name="simple_flow",
            nodes=[
                {
                    "id": "py1",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'answer': 42}"},
                },
            ],
            edges=[],
        )

        from agstack.llm.flow.registry import registry

        registry._flows["simple_flow"] = lambda **kwargs: child_flow

        try:
            ctx = FlowContext()
            node = {
                "id": "sub1",
                "type": "subflow",
                "config": {"flow_name": "simple_flow"},
            }
            result = run(self.handler.execute(node, ctx))
            assert result == {"answer": 42}
        finally:
            registry._flows.pop("simple_flow", None)

    def test_subflow_with_inputs(self):
        """子 flow 接收 inputs 参数"""
        child_flow = Flow(
            flow_id="child_2",
            name="echo_flow",
            nodes=[
                {
                    "id": "py1",
                    "type": "python",
                    "config": {
                        "code": "def main(**kwargs):\n    return {'echo': kwargs.get('query', '')}",
                        "inputs": {"query": "$v.query"},
                    },
                },
            ],
            edges=[],
        )

        from agstack.llm.flow.registry import registry

        registry._flows["echo_flow"] = lambda **kwargs: child_flow

        try:
            ctx = FlowContext(variables={"user_query": "hello world"})
            node = {
                "id": "sub1",
                "type": "subflow",
                "config": {
                    "flow_name": "echo_flow",
                    "inputs": {"query": "$v.user_query"},
                },
            }
            result = run(self.handler.execute(node, ctx))
            assert result == {"echo": "hello world"}
            assert ctx.variables["query"] == "hello world"
        finally:
            registry._flows.pop("echo_flow", None)

    def test_subflow_not_found(self):
        """找不到子 flow 时抛出 FlowError"""
        from agstack.llm.flow.exceptions import FlowError

        ctx = FlowContext()
        node = {
            "id": "sub1",
            "type": "subflow",
            "config": {"flow_name": "nonexistent_flow"},
        }
        with pytest.raises(FlowError):
            run(self.handler.execute(node, ctx))

    def test_subflow_inline_config(self):
        """通过内联 flow_config 加载子 flow"""
        ctx = FlowContext()
        node = {
            "id": "sub1",
            "type": "subflow",
            "config": {
                "flow_name": "not_registered",
                "flow_config": {
                    "flow_id": "inline_1",
                    "name": "inline_flow",
                    "nodes": [
                        {
                            "id": "py1",
                            "type": "python",
                            "config": {"code": "def main(**kwargs):\n    return {'inline': True}"},
                        },
                    ],
                    "edges": [],
                },
            },
        }
        result = run(self.handler.execute(node, ctx))
        assert result == {"inline": True}

    def test_subflow_stream(self):
        """流式执行子 flow 并透传事件"""
        child_flow = Flow(
            flow_id="child_s",
            name="stream_flow",
            nodes=[
                {
                    "id": "py1",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'streamed': True}"},
                },
            ],
            edges=[{"source": "py1", "target": None}],
        )

        from agstack.llm.flow.registry import registry

        registry._flows["stream_flow"] = lambda **kwargs: child_flow

        try:
            ctx = FlowContext()
            node = {
                "id": "sub1",
                "type": "subflow",
                "config": {"flow_name": "stream_flow"},
            }

            async def collect():
                events = []
                async for evt in self.handler.stream(node, ctx, "sub1"):
                    events.append(evt)
                return events

            events = run(collect())
            assert len(events) > 0
            assert ctx.outputs["sub1"] == {"streamed": True}
        finally:
            registry._flows.pop("stream_flow", None)

    def test_node_type(self):
        assert self.handler.node_type == "subflow"


# ── 集成测试：switch + edge 路由 ──


class TestSwitchEdgeRouting:
    """switch 节点与 edge 路由集成测试"""

    def test_switch_routes_to_correct_branch(self):
        """switch 节点路由到正确的下游节点"""
        flow = Flow(
            flow_id="test_flow",
            name="switch_routing",
            nodes=[
                {
                    "id": "model_switch",
                    "type": "switch",
                    "config": {
                        "variable": "$v.model_tier",
                        "cases": {"strong": "agent_a", "basic": "pipeline_b"},
                        "default": "pipeline_b",
                    },
                },
                {
                    "id": "agent_a",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'path': 'strong_path'}"},
                },
                {
                    "id": "pipeline_b",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'path': 'basic_path'}"},
                },
            ],
            edges=[
                {"source": "model_switch", "target": "agent_a", "condition": "strong"},
                {"source": "model_switch", "target": "pipeline_b", "condition": "basic"},
            ],
        )

        ctx = FlowContext(variables={"model_tier": "strong"})
        run(flow.run(ctx))
        assert ctx.outputs["agent_a"] == {"path": "strong_path"}
        assert "pipeline_b" not in ctx.outputs

    def test_switch_routes_default(self):
        """switch 节点 default 分支路由"""
        flow = Flow(
            flow_id="test_flow",
            name="switch_default",
            nodes=[
                {
                    "id": "sw",
                    "type": "switch",
                    "config": {
                        "variable": "$v.tier",
                        "cases": {"a": "node_a", "b": "node_b"},
                        "default": "b",
                    },
                },
                {
                    "id": "node_a",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'path': 'a'}"},
                },
                {
                    "id": "node_b",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'path': 'b'}"},
                },
            ],
            edges=[
                {"source": "sw", "target": "node_a", "condition": "a"},
                {"source": "sw", "target": "node_b", "condition": "b"},
            ],
        )

        ctx = FlowContext(variables={"tier": "unknown"})
        run(flow.run(ctx))
        assert ctx.outputs["node_b"] == {"path": "b"}
