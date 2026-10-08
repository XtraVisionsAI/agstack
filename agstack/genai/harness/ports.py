#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""存储端口（SPI）：库与应用之间的分层缝，唯一实现、由应用在进程入口注册

agstack 只声明 Protocol 与数据类型，不含任何存储实现；应用（如 MetaMatrix 的 ``common.harness``）用自己的表 /
文件系统实现后经 :func:`register_ports` 注入。端口刻意少：会话日志（追加 / 读取 / 遮蔽 / 锚点）、落盘（spill）、
用量汇、可选 KV。

``LogEvent`` 借「仅追加日志 + 投影」思路但不做事件溯源：``kind`` 取值见 :data:`LOG_KINDS`；被重试 / 溢出恢复 / 折叠
取代的行不删，由 ``shadowed_by`` 指向取代它的事件序号，投影只消费 ``shadowed_by is None`` 的行。
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
    :param seq: 会话内单调序号，append 时由实现分配
    :param shadowed_by: 遮蔽它的事件序号（None 为有效行）
    """

    kind: str
    role: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None
    shadowed_by: int | None = None


@dataclass(frozen=True, slots=True)
class TokenAnchor:
    """上一次真实请求的 token 锚点：历史主体用锚点值，只对锚点之后的新增内容做估算"""

    seq: int
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
    """会话日志端口"""

    async def append(self, session_id: UUID, events: Sequence[LogEvent]) -> int:
        """追加事件，返回末 seq"""
        ...

    async def read(self, session_id: UUID, *, after_seq: int = 0, limit: int | None = None) -> Sequence[LogEvent]:
        """按 seq 升序读取（含被遮蔽行，投影方自行过滤）"""
        ...

    async def shadow(self, session_id: UUID, target_seqs: Sequence[int], by_seq: int, kind: str) -> None:
        """把 target_seqs 标为被 by_seq 遮蔽；kind ∈ SHADOW_KINDS"""
        ...

    async def latest_anchor(self, session_id: UUID) -> TokenAnchor | None:
        """最近一次 token 锚点"""
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
