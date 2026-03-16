#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""统一的执行框架"""

from . import event
from .agent import Agent
from .context import FlowContext, Usage
from .event import EventType
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
from .nodes import NodeHandler
from .records import Record, Status
from .registry import registry
from .state import FlowState
from .tool import Tool, ToolResult


register_node_handler = registry.register_node_handler

__all__ = [
    # 核心抽象
    "Tool",
    "ToolResult",
    "Agent",
    "Flow",
    "FlowContext",
    "Usage",
    # 节点处理器
    "NodeHandler",
    "register_node_handler",
    # AG-UI 协议
    "EventType",
    "event",
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
