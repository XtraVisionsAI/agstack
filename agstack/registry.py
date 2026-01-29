#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""全局统一注册中心"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComponentManifest:
    """组件清单"""

    name: str  # 组件唯一标识
    type: str  # 组件类型 (tool/agent/flow/router/preprocessor)
    component: Any  # 实际组件对象
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展元数据
    dependencies: list[str] = field(default_factory=list)  # 依赖关系
    version: str = "1.0.0"  # 版本信息


class Registry:
    """统一注册中心"""

    def __init__(self):
        self._components: dict[str, dict[str, ComponentManifest]] = defaultdict(dict)

    # 底层通用接口
    def register(self, manifest: ComponentManifest) -> None:
        """注册组件"""
        self._components[manifest.type][manifest.name] = manifest

    def get(self, component_type: str, name: str) -> ComponentManifest | None:
        """获取组件清单"""
        return self._components[component_type].get(name)

    def list(self, component_type: str) -> list[str]:
        """列出指定类型的所有组件名称"""
        return list(self._components[component_type].keys())

    # 快捷注册接口
    def register_tool(
        self,
        name: str,
        tool_class: Any,
        metadata: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,  # noqa
        version: str = "1.0.0",
    ) -> None:
        """注册工具"""
        manifest = ComponentManifest(
            name=name,
            type="tool",
            component=tool_class,
            metadata=metadata or {},
            dependencies=dependencies or [],
            version=version,
        )
        self.register(manifest)

    def register_agent(
        self,
        name: str,
        agent_class: Any,
        metadata: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,  # noqa
        version: str = "1.0.0",
    ) -> None:
        """注册代理"""
        manifest = ComponentManifest(
            name=name,
            type="agent",
            component=agent_class,
            metadata=metadata or {},
            dependencies=dependencies or [],
            version=version,
        )
        self.register(manifest)

    def register_flow(
        self,
        name: str,
        flow_class: Any,
        metadata: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,  # noqa
        version: str = "1.0.0",
    ) -> None:
        """注册工作流"""
        manifest = ComponentManifest(
            name=name,
            type="flow",
            component=flow_class,
            metadata=metadata or {},
            dependencies=dependencies or [],
            version=version,
        )
        self.register(manifest)

    def register_router(
        self,
        name: str,
        router: Any,
        metadata: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,  # noqa
        version: str = "1.0.0",
    ) -> None:
        """注册路由"""
        manifest = ComponentManifest(
            name=name,
            type="router",
            component=router,
            metadata=metadata or {},
            dependencies=dependencies or [],
            version=version,
        )
        self.register(manifest)

    def register_preprocessor(
        self,
        name: str,
        processor_class: Any,
        metadata: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,  # noqa
        version: str = "1.0.0",
    ) -> None:
        """注册预处理器"""
        manifest = ComponentManifest(
            name=name,
            type="preprocessor",
            component=processor_class,
            metadata=metadata or {},
            dependencies=dependencies or [],
            version=version,
        )
        self.register(manifest)

    # 创建实例接口
    def create_tool(self, name: str, **kwargs) -> Any | None:
        """创建工具实例"""
        manifest = self.get("tool", name)
        if not manifest:
            return None

        component = manifest.component
        if callable(component):
            return component(**kwargs) if kwargs else component()
        return component

    def create_agent(self, name: str, **kwargs) -> Any | None:
        """创建代理实例"""
        manifest = self.get("agent", name)
        if not manifest:
            return None

        component = manifest.component
        if callable(component):
            return component(**kwargs)
        return component

    def create_flow(self, name: str, **kwargs) -> Any | None:
        """创建工作流实例"""
        manifest = self.get("flow", name)
        if not manifest:
            return None

        component = manifest.component
        if callable(component):
            return component(**kwargs)
        return component

    def create_tools(self, names: list[str]) -> list[Any]:  # noqa
        """批量创建工具"""
        return [tool for name in names if (tool := self.create_tool(name))]

    def get_all_info(self) -> dict[str, list[str]]:  # noqa
        """获取所有组件信息"""
        return {component_type: list(components.keys()) for component_type, components in self._components.items()}


# 全局注册中心实例
registry = Registry()
