#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Flow 系统注册中心 — 自包含的精简组件注册与创建"""

from __future__ import annotations

import copy
from typing import Any, cast

from .agent import Agent
from .tool import Tool, ToolHook, clear_tool_hooks, register_tool_hook


class FlowRegistry:
    """Flow 系统统一注册中心"""

    def __init__(self):
        self._tools: dict[str, Any] = {}
        self._agents: dict[str, Any] = {}
        self._flows: dict[str, Any] = {}
        self._node_handlers: dict[str, Any] = {}
        self._builtins_loaded = False
        self._tool_labels: dict[str, str] = {}
        self._tool_echo: dict[str, bool] = {}
        self._agent_labels: dict[str, str] = {}
        self._agent_echo: dict[str, bool] = {}

    def _ensure_builtins(self) -> None:
        """延迟加载内置 node handlers（避免循环导入）"""
        if self._builtins_loaded:
            return
        self._builtins_loaded = True
        from .nodes import builtin_handlers

        for handler in builtin_handlers:
            self._node_handlers.setdefault(handler.node_type, handler)

    # ── 注册 ──

    def register_tool(self, name: str, tool_class, *, label: str | None = None, echo: bool = False) -> None:
        """注册工具工厂/类/实例"""
        self._tools[name] = tool_class
        if label is not None:
            self._tool_labels[name] = label
        if echo:
            self._tool_echo[name] = True

    def register_tool_hook(self, hook: ToolHook, *, prepend: bool = False) -> None:
        """注册全局工具执行钩子，对所有 Tool.execute_async 生效

        pre_execute 按注册顺序、post_execute 按逆序执行；
        prepend=True 抢占链头（其 post_execute 成为最外层，适合 spill/截断类钩子）。
        钩子链存于 tool 模块（保持 registry→tool 单向导入），此处仅转发注册。
        """
        register_tool_hook(hook, prepend=prepend)

    def clear_tool_hooks(self) -> None:
        """清空全局工具钩子（测试隔离用）"""
        clear_tool_hooks()

    def register_agent(
        self, name: str, agent_class: type[Agent], *, label: str | None = None, echo: bool = False
    ) -> None:
        """注册 Agent 类型"""
        self._agents[name] = agent_class
        if label is not None:
            self._agent_labels[name] = label
        if echo:
            self._agent_echo[name] = True

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
        """创建 Agent 实例

        kwargs 中的参数会覆盖 agent 默认值（如 max_turns, model 等）。
        """
        component = self._agents.get(name)
        if component is None:
            return None
        if callable(component):
            return cast(Agent, component(**kwargs))
        agent = cast(Agent, copy.copy(component))
        for attr, value in kwargs.items():
            setattr(agent, attr, value)
        return agent

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

    def get_tool_label(self, name: str) -> str | None:
        """获取工具的展示名称"""
        return self._tool_labels.get(name)

    def get_tool_echo(self, name: str) -> bool:
        """获取工具是否转发 TEXT_MESSAGE（label 隐含 echo）"""
        return name in self._tool_labels or self._tool_echo.get(name, False)

    def get_agent_label(self, name: str) -> str | None:
        """获取 Agent 的展示名称"""
        return self._agent_labels.get(name)

    def get_agent_echo(self, name: str) -> bool:
        """获取 Agent 是否转发 TEXT_MESSAGE（label 隐含 echo）"""
        return name in self._agent_labels or self._agent_echo.get(name, False)

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
