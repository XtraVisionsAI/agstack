#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""存储端口（SPI）：库与应用之间的分层缝，唯一实现、由应用在进程入口注册

agstack 只声明 Protocol 与数据类型，不含任何存储实现；应用（如 MetaMatrix 的 ``common.harness``）用自己的表 /
文件系统实现后经 :func:`register_ports` 注入。端口刻意少：会话日志（追加 / 读取 / 遮蔽 / 锚点）、落盘（spill）、
用量汇、可选 KV。

``LogEvent`` 借「仅追加日志 + 投影」思路但不做事件溯源：``kind`` 取值见 :data:`LOG_KINDS`；被重试 / 溢出恢复 / 折叠
取代的行不删，由 ``shadowed_by`` 指向取代它的行（应用自定引用：行 id 或序号的字符串），投影只消费
``shadowed_by is None`` 的行。

**seq 口径（3.1 裁定）**：``SessionLog`` 的 ``session_id`` 是**一次运行**（应用里的任务 / run），不是对话话题；``seq``
是该运行内由应用的事件枢纽（:class:`~agstack.genai.harness.events.EventHub`）分配的单调序号——一次运行只在一个进程内
执行，序号天然无跨进程冲突，也不依赖时钟。话题级的消息顺序由应用自己的表承担（如按写入时间），不进这个端口；
遮蔽同理在应用的消息表上做（``SHADOW_KINDS`` 只是共享的原因词汇），端口不再声明 ``shadow``。
回放重建与崩溃接管的读方修复在 :mod:`agstack.genai.harness.replay`。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from ..llm.client import UsageEvent, set_usage_callback


#: 日志事件类型
LOG_KINDS = frozenset(
    {"message", "event", "tool_call", "tool_result", "summary", "fold", "attempt", "system_snapshot", "phase_marker"}
)

#: 遮蔽原因
SHADOW_KINDS = frozenset({"retry", "overflow_recovery", "fold"})


@dataclass(slots=True)
class LogEvent:
    """会话日志的一条事件

    :param kind: 事件类型（:data:`LOG_KINDS`）
    :param role: 可投影为模型消息时的角色（system / user / assistant / tool / event），否则 None
    :param content: 正文（消息文本 / 工具结果 / 摘要）
    :param metadata: 结构化附带（tool_calls、tool_call_id、来源、锚点等）
    :param seq: 运行内单调序号，append 时由实现分配（读回时必有）
    :param shadowed_by: 遮蔽它的行的引用（应用自定：行 id / 序号的字符串；None 为有效行）
    """

    kind: str
    role: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None
    shadowed_by: str | None = None


@dataclass(frozen=True, slots=True)
class TokenAnchor:
    """上一次真实请求的 token 锚点：历史主体用锚点值，只对锚点之后的新增内容做估算

    :param anchor_ref: 锚点所在行的引用（应用自定，通常是历史末行的 id；3.1 起不再是 seq——锚点落在话题级消息上）
    """

    anchor_ref: str
    prompt_tokens: int
    model: str


@dataclass(frozen=True, slots=True)
class SpillOwner:
    """落盘内容的归属（权限与密级由实现按此判定）"""

    user_id: UUID | None
    session_id: str | None = None
    task_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SpillSource:
    """落盘内容的来源（哪个工具、哪次调用）"""

    tool: str
    call_id: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SpillRef:
    """落盘结果的引用：locator 对模型不透明，读取经实现的权限检查"""

    locator: str
    chars: int
    tokens: int
    name: str


@runtime_checkable
class SessionLog(Protocol):
    """运行日志端口（``session_id`` = 一次运行的任务 id）"""

    async def append(self, session_id: UUID, events: Sequence[LogEvent]) -> int:
        """追加事件，返回末 seq（实现分配序号；本进程未开始过该运行时须先从存储续上序号）"""
        ...

    async def read(self, session_id: UUID, *, after_seq: int = -1, limit: int | None = None) -> Sequence[LogEvent]:
        """按 seq 升序读取 ``seq > after_seq`` 的事件（缺省全部；含被遮蔽行，投影方自行过滤）"""
        ...

    async def latest_anchor(self, session_id: UUID) -> TokenAnchor | None:
        """该运行所属话题最近一次 token 锚点（无话题 / 无锚点为 None）"""
        ...


@runtime_checkable
class SpillStore(Protocol):
    """落盘端口"""

    async def save_text(
        self, owner: SpillOwner, source: SpillSource, suggested_name: str, content: str
    ) -> SpillRef: ...

    async def read_text(self, locator: str, *, offset: int = 0, limit: int | None = None) -> str: ...


#: 用量汇：与 client.set_usage_callback 同一回调类型，纳入同一注册面
UsageSink = Callable[[UsageEvent], None]


@runtime_checkable
class KVStore(Protocol):
    """可选 KV 端口（投影缓存、守卫状态）"""

    async def get(self, scope: str, key: str) -> Any | None: ...

    async def put(self, scope: str, key: str, value: Any) -> None: ...


@dataclass(slots=True)
class Ports:
    """已注册的端口集合"""

    session_log: SessionLog | None = None
    spill: SpillStore | None = None
    usage: UsageSink | None = None
    kv: KVStore | None = None


_PORTS = Ports()


def register_ports(
    *,
    session_log: SessionLog | None = None,
    spill: SpillStore | None = None,
    usage: UsageSink | None = None,
    kv: KVStore | None = None,
) -> None:
    """注册端口实现（进程级单例；只覆盖传入的项，传 None 的项保持不变）

    ``usage`` 同时写入 :func:`agstack.genai.llm.client.set_usage_callback`，两处始终一致。
    """
    if session_log is not None:
        _PORTS.session_log = session_log
    if spill is not None:
        _PORTS.spill = spill
    if usage is not None:
        _PORTS.usage = usage
        set_usage_callback(usage)
    if kv is not None:
        _PORTS.kv = kv


def get_ports() -> Ports:
    """当前端口集合（未注册的项为 None，消费方自行降级）"""
    return _PORTS


def clear_ports() -> None:
    """清空全部端口（测试隔离用），usage 回调同步注销"""
    _PORTS.session_log = None
    _PORTS.spill = None
    _PORTS.usage = None
    _PORTS.kv = None
    set_usage_callback(None)


__all__ = [
    "LOG_KINDS",
    "SHADOW_KINDS",
    "KVStore",
    "LogEvent",
    "Ports",
    "SessionLog",
    "SpillOwner",
    "SpillRef",
    "SpillSource",
    "SpillStore",
    "TokenAnchor",
    "UsageSink",
    "clear_ports",
    "get_ports",
    "register_ports",
]
