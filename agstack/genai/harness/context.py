#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""对话上下文压缩引擎：在 token 预算内构造模型历史（agstack-as-harness.md §3.1；3.0）

机制在这里，口径由应用给。引擎每轮按下面的顺序工作：

1. **预算分层**：历史预算 = 窗口 × (1 − 输出比例) − 已占用（system prompt / 检索等），非正数直接空历史；
2. **最近 N 条快速路径**：取最近 ``recent_limit`` 条（槽位口径由 :class:`HistorySource` 决定），估算总量在预算内
   就整窗送出，并记下行跨度 ``history_span`` 供应用落下一轮的 token 锚点；
3. **锚点计量**：上轮真实请求实测的历史 token 作锚点（:class:`TokenAnchor` 口径），本轮窗内历史 = 锚点实测
   − 滑出窗的行估算 + 锚点之后新增行估算；锚点失效（模型切换、锚点行不在窗内也找不回）时回退整体估算；
4. **消费摘要**：预算不足时只读已有摘要（绝不在请求关键路径上调模型），缺失 / 过期时经 :attr:`ContextPolicy.schedule`
   投递后台刷新，本轮降级为两遍装填截断；有摘要则 ``[摘要 system 消息] + 摘要之后的消息装填到剩余预算``；
5. **两遍装填**（:func:`fill_to_budget`）：先从新到旧装 user 消息至预算的 ``user_ratio``，再装其余消息至预算耗尽，
   输出按原时序——预算紧张时牺牲的是模型的长回答，用户的提问链保留；
6. **摘要刷新算法**（:meth:`ContextEngine.refresh_summary`）：无摘要且消息数 ≥ ``summary_min_messages`` 时全量摘要
   （覆盖到倒数第 ``keep_recent`` 条）；有摘要且其后新消息 ≥ ``incremental_threshold`` 时增量摘要（以旧摘要为底
   合并新消息，覆盖点同样留出最近 ``keep_recent`` 条）。摘要正文怎么生成（prompt / schema / 前缀复用）由
   ``summarize`` 回调给，怎么渲染进 system 消息由 ``render`` 回调给。

:func:`schedule_once` 是缺省的后台投递器：按会话 id 在途去重、以空 ``contextvars.Context`` 启动（剥离请求侧 ambient
session 与用量收集器），应用可换成自己的（如先绑定独立用量账目再跑）。
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .metering import anchored_estimate
from .ports import TokenAnchor


logger = logging.getLogger(__name__)

#: 每条消息的角色 / 格式开销（token）
MESSAGE_OVERHEAD = 4

#: 文本 token 估算（调用方注入，携带各自的模型与校准系数）
type Counter = Callable[[str], int]
#: 摘要生成：``(被摘要消息, previous=旧摘要正文或 None) -> 摘要正文 | None``（失败返回 None，引擎不落库）
type Summarizer = Callable[[Sequence[dict[str, Any]], str | None], Awaitable[str | None]]
#: 后台投递：``(会话 id, 协程工厂)``
type Scheduler = Callable[[Any, Callable[[], Awaitable[Any]]], None]


@dataclass(frozen=True, slots=True)
class SummaryRecord:
    """一条已落库的摘要

    :param content: 摘要正文（应用自己的格式，渲染经 ``render``）
    :param start_ref: 覆盖的首条消息引用（应用口径，如消息 id）
    :param end_ref: 覆盖的末条消息引用
    :param message_count: 覆盖的消息条数
    """

    content: str
    start_ref: Any
    end_ref: Any
    message_count: int = 0


class HistorySource(Protocol):
    """会话历史来源（应用按自己的表实现）"""

    async def recent(self, limit: int) -> tuple[Sequence[Any], Sequence[Any]]:
        """最近窗口 → ``(窗内行·时间正序, 取回的超集·从新到旧)``；超集留给锚点计量找回已滑出窗的行"""
        ...

    async def after(self, ref: Any) -> Sequence[Any]:
        """``ref`` 所指消息之后的全部行（时间正序）；ref 不存在返回空"""
        ...

    async def all_rows(self) -> Sequence[Any]:
        """会话全部有效行（时间正序）"""
        ...

    async def count(self) -> int:
        """会话有效行数"""
        ...

    def project(self, rows: Sequence[Any]) -> list[dict[str, Any]]:
        """行 → 模型消息（投影口径与主对话一致，摘要所见即模型所见）"""
        ...

    def ref_of(self, row: Any) -> Any:
        """行的引用（与锚点 ``first_id`` / ``last_id`` 同口径，可比较相等）"""
        ...


class SummaryStore(Protocol):
    """摘要存取（应用实现）"""

    async def latest(self) -> SummaryRecord | None: ...

    async def save(self, record: SummaryRecord) -> None: ...


@dataclass(frozen=True)
class ContextPolicy:
    """压缩策略数值（应用给；缺省值只是示例口径，不是推荐值）

    :param output_ratio: 窗口里留给输出的比例
    :param recent_limit: 快速路径最多装载的最近消息槽位数
    :param user_ratio: 两遍装填第一遍给 user 消息的预算比例
    :param summary_min_messages: 触发首次全量摘要的最小消息数
    :param incremental_threshold: 触发增量摘要的最小新消息数
    :param keep_recent: 摘要不覆盖的最近消息条数
    :param summary_header: 摘要 system 消息的首行标头
    :param render: 摘要正文 → 进 system 消息的文本（结构化摘要在此渲染）
    :param schedule: 后台刷新投递器；None 则不投递（纯只读消费已有摘要）
    """

    output_ratio: float = 0.25
    recent_limit: int = 20
    user_ratio: float = 0.6
    summary_min_messages: int = 16
    incremental_threshold: int = 10
    keep_recent: int = 6
    summary_header: str = "[对话历史摘要]"
    render: Callable[[str], str] = staticmethod(lambda content: content)
    schedule: Scheduler | None = None


def fill_to_budget(
    messages: Sequence[dict[str, Any]], budget: int, count: Counter, *, user_ratio: float = 0.6
) -> list[dict[str, Any]]:
    """预算内两遍装填，优先保 user 消息

    第一遍从新到旧装填 user 消息，至预算的 ``user_ratio`` 或 user 消息用尽；第二遍从新到旧装填其余消息至预算耗尽；
    输出按原时序排列。
    """
    if budget <= 0:
        return []

    def cost(msg: dict[str, Any]) -> int:
        content = msg.get("content", "")
        return count(content) + MESSAGE_OVERHEAD if content else MESSAGE_OVERHEAD

    selected: set[int] = set()
    used = 0
    user_budget = int(budget * user_ratio)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") != "user":
            continue
        tokens = cost(messages[i])
        if used + tokens > user_budget:
            break
        selected.add(i)
        used += tokens
    for i in range(len(messages) - 1, -1, -1):
        if i in selected or messages[i].get("role") == "user":
            continue
        tokens = cost(messages[i])
        if used + tokens > budget:
            break
        selected.add(i)
        used += tokens
    return [messages[i] for i in sorted(selected)]


def messages_tokens(messages: Sequence[dict[str, Any]], count: Counter) -> int:
    """消息列表的估算总量（正文估算 + 每条固定开销）"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if content:
            total += count(content)
        total += MESSAGE_OVERHEAD
    return total


def anchored_history_tokens(
    *,
    anchor: dict[str, Any] | None,
    model: str,
    selected: Sequence[Any],
    fetched_newest_first: Sequence[Any],
    ref_of: Callable[[Any], Any],
    project: Callable[[Sequence[Any]], Sequence[dict[str, Any]]],
    count: Counter,
    fallback: int,
) -> int:
    """窗内历史的 token（锚点计量）

    锚点形如 ``{first_id, last_id, history_tokens, model}``。有效条件：模型相同、锚点末行仍在窗内、锚点首行在窗首
    或可在超集里找回（窗口前移）；满足则 ``锚点实测 − 滑出窗的行估算 + 锚点之后新增行估算``，否则 ``fallback``。
    """
    if not isinstance(anchor, dict) or anchor.get("model") != model:
        return fallback
    ids = [str(ref_of(row)) for row in selected]
    first_id, last_id = str(anchor.get("first_id")), str(anchor.get("last_id"))
    if last_id not in ids:
        return fallback
    last_idx = ids.index(last_id)
    dropped: list[Any] = []
    if first_id in ids:
        if ids.index(first_id) != 0:
            return fallback  # 窗内还有锚点之前的行：锚点覆盖不全
    else:
        older = list(reversed(fetched_newest_first))  # 时间正序
        older_ids = [str(ref_of(row)) for row in older]
        if first_id not in older_ids or ids[0] not in older_ids:
            return fallback
        dropped = older[older_ids.index(first_id) : older_ids.index(ids[0])]
    dropped_tokens = messages_tokens(project(dropped), count) if dropped else 0
    tail_rows = list(selected[last_idx + 1 :])
    tail_tokens = messages_tokens(project(tail_rows), count) if tail_rows else 0
    base = TokenAnchor(
        anchor_ref=str(last_id),
        prompt_tokens=max(int(anchor.get("history_tokens") or 0) - dropped_tokens, 0),
        model=model,
    )
    return anchored_estimate(base, tail_tokens, fallback_tokens=fallback)


#: 在途后台刷新（会话 id → Task）：去重 + 强引用防 GC；多实例下最坏重复生成一次，读侧取最新，幂等可接受
_refresh_tasks: dict[Any, asyncio.Task[None]] = {}


def schedule_once(key: Any, factory: Callable[[], Awaitable[Any]]) -> None:
    """缺省后台投递：同一 ``key`` 在途即跳过；以空 ``contextvars.Context`` 启动；无事件循环时静默放弃"""
    if key in _refresh_tasks:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_await(factory), context=contextvars.Context())
    _refresh_tasks[key] = task
    task.add_done_callback(lambda _t, k=key: _refresh_tasks.pop(k, None))


async def _await(factory: Callable[[], Awaitable[Any]]) -> None:
    await factory()


def pending_refreshes() -> dict[Any, asyncio.Task[None]]:
    """在途后台刷新任务（测试 / 观测用）"""
    return _refresh_tasks


@dataclass
class ContextEngine:
    """一次会话、一轮请求的历史构造器

    :param source: 历史来源
    :param summaries: 摘要存取
    :param summarize: 摘要生成回调
    :param count: 文本 token 估算（已含校准系数）
    :param context_length: 模型窗口
    :param model: 生成模型名（锚点匹配）
    :param anchor: 上轮锚点 ``{first_id, last_id, history_tokens, model}``；None 为整体估算
    :param policy: 策略数值
    :param session_key: 后台刷新去重键（通常是会话 id）；None 则不投递
    """

    source: HistorySource
    summaries: SummaryStore
    summarize: Summarizer
    count: Counter
    context_length: int
    model: str
    anchor: dict[str, Any] | None = None
    policy: ContextPolicy = field(default_factory=ContextPolicy)
    session_key: Any = None
    #: 本轮以快速路径整窗送出的历史行跨度 ``{first_id, last_id}``（供落下一轮锚点）；截断 / 摘要路径为 None
    history_span: dict[str, str] | None = field(default=None, init=False)

    async def build_history(self, reserved_tokens: int = 0) -> list[dict[str, Any]]:
        """构造可直接作模型历史的消息列表"""
        self.history_span = None
        policy = self.policy
        budget = int(self.context_length * (1 - policy.output_ratio)) - reserved_tokens
        if budget <= 0:
            return []

        selected, fetched = await self.source.recent(policy.recent_limit)
        messages = self.source.project(selected) if selected else []
        if not messages:
            return []

        recent_tokens = anchored_history_tokens(
            anchor=self.anchor,
            model=self.model,
            selected=selected,
            fetched_newest_first=fetched,
            ref_of=self.source.ref_of,
            project=self.source.project,
            count=self.count,
            fallback=messages_tokens(messages, self.count),
        )
        if recent_tokens <= budget:
            self.history_span = {
                "first_id": str(self.source.ref_of(selected[0])),
                "last_id": str(self.source.ref_of(selected[-1])),
            }
            return messages

        try:
            summary = await self._consume_summary()
        except Exception:  # noqa: BLE001 — 摘要只是压缩优化，任何失败都不中断对话
            logger.warning("failed to load conversation summary", exc_info=True)
            summary = None
        if summary is None:
            return self.fill(messages, budget)

        rendered = policy.render(summary.content)
        summary_msg = {"role": "system", "content": f"{policy.summary_header}\n{rendered}"}
        remaining = budget - self.count(rendered)
        if remaining <= 0:
            return [summary_msg]
        after = self.source.project(await self.source.after(summary.end_ref))
        if not after:
            return [summary_msg]
        return [summary_msg, *self.fill(after, remaining)]

    def fill(self, messages: Sequence[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        """预算内两遍装填（见 :func:`fill_to_budget`）"""
        return fill_to_budget(messages, budget, self.count, user_ratio=self.policy.user_ratio)

    def tokens(self, messages: Sequence[dict[str, Any]]) -> int:
        """消息列表的估算总量"""
        return messages_tokens(messages, self.count)

    async def _consume_summary(self) -> SummaryRecord | None:
        """只读消费已有摘要；缺失 / 过期时投递后台刷新（本方法绝不调模型）"""
        existing = await self.summaries.latest()
        policy = self.policy
        if existing is not None:
            fresh = await self.source.after(existing.end_ref)
            if len(fresh) >= policy.incremental_threshold:
                self._schedule_refresh()
            return existing
        if await self.source.count() >= policy.summary_min_messages:
            self._schedule_refresh()
        return None

    def _schedule_refresh(self) -> None:
        if self.policy.schedule is None or self.session_key is None:
            return
        self.policy.schedule(self.session_key, self.refresh_summary)

    async def refresh_summary(self) -> SummaryRecord | None:
        """生成 / 更新摘要并落库（后台任务体；失败返回 None，下一轮自然重试）"""
        try:
            existing = await self.summaries.latest()
            if existing is not None:
                fresh = await self.source.after(existing.end_ref)
                if len(fresh) < self.policy.incremental_threshold:
                    return None
                return await self._incremental_summary(existing, fresh)
            return await self._full_summary()
        except Exception:  # noqa: BLE001 — 后台任务不冒泡
            logger.warning("background summary refresh failed (session=%s)", self.session_key, exc_info=True)
            return None

    async def _full_summary(self) -> SummaryRecord | None:
        rows = list(await self.source.all_rows())
        policy = self.policy
        if len(rows) < policy.summary_min_messages:
            return None
        cutoff = max(len(rows) - policy.keep_recent, policy.summary_min_messages // 2)
        covered = rows[:cutoff]
        content = await self.summarize(self.source.project(covered), None)
        if content is None:
            return None
        record = SummaryRecord(
            content=content,
            start_ref=self.source.ref_of(covered[0]),
            end_ref=self.source.ref_of(covered[-1]),
            message_count=len(covered),
        )
        await self.summaries.save(record)
        return record

    async def _incremental_summary(self, existing: SummaryRecord, fresh_rows: Sequence[Any]) -> SummaryRecord | None:
        content = await self.summarize(self.source.project(fresh_rows), existing.content)
        if content is None:
            return None
        keep = self.policy.keep_recent
        rows = list(await self.source.all_rows())
        end_ref = self.source.ref_of(rows[-keep - 1]) if len(rows) > keep else existing.end_ref
        record = SummaryRecord(
            content=content,
            start_ref=existing.start_ref,
            end_ref=end_ref,
            message_count=existing.message_count + len(fresh_rows),
        )
        await self.summaries.save(record)
        return record


__all__ = [
    "MESSAGE_OVERHEAD",
    "ContextEngine",
    "ContextPolicy",
    "Counter",
    "HistorySource",
    "Scheduler",
    "Summarizer",
    "SummaryRecord",
    "SummaryStore",
    "anchored_history_tokens",
    "fill_to_budget",
    "messages_tokens",
    "pending_refreshes",
    "schedule_once",
]
