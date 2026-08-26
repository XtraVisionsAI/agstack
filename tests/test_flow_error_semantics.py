#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Flow 系统失败语义测试 — 1.26.0 修复的五项缺陷（D1-D5）验收用例

D1: Agent max_turns 耗尽显式收尾
D2: 工具参数 JSON 解析失败反馈给模型
D3: tool 节点 on_error 开关
D4: NodeTrace.usage 按节点归因
D5: run() 收敛为 stream() 消费者
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agstack.llm.flow.agent import Agent
from agstack.llm.flow.context import FlowContext
from agstack.llm.flow.event import EventType
from agstack.llm.flow.exceptions import AgentError, NodeExecutionError
from agstack.llm.flow.flow import Flow
from agstack.llm.flow.registry import registry
from agstack.llm.flow.tool import Tool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── 可编程 LLM 流式客户端桩 ──


def _text_chunk(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None), finish_reason=None)]
    )


def _tool_call_chunk(call_id: str, name: str, arguments: str):
    tc = SimpleNamespace(index=0, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tc]), finish_reason=None)]
    )


def _finish_chunk(reason: str = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None), finish_reason=reason)]
    )


class FakeStreamClient:
    """按轮次返回预编程 chunk 序列；超出编程轮次时重复最后一轮"""

    def __init__(self, turns: list[list]):
        self.turns = turns
        self.requests: list[dict] = []

    async def chat(self, stream: bool = True, **kwargs):
        self.requests.append(kwargs)
        idx = min(len(self.requests) - 1, len(self.turns) - 1)
        chunks = self.turns[idx]

        async def _gen():
            for c in chunks:
                yield c

        return _gen()


async def _collect(aiter):
    return [evt async for evt in aiter]


# ── D1: Agent max_turns 耗尽显式收尾 ──


class TestAgentMaxTurns:
    def _looping_tool(self, counter: dict) -> Tool:
        def fn(context, inputs):
            counter["n"] = counter.get("n", 0) + 1
            return {"note": "need more tool calls"}

        return Tool(name="looper", description="always asks for more", function=fn)

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_exhaustion_finalizes_explicitly(self, mock_get_client):
        """打满场景：END 事件闭合、agent_max_turns CUSTOM 事件、输出含 truncated 标记"""
        counter: dict = {}
        turn = [
            _text_chunk("thinking"),
            _tool_call_chunk("tc1", "looper", "{}"),
            _finish_chunk("tool_calls"),
        ]
        mock_get_client.return_value = FakeStreamClient([turn])

        agent = Agent(name="worker", tools=[self._looping_tool(counter)], max_turns=2)
        ctx = FlowContext(variables={"input": "go"})
        events = _run(_collect(agent.stream(ctx)))

        types = [e["type"] for e in events]
        assert types.count(EventType.TEXT_MESSAGE_END) == 1
        customs = [e for e in events if e["type"] == EventType.CUSTOM and e.get("name") == "agent_max_turns"]
        assert len(customs) == 1
        assert customs[0]["value"] == {"agentName": "worker", "maxTurns": 2}
        assert ctx.outputs["worker"]["truncated"] is True
        assert ctx.outputs["worker"]["result"] == "thinking"
        assert ctx.get_variable("_agent_call_id") is None
        assert counter["n"] == 2  # 两轮各执行一次工具

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_normal_exit_unchanged(self, mock_get_client):
        """正常场景：无 truncated 键、无 agent_max_turns 事件"""
        mock_get_client.return_value = FakeStreamClient([[_text_chunk("hello"), _finish_chunk()]])

        agent = Agent(name="worker", max_turns=2)
        ctx = FlowContext(variables={"input": "hi"})
        events = _run(_collect(agent.stream(ctx)))

        assert ctx.outputs["worker"] == {"result": "hello"}
        assert "truncated" not in ctx.outputs["worker"]
        assert [e["type"] for e in events].count(EventType.TEXT_MESSAGE_END) == 1
        assert not any(e["type"] == EventType.CUSTOM and e.get("name") == "agent_max_turns" for e in events)

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_run_returns_partial_text_on_exhaustion(self, mock_get_client):
        counter: dict = {}
        turn = [
            _text_chunk("partial"),
            _tool_call_chunk("tc1", "looper", "{}"),
            _finish_chunk("tool_calls"),
        ]
        mock_get_client.return_value = FakeStreamClient([turn])

        agent = Agent(name="worker", tools=[self._looping_tool(counter)], max_turns=2)
        result = _run(agent.run(FlowContext(variables={"input": "go"})))
        assert "partial" in result["result"]

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_on_max_turns_error_mode(self, mock_get_client):
        """严格模式：yield RUN_ERROR 后抛 AgentError"""
        counter: dict = {}
        turn = [_tool_call_chunk("tc1", "looper", "{}"), _finish_chunk("tool_calls")]
        mock_get_client.return_value = FakeStreamClient([turn])

        agent = Agent(name="worker", tools=[self._looping_tool(counter)], max_turns=1, on_max_turns="error")
        ctx = FlowContext(variables={"input": "go"})

        async def _consume():
            events = []
            with pytest.raises(AgentError):
                async for evt in agent.stream(ctx):
                    events.append(evt)
            return events

        events = _run(_consume())
        assert any(e["type"] == EventType.RUN_ERROR and e.get("code") == "AGENT_MAX_TURNS_EXCEEDED" for e in events)


# ── D2: 工具参数 JSON 解析失败反馈给模型 ──


class TestToolArgsParseFailure:
    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_invalid_json_feeds_error_back_to_model(self, mock_get_client):
        """解析失败：工具不被调用，错误进 tool message 与 TOOL_CALL_RESULT，下一轮模型可见"""
        counter: dict = {"n": 0}

        def fn(context, inputs):
            counter["n"] += 1
            return {"docs": []}

        tool = Tool(name="search", description="retrieval", function=fn)
        bad_args = '{"query": "未闭合'
        client = FakeStreamClient(
            [
                [_tool_call_chunk("tc1", "search", bad_args), _finish_chunk("tool_calls")],
                [_text_chunk("done"), _finish_chunk()],
            ]
        )
        mock_get_client.return_value = client

        agent = Agent(name="worker", tools=[tool])
        ctx = FlowContext(variables={"input": "find it"})
        events = _run(_collect(agent.stream(ctx)))

        # 工具函数不被调用
        assert counter["n"] == 0

        # 事件流出现含 parse failed 信息的 TOOL_CALL_RESULT
        results = [e for e in events if e["type"] == EventType.TOOL_CALL_RESULT]
        assert len(results) == 1
        payload = json.loads(results[0]["content"])
        assert "JSON parse failed" in payload["error"]
        assert payload["raw_arguments"] == bad_args

        # 下一轮模型请求的 messages 中含该 tool 角色错误消息
        second_request_messages = client.requests[1]["messages"]
        tool_msgs = [m for m in second_request_messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "JSON parse failed" in tool_msgs[0]["content"]
        assert tool_msgs[0]["tool_call_id"] == "tc1"

        # 循环正常继续并结束
        assert ctx.outputs["worker"] == {"result": "done"}

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_empty_arguments_still_calls_tool(self, mock_get_client):
        """arguments 为空字符串维持现行为：无参工具正常调用"""
        captured: dict = {"n": 0}

        def fn(context, inputs):
            captured["n"] += 1
            captured["inputs"] = inputs
            return {"ok": True}

        tool = Tool(name="noargs", description="no-arg tool", function=fn)
        client = FakeStreamClient(
            [
                [_tool_call_chunk("tc1", "noargs", ""), _finish_chunk("tool_calls")],
                [_text_chunk("done"), _finish_chunk()],
            ]
        )
        mock_get_client.return_value = client

        agent = Agent(name="worker", tools=[tool])
        _run(_collect(agent.stream(FlowContext(variables={"input": "go"}))))
        assert captured["n"] == 1
        assert captured["inputs"] == {}


# ── D3: tool 节点 on_error 开关 ──


def _fail_fn(context, inputs):
    raise ValueError("boom")


class TestToolNodeOnError:
    def test_default_raise_preserved(self):
        """不写 on_error：失败仍中断 flow（NodeExecutionError 包装）"""
        registry.register_tool("d3_fail_raise", Tool(name="d3_fail_raise", description="", function=_fail_fn))
        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[{"id": "search", "type": "tool", "config": {"tool_name": "d3_fail_raise"}}],
        )
        with pytest.raises(NodeExecutionError):
            _run(flow.run(FlowContext()))

    def test_on_error_continue_routes_condition_edge(self):
        """on_error: continue：flow 不中断，条件边按 success == false 分流，trace 记录 error"""
        registry.register_tool("d3_fail_cont", Tool(name="d3_fail_cont", description="", function=_fail_fn))
        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[
                {
                    "id": "search",
                    "type": "tool",
                    "config": {"tool_name": "d3_fail_cont", "on_error": "continue"},
                },
                {
                    "id": "fallback",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'handled': True}"},
                },
                {
                    "id": "happy",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'happy': True}"},
                },
            ],
            edges=[
                {"source": "search", "condition": "$o.search.success == false", "target": "fallback"},
                {"source": "search", "target": "happy"},
            ],
        )
        ctx = FlowContext()
        _run(flow.run(ctx))

        assert ctx.outputs["search"]["success"] is False
        assert "boom" in ctx.outputs["search"]["error"]
        assert ctx.outputs["fallback"] == {"handled": True}
        assert "happy" not in ctx.outputs

        search_trace = next(n for n in ctx.trace.nodes if n.node_id == "search")
        assert search_trace.error is not None and "boom" in search_trace.error


# ── D4: NodeTrace.usage 按节点归因 ──


def _chat_response(text: str, prompt: int, completion: int):
    resp = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion)
    return resp


_NEVER_EDGE = {"condition": "$v._never == yes"}  # 恒不满足且无 fallback：驱动 edge-driven 路径后自然结束


class TestNodeUsageAttribution:
    @patch("agstack.llm.flow.nodes.llm_chat_node.get_llm_client")
    def test_single_llm_node(self, mock_get_client):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=_chat_response("hi", 10, 5))
        mock_get_client.return_value = mock_client

        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[{"id": "chat1", "type": "llm_chat", "config": {"prompt": "hello"}}],
            edges=[{"source": "chat1", "target": "chat1", **_NEVER_EDGE}],
        )
        ctx = FlowContext()
        _run(flow.run(ctx))

        node = next(n for n in ctx.trace.nodes if n.node_id == "chat1")
        assert node.usage is not None
        assert node.usage.total_tokens == 15
        assert node.usage.total_tokens == ctx.trace.total_usage.total_tokens

    @patch("agstack.llm.flow.nodes.llm_chat_node.get_llm_client")
    def test_two_serial_llm_nodes_sum_to_total(self, mock_get_client):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=[_chat_response("a", 10, 5), _chat_response("b", 6, 2)])
        mock_get_client.return_value = mock_client

        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[
                {"id": "chat1", "type": "llm_chat", "config": {"prompt": "one"}},
                {"id": "chat2", "type": "llm_chat", "config": {"prompt": "two"}},
            ],
            edges=[{"source": "chat1", "target": "chat2"}],
        )
        ctx = FlowContext()
        _run(flow.run(ctx))

        n1 = next(n for n in ctx.trace.nodes if n.node_id == "chat1")
        n2 = next(n for n in ctx.trace.nodes if n.node_id == "chat2")
        assert n1.usage is not None and n2.usage is not None
        assert n1.usage.total_tokens == 15
        assert n2.usage.total_tokens == 8
        assert n1.usage.total_tokens + n2.usage.total_tokens == ctx.trace.total_usage.total_tokens

    def test_non_llm_node_usage_is_none(self):
        """无 LLM 调用的节点：usage 为 None 而非零值 Usage"""
        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[
                {
                    "id": "py1",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'ok': True}"},
                }
            ],
            edges=[{"source": "py1", "target": "py1", **_NEVER_EDGE}],
        )
        ctx = FlowContext()
        _run(flow.run(ctx))
        node = next(n for n in ctx.trace.nodes if n.node_id == "py1")
        assert node.usage is None

    @patch("agstack.llm.flow.nodes.llm_chat_node.get_llm_client")
    def test_parallel_container_owns_branch_usage(self, mock_get_client):
        """parallel 容器 usage = 分支用量总和，分支节点 usage 为 None"""
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=[_chat_response("a", 10, 5), _chat_response("b", 6, 2)])
        mock_get_client.return_value = mock_client

        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[
                {"id": "par", "type": "parallel", "config": {"branches": ["b1", "b2"]}},
                {"id": "b1", "type": "llm_chat", "config": {"prompt": "one"}},
                {"id": "b2", "type": "llm_chat", "config": {"prompt": "two"}},
            ],
            edges=[{"source": "par", "target": "par", **_NEVER_EDGE}],
        )
        ctx = FlowContext()
        _run(flow.run(ctx))

        par = next(n for n in ctx.trace.nodes if n.node_id == "par")
        assert par.usage is not None
        assert par.usage.total_tokens == 23
        for branch_id in ("b1", "b2"):
            branch = next(n for n in ctx.trace.nodes if n.node_id == branch_id)
            assert branch.usage is None


# ── D5: run() 收敛为 stream() 消费者 ──


class TestRunStreamConvergence:
    def test_run_applies_retry_policy(self):
        """首次必失败、二次成功 + retry 配置：run() 成功返回"""
        calls = {"n": 0}

        def flaky(context, inputs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("first attempt fails")
            return {"ok": True}

        registry.register_tool("d5_flaky", Tool(name="d5_flaky", description="", function=flaky))
        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[
                {
                    "id": "job",
                    "type": "tool",
                    "config": {
                        "tool_name": "d5_flaky",
                        "retry": {"max_retries": 2, "delay": 0.001, "backoff": 1.0},
                    },
                }
            ],
        )
        ctx = FlowContext()
        outputs = _run(flow.run(ctx))
        assert calls["n"] == 2
        assert outputs["job"] == {"ok": True}

    def test_run_populates_trace(self):
        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[
                {
                    "id": "py1",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'ok': True}"},
                }
            ],
            edges=[{"source": "py1", "target": "py1", **_NEVER_EDGE}],
        )
        ctx = FlowContext()
        _run(flow.run(ctx))
        assert len(ctx.trace.nodes) == 1
        assert ctx.trace.started_at is not None
        assert ctx.trace.finished_at is not None
        assert ctx.trace.total_usage is ctx.usage

    def test_run_output_mode_append(self):
        """output_mode: append 的节点被访问两次后输出是长度 2 的 list"""
        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[
                {
                    "id": "gen",
                    "type": "python",
                    "config": {
                        "code": "def main(**kwargs):\n    return {'tick': 1}",
                        "output_mode": "append",
                    },
                },
                {
                    "id": "end",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    return {'end': True}"},
                },
            ],
            edges=[
                {"source": "gen", "condition": "$v.always == yes", "target": "gen"},
                {"source": "gen", "target": "end"},
            ],
            cycle_limits={"gen": 2},
        )
        ctx = FlowContext(variables={"always": "yes"})
        _run(flow.run(ctx))
        assert isinstance(ctx.outputs["gen"], list)
        assert len(ctx.outputs["gen"]) == 2
        assert ctx.outputs["end"] == {"end": True}

    def test_run_wraps_errors_as_node_execution_error(self):
        """异常类型收窄：run() 节点失败抛 NodeExecutionError（1.25.1 抛原始异常）"""
        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[
                {
                    "id": "bad",
                    "type": "python",
                    "config": {"code": "def main(**kwargs):\n    raise RuntimeError('inner')"},
                }
            ],
        )
        with pytest.raises(NodeExecutionError):
            _run(flow.run(FlowContext()))
