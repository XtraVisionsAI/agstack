#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""工具执行管线钩子（F4）验收用例

registry 级全局钩子链织入 Tool.execute_async（所有调用路径的唯一咽喉）：
pre_execute 按注册顺序（可改写入参，Deny/抛异常＝拒绝，fail closed），
post_execute 按逆序（可改写结果，抛异常＝放行，fail open）。
Deny 的失败结果同样穿过 post 链；execution_records 记录改写后的入参。
"""

from unittest.mock import patch

import pytest

from agstack.llm.flow.agent import Agent
from agstack.llm.flow.context import FlowContext
from agstack.llm.flow.event import EventType
from agstack.llm.flow.registry import registry
from agstack.llm.flow.tool import Deny, Tool, ToolHook, ToolResult
from tests.test_flow_error_semantics import (
    FakeStreamClient,
    _collect,
    _finish_chunk,
    _run,
    _text_chunk,
    _tool_call_chunk,
)


@pytest.fixture(autouse=True)
def _isolated_hooks():
    """全局钩子链测试隔离"""
    registry.clear_tool_hooks()
    yield
    registry.clear_tool_hooks()


def _echo_tool(seen: dict) -> Tool:
    def fn(context, inputs):
        seen["inputs"] = inputs
        seen["called"] = seen.get("called", 0) + 1
        return {"echo": inputs}

    return Tool(name="echo", description="", function=fn)


class TestPreExecute:
    def test_rewrite_inputs_reaches_tool_and_records(self):
        """pre 改写入参：工具函数收到改写后入参，execution_records 记录的也是改写后的"""

        class Rewrite(ToolHook):
            async def pre_execute(self, context, tool, inputs):
                return {**inputs, "tenant_id": "t-42"}

        registry.register_tool_hook(Rewrite())
        seen: dict = {}
        ctx = FlowContext()
        result = _run(_echo_tool(seen).execute_async(ctx, {"q": "hi"}))

        assert seen["inputs"] == {"q": "hi", "tenant_id": "t-42"}
        assert result.arguments == {"q": "hi", "tenant_id": "t-42"}
        assert ctx.execution_records[-1]["tool_args"] == {"q": "hi", "tenant_id": "t-42"}

    def test_deny_skips_tool_and_returns_failure(self):
        """pre 返回 Deny：工具函数不执行，失败结果带 reason"""

        class Gate(ToolHook):
            async def pre_execute(self, context, tool, inputs):
                return Deny("no permission for kb")

        registry.register_tool_hook(Gate())
        seen: dict = {}
        result = _run(_echo_tool(seen).execute_async(FlowContext(), {"q": "hi"}))

        assert "called" not in seen
        assert result.success is False
        assert result.error == "no permission for kb"
        assert "no permission" in (result.content or "")

    def test_pre_exception_is_fail_closed(self):
        """pre 抛异常按 Deny 处理：权限门自己出错时不放行"""

        class Broken(ToolHook):
            async def pre_execute(self, context, tool, inputs):
                raise RuntimeError("gate crashed")

        registry.register_tool_hook(Broken())
        seen: dict = {}
        result = _run(_echo_tool(seen).execute_async(FlowContext(), {}))

        assert "called" not in seen
        assert result.success is False
        assert "gate crashed" in (result.error or "")

    @patch("agstack.llm.flow.agent.get_llm_client")
    def test_deny_feeds_model_without_breaking_loop(self, mock_get_client):
        """agent 循环里被拒绝：模型收到失败 tool message，flow 不中断，下一轮照常"""

        class Gate(ToolHook):
            async def pre_execute(self, context, tool, inputs):
                return Deny("denied by policy")

        registry.register_tool_hook(Gate())
        seen: dict = {}
        client = FakeStreamClient(
            [
                [_tool_call_chunk("tc1", "echo", "{}"), _finish_chunk("tool_calls")],
                [_text_chunk("ok, changing plan"), _finish_chunk()],
            ]
        )
        mock_get_client.return_value = client

        ctx = FlowContext(variables={"input": "go"})
        events = _run(_collect(Agent(name="worker", tools=[_echo_tool(seen)]).stream(ctx)))

        assert "called" not in seen
        results = [e for e in events if e["type"] == EventType.TOOL_CALL_RESULT]
        assert len(results) == 1 and "denied by policy" in results[0]["content"]
        assert ctx.outputs["worker"] == {"result": "ok, changing plan"}  # 第二轮正常收尾
        tool_msgs = [m for m in client.requests[1]["messages"] if m.get("role") == "tool"]
        assert "denied by policy" in tool_msgs[0]["content"]


class TestPostExecute:
    def test_truncation_applies_to_all_outlets(self):
        """post 截断：ToolResult.result、LLM 消费内容、execution_records 三处一致"""

        class Truncate(ToolHook):
            async def post_execute(self, context, tool, result):
                result.result = {"echo": "[truncated]"}
                return result

        registry.register_tool_hook(Truncate())
        seen: dict = {}
        ctx = FlowContext()
        result = _run(_echo_tool(seen).execute_async(ctx, {"q": "x" * 100}))

        assert result.result == {"echo": "[truncated]"}
        assert "[truncated]" in (result.content or "")
        assert "[truncated]" in ctx.execution_records[-1]["result"]

    def test_post_exception_is_fail_open(self):
        """post 抛异常：记日志放行原结果，主流程不受影响"""

        class BrokenAudit(ToolHook):
            async def post_execute(self, context, tool, result):
                raise RuntimeError("audit db down")

        registry.register_tool_hook(BrokenAudit())
        seen: dict = {}
        result = _run(_echo_tool(seen).execute_async(FlowContext(), {"q": "hi"}))

        assert result.success is True
        assert result.result == {"echo": {"q": "hi"}}

    def test_denied_result_still_passes_post_chain(self):
        """Deny 的失败结果也穿过 post 链：审计钩子看得到被拒绝的调用"""
        audited: list[ToolResult] = []

        class Gate(ToolHook):
            async def pre_execute(self, context, tool, inputs):
                return Deny("nope")

        class Audit(ToolHook):
            async def post_execute(self, context, tool, result):
                audited.append(result)
                return result

        registry.register_tool_hook(Gate())
        registry.register_tool_hook(Audit())
        _run(_echo_tool({}).execute_async(FlowContext(), {}))

        assert len(audited) == 1
        assert audited[0].success is False and audited[0].error == "nope"


class TestChainOrdering:
    def _order_hook(self, name: str, log: list[str]) -> ToolHook:
        class H(ToolHook):
            async def pre_execute(self, context, tool, inputs, _n=name, _log=log):
                _log.append(f"pre:{_n}")
                return inputs

            async def post_execute(self, context, tool, result, _n=name, _log=log):
                _log.append(f"post:{_n}")
                return result

        return H()

    def test_pre_in_order_post_reversed(self):
        """pre 按注册顺序、post 按逆序（洋葱模型）"""
        log: list[str] = []
        registry.register_tool_hook(self._order_hook("a", log))
        registry.register_tool_hook(self._order_hook("b", log))
        _run(_echo_tool({}).execute_async(FlowContext(), {}))

        assert log == ["pre:a", "pre:b", "post:b", "post:a"]

    def test_prepend_takes_outermost_position(self):
        """prepend=True 抢占链头：其 post 成为最外层（spill 类钩子的需求）"""
        log: list[str] = []
        registry.register_tool_hook(self._order_hook("a", log))
        registry.register_tool_hook(self._order_hook("spill", log), prepend=True)
        _run(_echo_tool({}).execute_async(FlowContext(), {}))

        assert log == ["pre:spill", "pre:a", "post:a", "post:spill"]


class TestNoHooks:
    def test_no_hooks_behavior_unchanged(self):
        """未注册任何钩子：行为与 2.0.0 一致"""
        seen: dict = {}
        ctx = FlowContext()
        result = _run(_echo_tool(seen).execute_async(ctx, {"q": "hi"}))

        assert result.success is True
        assert result.result == {"echo": {"q": "hi"}}
        assert ctx.execution_records[-1]["tool_args"] == {"q": "hi"}
