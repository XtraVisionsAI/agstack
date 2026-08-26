#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Agent 工具循环并行执行（F3）验收用例

Tool 声明 concurrency_safe=True 后，同轮内连续的安全调用聚组 asyncio.gather
并发执行；组内事件按完成顺序 yield（消费端靠 toolCallId 关联），tool 消息
按 tool_call 原始顺序写回（OpenAI 协议要求与 assistant.tool_calls 对应）。
默认全 False → 全串行，行为与 2.0.0 一致（fail closed）。
"""

import asyncio
import time
from unittest.mock import patch

from agstack.llm.flow.agent import Agent
from agstack.llm.flow.context import FlowContext
from agstack.llm.flow.event import EventType
from agstack.llm.flow.tool import Tool
from tests.test_flow_cancellation import _tool_calls_chunk
from tests.test_flow_error_semantics import (
    FakeStreamClient,
    _collect,
    _finish_chunk,
    _run,
    _text_chunk,
)


def _sleep_tool(name: str, delay: float, log: list[tuple[str, str]], *, safe: bool) -> Tool:
    async def fn(context, inputs, _name=name, _delay=delay):
        log.append(("start", _name))
        await asyncio.sleep(_delay)
        log.append(("end", _name))
        return {"tool": _name}

    return Tool(name=name, description="", function=fn, concurrency_safe=safe)


def _one_turn_client(calls: list[tuple[str, str, str]]) -> FakeStreamClient:
    """第一轮返回给定 tool_calls，第二轮起纯文本收尾"""
    return FakeStreamClient(
        [
            [_tool_calls_chunk(calls), _finish_chunk("tool_calls")],
            [_text_chunk("done"), _finish_chunk()],
        ]
    )


class TestConcurrentToolCalls:
    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_safe_group_runs_concurrently(self, mock_get_client):
        """三个 concurrency_safe、各 sleep 的工具同轮调用：总耗时约为单个而非三倍"""
        log: list[tuple[str, str]] = []
        tools = [_sleep_tool(f"s{i}", 0.05, log, safe=True) for i in range(1, 4)]
        mock_get_client.return_value = _one_turn_client([(f"tc{i}", f"s{i}", "{}") for i in range(1, 4)])

        agent = Agent(name="worker", tools=tools)
        t0 = time.perf_counter()
        _run(_collect(agent.stream(FlowContext(variables={"input": "go"}))))
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.12  # 串行需 ≥0.15s
        # 并发证据：三个 start 都发生在任何 end 之前
        assert [kind for kind, _ in log[:3]] == ["start", "start", "start"]

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_mixed_safe_unsafe_grouping(self, mock_get_client):
        """S,S,U,S：前两个并发成组，第三、四个各自单独串行，执行顺序保持"""
        log: list[tuple[str, str]] = []
        tools = [
            _sleep_tool("g1", 0.02, log, safe=True),
            _sleep_tool("g2", 0.02, log, safe=True),
            _sleep_tool("u1", 0.02, log, safe=False),
            _sleep_tool("g3", 0.02, log, safe=True),
        ]
        mock_get_client.return_value = _one_turn_client(
            [("tc1", "g1", "{}"), ("tc2", "g2", "{}"), ("tc3", "u1", "{}"), ("tc4", "g3", "{}")]
        )

        agent = Agent(name="worker", tools=tools)
        _run(_collect(agent.stream(FlowContext(variables={"input": "go"}))))

        starts = [n for kind, n in log if kind == "start"]
        # g1/g2 并发（顺序不定）后才轮到 u1，最后 g3
        assert set(starts[:2]) == {"g1", "g2"} and starts[2:] == ["u1", "g3"]
        # u1 开始前 g1/g2 均已结束（组间串行）
        u1_start = log.index(("start", "u1"))
        assert ("end", "g1") in log[:u1_start] and ("end", "g2") in log[:u1_start]

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_tool_messages_keep_original_order(self, mock_get_client):
        """下一轮 LLM 请求中 tool 消息顺序与 assistant.tool_calls 一致（即使完成顺序相反）"""
        log: list[tuple[str, str]] = []
        # f1 慢、f2 快 → 完成顺序 f2, f1；消息顺序必须仍是 f1, f2
        tools = [_sleep_tool("f1", 0.05, log, safe=True), _sleep_tool("f2", 0.005, log, safe=True)]
        client = _one_turn_client([("tc1", "f1", "{}"), ("tc2", "f2", "{}")])
        mock_get_client.return_value = client

        agent = Agent(name="worker", tools=tools)
        events = _run(_collect(agent.stream(FlowContext(variables={"input": "go"}))))

        # 事件按完成顺序：f2 的结果先到
        results = [e for e in events if e["type"] == EventType.TOOL_CALL_RESULT]
        assert [e["toolCallId"] for e in results] == ["tc2", "tc1"]

        # 第二轮请求的消息中 tool 消息按原始顺序
        second_request = client.requests[1]
        tool_msgs = [m for m in second_request["messages"] if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["tc1", "tc2"]

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_failure_in_group_does_not_affect_others(self, mock_get_client):
        """组内单个失败：其它调用正常完成，失败经 ToolResult 通道反馈"""

        def boom(context, inputs):
            raise RuntimeError("boom")

        tools = [
            Tool(name="ok1", description="", function=lambda c, i: {"fine": 1}, concurrency_safe=True),
            Tool(name="bad", description="", function=boom, concurrency_safe=True),
            Tool(name="ok2", description="", function=lambda c, i: {"fine": 2}, concurrency_safe=True),
        ]
        client = _one_turn_client([("tc1", "ok1", "{}"), ("tc2", "bad", "{}"), ("tc3", "ok2", "{}")])
        mock_get_client.return_value = client

        agent = Agent(name="worker", tools=tools)
        events = _run(_collect(agent.stream(FlowContext(variables={"input": "go"}))))

        results = {e["toolCallId"]: e["content"] for e in events if e["type"] == EventType.TOOL_CALL_RESULT}
        assert "fine" in results["tc1"] and "fine" in results["tc3"]
        assert "boom" in results["tc2"]
        # 三条 tool 消息都写回且按原始顺序
        tool_msgs = [m for m in client.requests[1]["messages"] if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["tc1", "tc2", "tc3"]

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_default_unsafe_stays_serial(self, mock_get_client):
        """全默认值回归：不声明 concurrency_safe 时严格串行，顺序与 2.0.0 一致"""
        log: list[tuple[str, str]] = []
        tools = [_sleep_tool(f"d{i}", 0.01, log, safe=False) for i in range(1, 4)]
        client = _one_turn_client([(f"tc{i}", f"d{i}", "{}") for i in range(1, 4)])
        mock_get_client.return_value = client

        agent = Agent(name="worker", tools=tools)
        events = _run(_collect(agent.stream(FlowContext(variables={"input": "go"}))))

        # 严格串行：start/end 成对交替，无重叠
        assert log == [(k, f"d{i}") for i in range(1, 4) for k in ("start", "end")]
        results = [e for e in events if e["type"] == EventType.TOOL_CALL_RESULT]
        assert [e["toolCallId"] for e in results] == ["tc1", "tc2", "tc3"]
        tool_msgs = [m for m in client.requests[1]["messages"] if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["tc1", "tc2", "tc3"]
