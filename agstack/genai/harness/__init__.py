#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""agstack.genai.harness——模型无关、表无关、产品无关的运行时部件

- :mod:`.ports`：存储端口声明（SessionLog / SpillStore / UsageSink / KVStore）与 ``register_ports``，
  由应用实现并在进程入口注册；
- :mod:`.truncation`：工具结果截断设施（保头尾截断、按相关度整条丢弃），策略数值由调用方给；
- :mod:`.spill`：超长工具结果落盘的 ToolHook（prepend 链头、按内联 token 上限判定、头尾保留 + 固定格式通知、
  存储失败保留内联）；
- :mod:`.events`（2.4）：任务事件枢纽 EventHub（序号 / 快照 / 订阅 / 重放，持久化由应用注入）与「flow 事件 → 用户可见
  事件」过滤规则表；
- :mod:`.projection`（2.4）：「日志 → 模型历史」投影引擎（遮蔽过滤 / 事件投影 / 正文改写 / 注记 / 同角色合并，
  规则由应用给）；
- :mod:`.metering`（2.4，原 tokens）：估算校准（实报 / 估算钳制）与锚点增量计量；3.2 加模型级 ``ModelCalibration``；
- :mod:`.context`（3.0）：对话上下文压缩引擎 ContextEngine（预算分层 / 快速路径 / 锚点计量 / 摘要消费与后台刷新 /
  两遍装填），历史来源与摘要存取经 HistorySource / SummaryStore 由应用实现；
- :mod:`.overflow`（3.0）：上下文溢出三态判定与 ``OverflowPolicy``（``Agent.stream`` 命中后调应用的压缩协程并
  同轮重发一次）；
- :mod:`.replay`（3.1）：日志回放重建与崩溃接管的读方修复——阶段标记 / 失败尝试事件、``recover`` 阅读一次运行的
  日志、``closers`` 合成闭合事件；``SessionLog`` 的 session 自 3.1 起定为一次运行（任务），seq 即 EventHub 序号。

LLM 调用钩子 :mod:`agstack.genai.llm.hooks`（F6）留在 llm 包——它织入 LLMClient，client 不能反向依赖 harness——
这里重导出。Agent 守卫机制（AgentGuards）在 :mod:`agstack.genai.flow.guards`。
3.0 起 llm / flow / harness 收为 ``agstack.genai`` 三层，``agstack.genai.llm`` 只留模型接入（client / hooks / prompts /
token）；2.x 的 ``agstack.llm`` 系路径不再保留（破坏性变更，随 major）。
"""

from ..llm.hooks import CallMeta, LLMCallHook, StreamSummary, clear_llm_hooks, register_llm_hook
from .context import (
    ContextEngine,
    ContextPolicy,
    HistorySource,
    SummaryRecord,
    SummaryStore,
    anchored_history_tokens,
    fill_to_budget,
    messages_tokens,
    schedule_once,
)
from .events import EventHub, TaskSnapshot, filter_user_event
from .metering import (
    APPROX_RATIO_BOUNDS,
    EXACT_RATIO_BOUNDS,
    CalibratedCounter,
    ModelCalibration,
    anchored_estimate,
    bounds_for,
    calibration_from_pair,
    calibration_from_samples,
    calibration_sample,
    clamp_ratio,
    update_calibration,
)
from .overflow import (
    OVERFLOW_ERROR,
    OVERFLOW_LENGTH,
    OVERFLOW_RECORD,
    OVERFLOW_SILENT,
    OverflowPolicy,
    classify_overflow,
    usage_tokens,
)
from .ports import (
    KVStore,
    LogEvent,
    Ports,
    SessionLog,
    SpillOwner,
    SpillRef,
    SpillSource,
    SpillStore,
    TokenAnchor,
    UsageSink,
    clear_ports,
    get_ports,
    register_ports,
)
from .projection import Projection, is_shadowed, merge_consecutive, select_recent
from .replay import (
    ORPHAN_STARTED,
    ORPHAN_UNRECORDED,
    REASON_INTERRUPTED,
    OrphanCall,
    RecoveryState,
    attempt,
    closers,
    is_synthetic,
    phase_marker,
    recover,
    tool_call,
    tool_result,
)
from .spill import SpillHook, SpillPolicy
from .truncation import clamp_results, truncate_middle


__all__ = [
    "ContextEngine",
    "ContextPolicy",
    "HistorySource",
    "SummaryRecord",
    "SummaryStore",
    "anchored_history_tokens",
    "fill_to_budget",
    "messages_tokens",
    "schedule_once",
    "CallMeta",
    "LLMCallHook",
    "StreamSummary",
    "clear_llm_hooks",
    "register_llm_hook",
    "OVERFLOW_ERROR",
    "OVERFLOW_LENGTH",
    "OVERFLOW_RECORD",
    "OVERFLOW_SILENT",
    "APPROX_RATIO_BOUNDS",
    "EXACT_RATIO_BOUNDS",
    "CalibratedCounter",
    "ModelCalibration",
    "EventHub",
    "OverflowPolicy",
    "Projection",
    "classify_overflow",
    "usage_tokens",
    "TaskSnapshot",
    "anchored_estimate",
    "bounds_for",
    "calibration_from_pair",
    "calibration_from_samples",
    "calibration_sample",
    "update_calibration",
    "clamp_ratio",
    "filter_user_event",
    "is_shadowed",
    "merge_consecutive",
    "select_recent",
    "ORPHAN_STARTED",
    "ORPHAN_UNRECORDED",
    "REASON_INTERRUPTED",
    "OrphanCall",
    "RecoveryState",
    "attempt",
    "closers",
    "is_synthetic",
    "phase_marker",
    "recover",
    "tool_call",
    "tool_result",
    "KVStore",
    "LogEvent",
    "Ports",
    "SessionLog",
    "SpillHook",
    "SpillOwner",
    "SpillPolicy",
    "SpillRef",
    "SpillSource",
    "SpillStore",
    "TokenAnchor",
    "UsageSink",
    "clamp_results",
    "clear_ports",
    "get_ports",
    "register_ports",
    "truncate_middle",
]
