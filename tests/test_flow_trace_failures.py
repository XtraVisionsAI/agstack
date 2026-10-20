#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""FlowTrace 失败收尾（3.3.0）：节点抛错 / 取消时 trace 节点仍收尾——记错误、已产生的工具记录与用量，
重试失败的尝试记入 ``attempts``；parallel / iteration 容器与 body 节点同样收尾。此前节点抛错时
``record_node_end`` 不被调用，trace 里该节点没有结束时间、没有错误，审计侧会把它当成「执行过且无错误」。
"""

import asyncio

import pytest

from agstack.genai.flow.context import FlowContext
from agstack.genai.flow.exceptions import NodeExecutionError, ToolExecutionError
from agstack.genai.flow.flow import Flow
from agstack.genai.flow.registry import registry
from agstack.genai.flow.tool import Tool


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _ok(context, inputs):
    return {"ok": True}


def _boom(context, inputs):
    context.execution_records.append({"tool_name": "inner_call", "success": True})
    raise ValueError("boom")


registry.register_tool("tf_ok", Tool(name="tf_ok", description="", function=_ok))
registry.register_tool("tf_boom", Tool(name="tf_boom", description="", function=_boom))


def _two_node_flow(second: dict, extra_nodes: list | None = None, extra_edges: list | None = None) -> Flow:
    return Flow(
        flow_id="t",
        name="t",
        nodes=[{"id": "first", "type": "tool", "config": {"tool_name": "tf_ok"}}, second, *(extra_nodes or [])],
        edges=[{"source": "first", "target": second["id"]}, *(extra_edges or [])],
    )


def _node(ctx: FlowContext, node_id: str):
    return next(n for n in ctx.trace.nodes if n.node_id == node_id)


def test_failed_node_is_closed_with_error_and_tool_records():
    flow = _two_node_flow({"id": "bad", "type": "tool", "config": {"tool_name": "tf_boom"}})
    ctx = FlowContext()
    with pytest.raises(NodeExecutionError):
        _run(flow.run(ctx))
    bad = _node(ctx, "bad")
    assert bad.finished_at is not None and bad.duration_ms is not None
    assert bad.error and "boom" in bad.error
    assert any(tc.get("tool_name") == "inner_call" for tc in bad.tool_calls)
    assert ctx.trace._active_nodes == {}


def test_retry_failures_recorded_as_attempts():
    calls = {"n": 0}

    def flaky(context, inputs):
        calls["n"] += 1
        context.execution_records.append({"tool_name": f"try{calls['n']}"})
        if calls["n"] < 3:
            raise ValueError(f"fail {calls['n']}")
        return {"ok": True}

    registry.register_tool("tf_flaky", Tool(name="tf_flaky", description="", function=flaky))
    flow = _two_node_flow(
        {
            "id": "job",
            "type": "tool",
            "config": {"tool_name": "tf_flaky", "retry": {"max_retries": 3, "delay": 0.001, "backoff": 1.0}},
        }
    )
    ctx = FlowContext()
    _run(flow.run(ctx))
    job = _node(ctx, "job")
    assert job.error is None
    assert [a["attempt"] for a in job.attempts] == [1, 2]
    assert "fail 1" in job.attempts[0]["error"]
    # 失败尝试的工具记录归属该次尝试，不混进最终成功的那次
    assert any(tc.get("tool_name") == "try1" for tc in job.attempts[0]["tool_calls"])
    assert all(tc.get("tool_name") != "try1" for tc in job.tool_calls)


def test_iterator_fallback_still_closes_node():
    flow = _two_node_flow(
        {"id": "loop", "type": "iterator", "config": {"items": "$v.items", "max_iterations": 2}},
        extra_nodes=[{"id": "body", "type": "tool", "config": {"tool_name": "tf_boom"}}],
        extra_edges=[
            {"source": "loop", "target": "body", "condition": "$o.loop.done == false"},
            {"source": "body", "target": "loop"},
        ],
    )
    ctx = FlowContext(variables={"items": ["a"]})
    _run(flow.run(ctx))
    body = _node(ctx, "body")
    assert body.finished_at is not None and "boom" in (body.error or "")


def test_parallel_container_closed_when_branch_fails():
    flow = _two_node_flow(
        {"id": "fan", "type": "parallel", "config": {"branches": ["b_ok", "b_bad"]}},
        extra_nodes=[
            {"id": "b_ok", "type": "tool", "config": {"tool_name": "tf_ok"}},
            {"id": "b_bad", "type": "tool", "config": {"tool_name": "tf_boom"}},
        ],
    )
    ctx = FlowContext()
    with pytest.raises(ToolExecutionError):
        _run(flow.run(ctx))
    fan = _node(ctx, "fan")
    assert fan.finished_at is not None and fan.error
    assert ctx.trace._active_nodes == {}


def test_iteration_body_failure_closes_body_and_container():
    flow = _two_node_flow(
        {"id": "each", "type": "iteration", "config": {"items": ["x"], "body": ["step"]}},
        extra_nodes=[{"id": "step", "type": "tool", "config": {"tool_name": "tf_boom"}}],
    )
    ctx = FlowContext()
    with pytest.raises(ToolExecutionError):
        _run(flow.run(ctx))
    assert "boom" in (_node(ctx, "step").error or "")
    assert _node(ctx, "each").error
    assert ctx.trace._active_nodes == {}


def test_cancelled_task_closes_running_node():
    started = asyncio.Event()

    async def slow(context, inputs):
        started.set()
        await asyncio.sleep(10)
        return {}

    registry.register_tool("tf_slow", Tool(name="tf_slow", description="", function=slow))
    flow = _two_node_flow({"id": "slow", "type": "tool", "config": {"tool_name": "tf_slow"}})
    ctx = FlowContext()

    async def main():
        task = asyncio.create_task(flow.run(ctx))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(main())
    slow_node = _node(ctx, "slow")
    assert slow_node.error == "CANCELLED" and slow_node.finished_at is not None
