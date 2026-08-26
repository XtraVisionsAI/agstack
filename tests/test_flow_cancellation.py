#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""协作式取消（F2）验收用例

取消是协作式的：cancel() 置事件，引擎在下一个检查点停止
（节点执行前、retry 重试前、agent 轮次开始、tool_call 执行前），
不强杀在途工具。停止形状：RUN_ERROR(code=CANCELLED) 结束事件流，
run() 抛 FlowExecutionError("FLOW_CANCELLED")。
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agstack.llm.flow.agent import Agent
from agstack.llm.flow.context import FlowContext
from agstack.llm.flow.event import EventType
from agstack.llm.flow.exceptions import FlowExecutionError
from agstack.llm.flow.flow import Flow
from agstack.llm.flow.registry import registry
from agstack.llm.flow.tool import Tool
from tests.test_flow_error_semantics import (
    FakeStreamClient,
    _collect,
    _finish_chunk,
    _run,
    _text_chunk,
)


def _tool_calls_chunk(calls: list[tuple[str, str, str]]):
    """一轮内多个 tool_calls 的增量 chunk"""
    tcs = [
        SimpleNamespace(index=i, id=cid, function=SimpleNamespace(name=name, arguments=args))
        for i, (cid, name, args) in enumerate(calls)
    ]
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=tcs), finish_reason=None)]
    )


def _register_recording_tool(name: str, calls: list[str], *, cancel: bool = False) -> None:
    def fn(context, inputs, _name=name, _cancel=cancel):
        calls.append(_name)
        if _cancel:
            context.cancel()
        return {"ok": _name}

    registry.register_tool(name, Tool(name=name, description="", function=fn))


def _three_node_flow(prefix: str) -> Flow:
    return Flow(
        flow_id="t",
        name="t",
        nodes=[
            {"id": "n1", "type": "tool", "config": {"tool_name": f"{prefix}_t1"}},
            {"id": "n2", "type": "tool", "config": {"tool_name": f"{prefix}_t2"}},
            {"id": "n3", "type": "tool", "config": {"tool_name": f"{prefix}_t3"}},
        ],
        edges=[
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    )


class TestFlowCancellation:
    def test_cancel_after_first_node_stops_stream(self):
        """第一节点执行中取消：第二节点不执行，事件流以 RUN_ERROR(code=CANCELLED) 结束"""
        calls: list[str] = []
        _register_recording_tool("fc_t1", calls, cancel=True)
        _register_recording_tool("fc_t2", calls)
        _register_recording_tool("fc_t3", calls)

        ctx = FlowContext()
        events = _run(_collect(_three_node_flow("fc").stream(ctx)))

        assert calls == ["fc_t1"]
        assert "n2" not in ctx.outputs and "n3" not in ctx.outputs
        assert events[-1]["type"] == EventType.RUN_ERROR
        assert events[-1]["code"] == "CANCELLED"

    def test_run_raises_flow_cancelled(self):
        """run() 作为 stream 消费者：取消向上抛 FlowExecutionError("FLOW_CANCELLED")"""
        calls: list[str] = []
        _register_recording_tool("fr_t1", calls, cancel=True)
        _register_recording_tool("fr_t2", calls)
        _register_recording_tool("fr_t3", calls)

        with pytest.raises(FlowExecutionError) as ei:
            _run(_three_node_flow("fr").run(FlowContext()))

        assert ei.value.error_key == "FLOW_CANCELLED"
        assert calls == ["fr_t1"]

    def test_cancel_skips_remaining_retries(self):
        """retry 重试前检查点：取消后不再开始新的重试"""
        attempts: list[int] = []

        def failing(context, inputs):
            attempts.append(1)
            context.cancel()
            raise RuntimeError("boom")

        registry.register_tool("fcr_fail", Tool(name="fcr_fail", description="", function=failing))
        flow = Flow(
            flow_id="t",
            name="t",
            nodes=[
                {
                    "id": "n1",
                    "type": "tool",
                    "config": {
                        "tool_name": "fcr_fail",
                        "retry": {"max_retries": 3, "delay": 0.001, "backoff": 1.0},
                    },
                }
            ],
            edges=[{"source": "n1", "target": "n1", "condition": "$v._never == yes"}],
        )
        events = _run(_collect(flow.stream(FlowContext())))

        assert len(attempts) == 1  # 第一次失败后取消，三次重试全部跳过
        assert events[-1]["type"] == EventType.RUN_ERROR
        assert events[-1]["code"] == "CANCELLED"

    def test_not_cancelled_flow_unchanged(self):
        """未取消场景回归：三节点全执行，事件流以 flow STEP_FINISHED 正常结束"""
        calls: list[str] = []
        _register_recording_tool("fn_t1", calls)
        _register_recording_tool("fn_t2", calls)
        _register_recording_tool("fn_t3", calls)

        ctx = FlowContext()
        events = _run(_collect(_three_node_flow("fn").stream(ctx)))

        assert calls == ["fn_t1", "fn_t2", "fn_t3"]
        assert ctx.outputs["n3"] == {"ok": "fn_t3"}
        assert not any(e["type"] == EventType.RUN_ERROR for e in events)
        assert events[-1]["type"] == EventType.STEP_FINISHED


class TestAgentCancellation:
    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_tool_calls_stop_at_checkpoint(self, mock_get_client):
        """五个 tool_calls，第二个执行完成后取消：第三个不执行"""
        counter = {"n": 0}

        def fn(context, inputs):
            counter["n"] += 1
            if counter["n"] == 2:
                context.cancel()
            return {"seq": counter["n"]}

        tool = Tool(name="ac_tool", description="", function=fn)
        turn = [
            _tool_calls_chunk([(f"tc{i}", "ac_tool", "{}") for i in range(1, 6)]),
            _finish_chunk("tool_calls"),
        ]
        mock_get_client.return_value = FakeStreamClient([turn])

        agent = Agent(name="worker", tools=[tool])
        events = _run(_collect(agent.stream(FlowContext(variables={"input": "go"}))))

        assert counter["n"] == 2
        results = [e for e in events if e["type"] == EventType.TOOL_CALL_RESULT]
        assert [e["toolCallId"] for e in results] == ["tc1", "tc2"]
        assert events[-1]["type"] == EventType.RUN_ERROR
        assert events[-1]["code"] == "CANCELLED"

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_cancel_before_first_turn_skips_llm(self, mock_get_client):
        """轮次开始检查点：预先取消的 context 不发起任何 LLM 调用"""
        client = FakeStreamClient([[_text_chunk("hi"), _finish_chunk()]])
        mock_get_client.return_value = client

        ctx = FlowContext(variables={"input": "go"})
        ctx.cancel()
        events = _run(_collect(Agent(name="worker").stream(ctx)))

        assert client.requests == []
        assert events[-1]["type"] == EventType.RUN_ERROR
        assert events[-1]["code"] == "CANCELLED"
