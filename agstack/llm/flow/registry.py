#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Flow 系统注册中心 - 提供类型安全的组件注册和创建接口

此模块是对全局注册中心的类型安全封装，为 Flow 系统提供：
- 强类型的返回值（Tool、Agent、Flow 等）
- Flow 系统的统一命名空间
- 方便的批量操作和查询方法

推荐在 Flow 模块内部使用此 registry 实例。
"""

from ...registry import registry as global_registry
from .agent import Agent
from .tool import Tool


class FlowRegistry:
    """统一注册中心 - Flow 系统适配器"""

    def __init__(self):
        self.global_registry = global_registry

    def register_tool(self, name: str, tool_factory) -> None:
        """注册工具工厂"""
        self.global_registry.register_tool(name, tool_factory)

    def register_agent(self, name: str, agent_class: type[Agent]) -> None:
        """注册 Agent 类型"""
        self.global_registry.register_agent(name, agent_class)

    def register_flow(self, name: str, flow_class: type) -> None:
        """注册 Flow 类型"""
        self.global_registry.register_flow(name, flow_class)

    def create_tool(self, name: str) -> Tool | None:
        """创建工具实例"""
        return self.global_registry.create_tool(name)

    def create_agent(self, name: str, **kwargs) -> Agent | None:
        """创建 Agent 实例"""
        return self.global_registry.create_agent(name, **kwargs)

    def create_flow(self, name: str, **kwargs):
        """创建 Flow 实例"""
        return self.global_registry.create_flow(name, **kwargs)

    def create_tools(self, names: list[str]) -> list[Tool]:
        """批量创建工具"""
        return self.global_registry.create_tools(names)

    def get_tool_class(self, name: str):
        """获取工具工厂（兼容性）"""
        manifest = self.global_registry.get("tool", name)
        return manifest.component if manifest else None

    def get_agent_class(self, name: str) -> type[Agent] | None:
        """获取 Agent 类型"""
        manifest = self.global_registry.get("agent", name)
        return manifest.component if manifest else None

    def get_flow_class(self, name: str) -> type | None:
        """获取 Flow 类型"""
        manifest = self.global_registry.get("flow", name)
        return manifest.component if manifest else None

    def list_tools(self) -> list[str]:
        """列出所有工具"""
        return self.global_registry.list("tool")

    def list_agents(self) -> list[str]:
        """列出所有 Agent"""
        return self.global_registry.list("agent")

    def list_flows(self) -> list[str]:
        """列出所有 Flow"""
        return self.global_registry.list("flow")


# Flow 系统专用实例
registry = FlowRegistry()
