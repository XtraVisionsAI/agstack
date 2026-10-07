#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""spill 钩子：落盘替换为头尾 + 通知、排除名单、无 SpillStore 空操作、存储失败保留内联；
以及 Tool.execute_async 的 content 顺序语义（post 钩子可改 content；换 result 未动 content 则重算）。
"""

import asyncio
from uuid import uuid4

import pytest

from agstack.llm.flow.context import FlowContext
from agstack.llm.flow.tool import Tool, ToolHook, ToolResult, clear_tool_hooks, register_tool_hook
from agstack.llm.harness import SpillHook, SpillOwner, SpillPolicy, SpillRef, clear_ports, register_ports


@pytest.fixture(autouse=True)
def _isolated():
    clear_tool_hooks()
    clear_ports()
    yield
    clear_tool_hooks()
    clear_ports()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Store:
    def __init__(self, fail=False):
        self.fail = fail
        self.saved: list[tuple] = []

    async def save_text(self, owner, source, suggested_name, content):
        if self.fail:
            raise OSError("disk full")
        self.saved.append((owner, source, suggested_name, content))
        return SpillRef(
            locator=f"spill:{len(self.saved)}", chars=len(content), tokens=len(content), name=suggested_name
        )

    async def read_text(self, locator, *, offset=0, limit=None):
        return self.saved[int(locator.split(":")[1]) - 1][3][offset : None if limit is None else offset + limit]


def _policy(max_inline=100, exclude=()):
    return SpillPolicy(
        max_inline_tokens=max_inline,
        count_tokens=len,
        owner_of=lambda ctx: SpillOwner(user_id=ctx.user_id, session_id=ctx.thread_id),
        exclude_tools=frozenset(exclude),
        head_chars=20,
        tail_chars=10,
    )


def _big_tool(name="big", n=500):
    return Tool(name, "d", lambda ctx, inputs: {"text": "T" * n})


def _ctx():
    return FlowContext(user_id=uuid4(), thread_id="topic-1")


def test_spill_replaces_content_and_records_ref():
    store = _Store()
    register_ports(spill=store)
    register_tool_hook(SpillHook(_policy()), prepend=True)
    result = _run(_big_tool().execute_async(_ctx(), {}))
    assert len(store.saved) == 1
    owner, source, name, content = store.saved[0]
    assert source.tool == "big" and owner.session_id == "topic-1" and content.startswith('{"text"')
    text = result.content or ""
    assert text.startswith(content[:20]) and text.endswith(content[-10:])
    assert "locator=spill:1" in text and "超出内联上限" in text
    assert result.metadata["spill"]["locator"] == "spill:1"
    # result.result 形状不变
    assert result.result == {"text": "T" * 500}


def test_spill_skips_small_excluded_failed_and_without_store():
    store = _Store()
    register_tool_hook(SpillHook(_policy(exclude=("read_spill",))), prepend=True)
    # 未注册 SpillStore：空操作
    r = _run(_big_tool().execute_async(_ctx(), {}))
    assert "spill" not in r.metadata and len(r.content or "") > 100
    register_ports(spill=store)
    # 小结果不落盘
    small = Tool("small", "d", lambda c, i: {"t": "x"})
    assert "spill" not in _run(small.execute_async(_ctx(), {})).metadata
    # 排除名单
    assert "spill" not in _run(_big_tool("read_spill").execute_async(_ctx(), {})).metadata

    # 失败结果不落盘
    def boom(c, i):
        raise ValueError("x" * 500)

    assert "spill" not in _run(Tool("f", "d", boom).execute_async(_ctx(), {})).metadata
    assert store.saved == []


def test_store_failure_keeps_inline():
    register_ports(spill=_Store(fail=True))
    register_tool_hook(SpillHook(_policy()), prepend=True)
    result = _run(_big_tool().execute_async(_ctx(), {}))
    assert "spill" not in result.metadata and result.content == '{"text": "' + "T" * 500 + '"}'


def test_post_hook_sees_rendered_content_and_can_rewrite_it():
    seen = {}

    class Trunc(ToolHook):
        async def post_execute(self, context, tool, result):
            seen["content"] = result.content
            result.content = "short"
            return result

    register_tool_hook(Trunc())
    tool = Tool("t", "d", lambda c, i: {"a": 1}, result_formatter=lambda r: f"formatted:{r.result['a']}")
    result = _run(tool.execute_async(_ctx(), {}))
    assert seen["content"] == "formatted:1" and result.content == "short"
    assert _ctx().execution_records == [] and result.content == "short"


def test_post_hook_replacing_result_recomputes_content():
    class Swap(ToolHook):
        async def post_execute(self, context, tool, result):
            return ToolResult(name=tool.name, arguments=result.arguments, result={"a": 2}, success=True)

    register_tool_hook(Swap())
    tool = Tool("t", "d", lambda c, i: {"a": 1}, result_formatter=lambda r: f"formatted:{r.result['a']}")
    ctx = _ctx()
    result = _run(tool.execute_async(ctx, {}))
    assert result.content == "formatted:2"
    assert ctx.execution_records[0]["result"] == "formatted:2"
