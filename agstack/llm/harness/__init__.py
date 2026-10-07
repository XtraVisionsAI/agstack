#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""agstack.llm.harness——模型无关、表无关、产品无关的运行时部件

- :mod:`.ports`：存储端口声明（SessionLog / SpillStore / UsageSink / KVStore）与 ``register_ports``，
  由应用实现并在进程入口注册；
- :mod:`.truncation`：工具结果截断设施（保头尾截断、按相关度整条丢弃），策略数值由调用方给；
- :mod:`.spill`：超长工具结果落盘的 ToolHook（prepend 链头、按内联 token 上限判定、头尾保留 + 固定格式通知、
  存储失败保留内联）。

2.3 只收这三块零状态模块与端口声明；events / projection / tokens / AgentGuards 排 2.4，context / overflow 排 3.0。
"""

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
from .spill import SpillHook, SpillPolicy
from .truncation import clamp_results, truncate_middle


__all__ = [
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
