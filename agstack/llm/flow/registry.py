#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Flow 系统注册中心 — 自包含的精简组件注册与创建"""

from __future__ import annotations

from typing import Any, cast

from .agent import Agent
from .tool import Tool


class FlowRegistry:
    """Flow 系统统一注册中心"""

    def __init__(self):
        self._tools: dict[str, Any] = {}
        self._agents: dict[str, Any] = {}
        self._flows: dict[str, Any] = {}
        self._node_handlers: dict[str, Any] = {}
        self._builtins_loaded = False

    def _ensure_builtins(self) -> None:
        """延迟加载内置 node handlers（避免循环导入）"""
        if self._builtins_loaded:
            return
        self._builtins_loaded = True
        from .nodes import builtin_handlers

        for handler in builtin_handlers:
            self._node_handlers.setdefault(handler.node_type, handler)

    # ── 注册 ──

    def register_tool(self, name: str, tool_class) -> None:
        """注册工具工厂/类/实例"""
        self._tools[name] = tool_class

    def register_agent(self, name: str, agent_class: type[Agent]) -> None:
        """注册 Agent 类型"""
        self._agents[name] = agent_class

    def register_flow(self, name: str, flow_class: type) -> None:
        """注册 Flow 类型"""
        self._flows[name] = flow_class

    def register_node_handler(self, node_type: str, handler) -> None:
        """注册自定义节点处理器（所有 Flow 实例共享）"""
        self._node_handlers[node_type] = handler

    # ── 创建实例 ──

    def create_tool(self, name: str, **kwargs) -> Tool | None:
        """创建工具实例"""
        component = self._tools.get(name)
        if component is None:
            return None
        # 已实例化的 Tool 对象直接返回
        if hasattr(component, "execute_async"):
            return cast(Tool, component)
        # 类或工厂函数：实例化
        if callable(component):
            return cast(Tool, component(**kwargs) if kwargs else component())
        return cast(Tool, component)

    def create_agent(self, name: str, **kwargs) -> Agent | None:
        """创建 Agent 实例"""
        component = self._agents.get(name)
        if component is None:
            return None
        if callable(component):
            return cast(Agent, component(**kwargs))
        return cast(Agent, component)

    def create_flow(self, name: str, **kwargs) -> Any | None:
        """创建 Flow 实例"""
        component = self._flows.get(name)
        if component is None:
            return None
        if callable(component):
            return component(**kwargs)
        return component

    def create_tools(self, names: list[str]) -> list[Tool]:
        """批量创建工具"""
        return [tool for name in names if (tool := self.create_tool(name))]

    # ── 查询 ──

    def get_tool_class(self, name: str) -> Any | None:
        """获取工具工厂/类"""
        return self._tools.get(name)

    def get_agent_class(self, name: str) -> type[Agent] | None:
        """获取 Agent 类型"""
        return self._agents.get(name)

    def get_flow_class(self, name: str) -> type | None:
        """获取 Flow 类型"""
        return self._flows.get(name)

    def get_node_handler(self, node_type: str):
        """获取节点处理器"""
        self._ensure_builtins()
        return self._node_handlers.get(node_type)

    def get_all_node_handlers(self) -> dict[str, Any]:
        """获取所有节点处理器（内置 + 自定义）"""
        self._ensure_builtins()
        return dict(self._node_handlers)

    # ── 列表 ──

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def list_agents(self) -> list[str]:
        return list(self._agents.keys())

    def list_flows(self) -> list[str]:
        return list(self._flows.keys())

    def get_all_info(self) -> dict[str, list[str]]:
        """获取所有组件信息"""
        self._ensure_builtins()
        info: dict[str, list[str]] = {}
        if self._tools:
            info["tool"] = self.list_tools()
        if self._agents:
            info["agent"] = self.list_agents()
        if self._flows:
            info["flow"] = self.list_flows()
        if self._node_handlers:
            info["node_handler"] = list(self._node_handlers.keys())
        return info


# Flow 系统专用实例
registry = FlowRegistry()
