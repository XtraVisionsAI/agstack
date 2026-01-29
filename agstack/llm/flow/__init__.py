#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""统一的执行框架"""

# 导入示例实现
from .agent import Agent
from .context import FlowContext, Usage
from .events import Event, EventType
from .exceptions import (
    AgentError,
    FlowConfigError,
    FlowError,
    FlowExecutionError,
    ModelError,
    NodeExecutionError,
    ToolExecutionError,
)
from .factory import create_agent, create_tool
from .flow import Flow
from .loader import FlowLoader
from .records import Record, Status
from .registry import registry
from .state import FlowState
from .tool import Tool, ToolResult


__all__ = [
    # 核心抽象
    "Tool",
    "ToolResult",
    "Agent",
    "Flow",
    "FlowContext",
    "Usage",
    # AG-UI 协议
    "Event",
    "EventType",
    # 注册和工厂（registry 返回 None 失败，factory 函数抛出异常）
    "registry",
    "create_tool",
    "create_agent",
    # 状态管理
    "FlowState",
    "Record",
    "Status",
    # 配置加载
    "FlowLoader",
    # 异常
    "FlowError",
    "AgentError",
    "ToolExecutionError",
    "ModelError",
    "FlowConfigError",
    "FlowExecutionError",
    "NodeExecutionError",
]
