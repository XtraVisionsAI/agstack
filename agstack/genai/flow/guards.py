#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Agent 工具调用守卫（AgentGuards）

「红线在代码」的一层：模型在给到的工具里自由决策，但以下几条**不靠提示词**，由本模块在
``Agent._stream_tool_call`` 前后执行：

- 同名同参重复调用直接退回上次结果（:attr:`AgentGuards.duplicate_kind`）；
- 受上限工具族累计调用达上限后返回「预算用尽」不再执行（:attr:`AgentGuards.cap_kind`）；
- 应用自定义的执行前守卫（:attr:`AgentGuards.checks`，如「先库后网」）；
- 工具结果累计 token 超预算时把最早的结果折叠为摘要（:attr:`AgentGuards.fold_kind`；最新一条永不折叠）；
- 工具结果末尾附应用给的提示（:attr:`AgentGuards.hint`，如「够了就停」的取材提示）。

每个守卫动作写一条与 Tool 管线同形的执行记录进 ``context.execution_records``（随节点 trace 持久化，审计按
``tool_name`` 呈现）。本模块只有机制；受上限的工具族、上限值、折叠渲染、提示文案、自定义规则全由应用在
:class:`AgentGuards` 里给。

另附 :func:`buffer_plan_text`：按轮缓冲助手文字——轮以工具调用结束时文字只进 trace（「这次为什么调它」），轮以
文字结束时放流；轮次耗尽且无可展示文字时按应用给的 closing_line 收尾，不把空串或已记为计划的文字当回答交出去。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import event
from .context import FlowContext
from .event import EventType


logger = logging.getLogger(__name__)

#: 守卫动作的缺省 ``tool_name``（审计呈现用；应用可在 :class:`AgentGuards` 覆盖）
GUARD_DUPLICATE = "guard_duplicate_call"
GUARD_CAP = "guard_call_cap"
GUARD_FOLD = "guard_fold_results"
#: 计划记录的缺省 ``tool_name``
PLAN_RECORD = "agent_plan"

#: 执行前守卫：``(state, tool_name, raw_arguments) -> (守卫种类, 退给模型的结果) | None``；None 放行
type GuardCheck = Callable[["GuardState", str, str], tuple[str, dict[str, Any]] | None]
#: 折叠渲染：``(context, 原工具结果文本) -> 折叠后文本``
type FoldRenderer = Callable[[FlowContext, str], str]
#: 结果提示：``(state, tool_name, 结果文本) -> 追加到末尾的提示 | None``
type HintBuilder = Callable[["GuardState", str, str], str | None]
#: token 计数：``(text, model) -> tokens``
type TokenCounter = Callable[[str, str], int]


@dataclass
class GuardState:
    """一次 Agent 运行的守卫计数（每次 ``stream`` 开始时 :meth:`reset`）

    :param cap: 本次运行的工具族上限；None 取 :attr:`AgentGuards.cap`
    :param seen_calls: 调用签名 → tool_call_id（重复调用判定）
    :param calls: 工具名 → 累计放行次数
    :param family_calls: 受上限工具族累计放行次数
    """

    cap: int | None = None
    seen_calls: dict[str, str] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)
    family_calls: int = 0

    def reset(self) -> None:
        self.seen_calls = {}
        self.calls = {}
        self.family_calls = 0

    def calls_of(self, *names: str) -> int:
        """给定工具名的累计放行次数之和"""
        return sum(self.calls.get(n, 0) for n in names)


def _default_duplicate_payload(previous_call_id: str) -> dict[str, Any]:
    return {
        "note": "与此前一次调用的工具与参数完全相同，结果未变（见前一次结果）；请换查询或直接作答",
        "previous_tool_call_id": previous_call_id,
    }


def _default_cap_payload(cap: int) -> dict[str, Any]:
    return {
        "error": "CALL_BUDGET_EXHAUSTED",
        "hint": f"本轮该类工具调用次数已达上限（{cap} 次），请基于已取得的结果作答并说明未覆盖之处",
    }


@dataclass
class AgentGuards:
    """守卫策略（应用给值）

    :param capped_family: 累计调用受上限的工具族（空＝不限）
    :param cap: 工具族累计调用上限缺省值（:attr:`GuardState.cap` 可按次运行覆盖）
    :param checks: 应用自定义执行前守卫，在重复调用与上限之后按序执行
    :param hint: 工具结果末尾附加的提示构造器（None＝不附）
    :param fold_budget_ratio: 工具结果累计 token 预算 = ``context_length // ratio``（0＝不折叠）
    :param fold_renderer: 折叠后文本的渲染（None＝固定一句通知）
    :param count_tokens: 折叠判定用的 token 计数（None＝不折叠）
    :param context_length_var / model_var: 从 ``context.variables`` 取上下文窗口与模型名的键
    :param duplicate_kind / cap_kind / fold_kind: 三类守卫动作写执行记录时的 ``tool_name``
    :param duplicate_payload / cap_payload: 退给模型的结果构造
    """

    capped_family: tuple[str, ...] = ()
    cap: int = 5
    checks: tuple[GuardCheck, ...] = ()
    hint: HintBuilder | None = None
    fold_budget_ratio: int = 3
    fold_renderer: FoldRenderer | None = None
    count_tokens: TokenCounter | None = None
    context_length_var: str = "context_length"
    context_length_default: int = 32768
    model_var: str = "llm_model"
    duplicate_kind: str = GUARD_DUPLICATE
    cap_kind: str = GUARD_CAP
    fold_kind: str = GUARD_FOLD
    duplicate_payload: Callable[[str], dict[str, Any]] = _default_duplicate_payload
    cap_payload: Callable[[int], dict[str, Any]] = _default_cap_payload


# ── 纯函数 ──


def call_signature(name: str, arguments: str) -> str:
    """同名同参重复调用的判定键：参数 JSON 规范化（键排序）后与工具名拼接"""
    try:
        parsed = json.loads(arguments) if arguments else {}
    except ValueError:
        parsed = arguments
    return f"{name}:{json.dumps(parsed, ensure_ascii=False, sort_keys=True)}"


def check_tool_call(
    guards: AgentGuards, state: GuardState, name: str, arguments: str
) -> tuple[str, dict[str, Any]] | None:
    """执行前守卫：返回 ``(守卫种类, 退给模型的结果)``；None 表示放行

    判定顺序：重复调用 → 工具族上限 → 应用自定义 checks。只读 ``state``，放行后由 :func:`note_tool_call` 记账。
    """
    previous = state.seen_calls.get(call_signature(name, arguments))
    if previous is not None:
        return guards.duplicate_kind, guards.duplicate_payload(previous)
    cap = state.cap if state.cap is not None else guards.cap
    if name in guards.capped_family and state.family_calls >= cap:
        return guards.cap_kind, guards.cap_payload(cap)
    for check in guards.checks:
        hit = check(state, name, arguments)
        if hit is not None:
            return hit
    return None


def note_tool_call(guards: AgentGuards, state: GuardState, name: str, arguments: str, call_id: str) -> None:
    """放行后记账：登记签名、累计工具与工具族次数"""
    state.seen_calls[call_signature(name, arguments)] = call_id
    state.calls[name] = state.calls.get(name, 0) + 1
    if name in guards.capped_family:
        state.family_calls += 1


_FOLDED_NOTE = "（该工具结果已折叠以节省上下文）"


def fold_tool_messages(
    context: FlowContext,
    agent_name: str,
    *,
    budget_tokens: int,
    model: str,
    count_tokens: TokenCounter,
    render: FoldRenderer | None = None,
) -> int:
    """工具结果累计超预算时，从最早的工具消息起折叠，返回折叠条数

    最新一条工具消息永不折叠（模型正要读它）；已折叠的消息（``_folded``）不重复处理。
    """
    messages = context.messages.get(agent_name) or []
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_idx) < 2:
        return 0
    sizes = {i: count_tokens(str(messages[i].get("content") or ""), model) for i in tool_idx}
    total = sum(sizes.values())
    if total <= budget_tokens:
        return 0
    folded = 0
    for i in tool_idx[:-1]:
        if total <= budget_tokens:
            break
        msg = messages[i]
        if msg.get("_folded"):
            continue
        content = str(msg.get("content") or "")
        replacement = render(context, content) if render else _FOLDED_NOTE
        messages[i] = {**msg, "content": replacement, "_folded": True}
        total -= sizes[i] - count_tokens(replacement, model)
        folded += 1
    if folded:
        logger.info("[%s] folded %d tool results (total=%d budget=%d)", agent_name, folded, total, budget_tokens)
    return folded


def add_trace_record(
    context: FlowContext,
    tool_name: str,
    *,
    args: dict[str, Any] | None = None,
    result: str = "",
    summary: str | None = None,
) -> None:
    """守卫动作 / 调用计划写一条执行记录（与 Tool 管线写的记录同形，随节点 trace 的 ``tool_calls`` 持久化）"""
    context.execution_records.append(
        {
            "agent_call_id": context.get_variable("_agent_call_id"),
            "tool_name": tool_name,
            "tool_args": args or {},
            "success": True,
            "result": result,
            "error": None,
            "duration_ms": 0,
            "summary": summary,
        }
    )


# ── Agent mixin ──


class GuardedToolCalls:
    """给 ``Agent`` 子类接守卫的 mixin：宿主须有 ``guards: AgentGuards``、``guard: GuardState``、``name``、``model``，
    并在 MRO 上先于 ``Agent``

    ``_stream_tool_call`` 执行前跑 :func:`check_tool_call`，放行后记账并交给基类执行；执行后给结果附提示，
    再按上下文窗口预算折叠最早的工具结果。
    """

    guards: AgentGuards
    guard: GuardState
    name: str
    model: str

    async def _stream_tool_call(
        self,
        context: FlowContext,
        tool_call: dict[str, Any],
        message_sink: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        name = tool_call["name"]
        arguments = tool_call.get("arguments") or ""
        hit = check_tool_call(self.guards, self.guard, name, arguments)
        if hit is not None:
            kind, payload = hit
            content = json.dumps(payload, ensure_ascii=False)
            if message_sink is None:
                context.add_message(self.name, "tool", content=content, tool_call_id=tool_call["id"])
            else:
                message_sink.append({"content": content, "tool_call_id": tool_call["id"]})
            add_trace_record(
                context, kind, args={"tool": name, "arguments": arguments}, result=content, summary=f"守卫拦截：{name}"
            )
            yield event.tool_call_result(tool_call_id=tool_call["id"], content=content)
            return
        note_tool_call(self.guards, self.guard, name, arguments, tool_call["id"])
        async for evt in super()._stream_tool_call(context, tool_call, message_sink):  # type: ignore[misc]
            yield evt
        self._append_hint(context, name, tool_call["id"], message_sink)
        self._fold(context)

    def _fold(self, context: FlowContext) -> None:
        guards = self.guards
        if guards.count_tokens is None or guards.fold_budget_ratio <= 0:
            return
        context_length = int(
            context.get_variable(guards.context_length_var, guards.context_length_default)
            or guards.context_length_default
        )
        budget = context_length // guards.fold_budget_ratio
        folded = fold_tool_messages(
            context,
            self.name,
            budget_tokens=budget,
            model=str(context.get_variable(guards.model_var) or self.model),
            count_tokens=guards.count_tokens,
            render=guards.fold_renderer,
        )
        if folded:
            add_trace_record(
                context,
                guards.fold_kind,
                args={"folded": folded, "budget_tokens": budget},
                summary=f"守卫折叠：最早 {folded} 条工具结果折叠为摘要",
            )

    def _append_hint(
        self, context: FlowContext, name: str, call_id: str, message_sink: list[dict[str, Any]] | None
    ) -> None:
        """把提示追加到刚写回的 tool 消息末尾（消息在 sink 或 context 里，按 tool_call_id 定位）"""
        if self.guards.hint is None:
            return
        target: dict[str, Any] | None = None
        if message_sink is not None:
            target = next((m for m in reversed(message_sink) if m.get("tool_call_id") == call_id), None)
        else:
            messages = context.messages.get(self.name) or []
            target = next(
                (m for m in reversed(messages) if m.get("role") == "tool" and m.get("tool_call_id") == call_id), None
            )
        if target is None:
            return
        content = str(target.get("content") or "")
        hint = self.guards.hint(self.guard, name, content)
        if hint:
            target["content"] = content + "\n" + hint


# ── 过程话语缓冲 ──


async def buffer_plan_text(
    events: AsyncIterator[dict[str, Any]],
    context: FlowContext,
    *,
    agent_name: str,
    buffer_chars: int = 200,
    plan_kind: str = PLAN_RECORD,
    closing_line: Callable[[Sequence[dict[str, Any]]], str | None] | None = None,
    truncated_line: str = "本轮未能在规定步骤内整理出回答，请换种说法再试一次。",
    empty_line: str = "本轮没有生成出回答（模型输出为空），请重试或换种说法。",
) -> AsyncIterator[dict[str, Any]]:
    """包装 ``Agent.stream`` 的事件流：按轮缓冲助手文字，把「调工具前的过程话语」记为计划而不放流

    - 轮以工具调用结束：缓冲内的文字写一条 ``plan_kind`` 执行记录（``args.next_tool`` 为随后调用的工具、
      ``args.streamed`` 标记文字是否已有部分放流），不交给用户；
    - 轮以文字结束：缓冲整体放流；单轮文字超过 ``buffer_chars`` 时从该点起实时放流（长答不等整段）；
    - 轮次耗尽（输出带 ``truncated``）或末轮无文字：按 ``closing_line(messages)`` 收尾，其返回 None 时用
      ``truncated_line`` / ``empty_line``，并把输出 ``result`` 改写为该句。
    """
    buffer: list[str] = []
    streamed = ""  # 本轮已放流的文字（超过缓冲阈值后开始放流）
    plan_done = False  # 本轮文字已记为计划（该轮有工具调用）
    results_seen = False  # 本轮已出现工具结果：下一个 TEXT / TOOL_CALL_START 属于新一轮
    async for evt in events:
        etype = evt.get("type")
        if etype == EventType.TEXT_MESSAGE_CONTENT:
            if results_seen:
                buffer, streamed, plan_done, results_seen = [], "", False, False
            delta = str(evt.get("delta") or "")
            if streamed:
                streamed += delta
                yield evt
                continue
            buffer.append(delta)
            if sum(len(piece) for piece in buffer) >= buffer_chars:
                streamed = "".join(buffer)
                buffer = []
                yield event.text_message_content(message_id=str(evt.get("messageId") or ""), delta=streamed)
            continue
        if etype == EventType.TOOL_CALL_START:
            if results_seen:
                buffer, streamed, plan_done, results_seen = [], "", False, False
            if not plan_done:
                plan_done = True
                text = (streamed + "".join(buffer)).strip()
                buffer = []
                if text:
                    add_trace_record(
                        context,
                        plan_kind,
                        args={"next_tool": str(evt.get("toolCallName") or ""), "streamed": bool(streamed)},
                        result=text,
                        summary=("（已展示给用户）" if streamed else "") + text[:80],
                    )
            yield evt
            continue
        if etype == EventType.TOOL_CALL_RESULT:
            results_seen = True
            yield evt
            continue
        if etype == EventType.TEXT_MESSAGE_END:
            msg_id = str(evt.get("messageId") or "")
            if buffer:
                streamed += "".join(buffer)
                yield event.text_message_content(message_id=msg_id, delta="".join(buffer))
                buffer = []
            out = context.outputs.get(agent_name)
            if isinstance(out, dict) and not streamed.strip():
                # 轮次耗尽：最后一轮文字若已被记为计划（未展示），不能当回答交出去；末轮 content 为空同样不能交出空串
                line = closing_line(context.get_messages(agent_name)) if closing_line else None
                if out.get("truncated"):
                    line = line or truncated_line
                else:
                    line = line or empty_line
                    logger.warning("[%s] empty final content, fallback line emitted", agent_name)
                yield event.text_message_content(message_id=msg_id, delta=line)
                context.set_output(agent_name, {**out, "result": line})
        yield evt


__all__ = [
    "GUARD_CAP",
    "GUARD_DUPLICATE",
    "GUARD_FOLD",
    "PLAN_RECORD",
    "AgentGuards",
    "FoldRenderer",
    "GuardCheck",
    "GuardState",
    "GuardedToolCalls",
    "HintBuilder",
    "TokenCounter",
    "add_trace_record",
    "buffer_plan_text",
    "call_signature",
    "check_tool_call",
    "fold_tool_messages",
    "note_tool_call",
]
