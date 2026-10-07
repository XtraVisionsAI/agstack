#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""agstack.llm.harness——模型无关、表无关、产品无关的运行时部件

- :mod:`.ports`：存储端口声明（SessionLog / SpillStore / UsageSink / KVStore）与 ``register_ports``，
  由应用实现并在进程入口注册；
- :mod:`.truncation`：工具结果截断设施（保头尾截断、按相关度整条丢弃），策略数值由调用方给；
- :mod:`.spill`：超长工具结果落盘的 ToolHook（prepend 链头、按内联 token 上限判定、头尾保留 + 固定格式通知、
  存储失败保留内联）；
- :mod:`.events`（2.4）：任务事件枢纽 EventHub（序号 / 快照 / 订阅 / 重放，持久化由应用注入）与「flow 事件 → 用户可见
  事件」过滤规则表；
- :mod:`.projection`（2.4）：「日志 → 模型历史」投影引擎（遮蔽过滤 / 事件投影 / 正文改写 / 注记 / 同角色合并，
  规则由应用给）；
- :mod:`.tokens`（2.4）：估算校准（实报 / 估算钳制）与锚点增量计量。

Agent 守卫机制（AgentGuards）在 :mod:`agstack.llm.flow.guards`。context / overflow 排 3.0。
"""

from .events import EventHub, TaskSnapshot, filter_user_event
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
from .spill import SpillHook, SpillPolicy
from .tokens import CalibratedCounter, anchored_estimate, calibration_from_samples, calibration_sample, clamp_ratio
from .truncation import clamp_results, truncate_middle


__all__ = [
    "CalibratedCounter",
    "EventHub",
    "Projection",
    "TaskSnapshot",
    "anchored_estimate",
    "calibration_from_samples",
    "calibration_sample",
    "clamp_ratio",
    "filter_user_event",
    "is_shadowed",
    "merge_consecutive",
    "select_recent",
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
