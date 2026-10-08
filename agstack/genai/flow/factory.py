#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""便捷工厂函数 - 快速创建组件并在失败时抛出异常

此模块提供了简洁的工厂函数，适用于：
- 确信组件已注册的场景
- 希望快速失败而不是处理 None 的场景
- 需要简洁 API 的场景

如果需要处理组件不存在的情况，请使用 registry.create_xxx() 方法。
"""

from .agent import Agent
from .registry import registry
from .tool import Tool


def create_tool(name: str, **kwargs) -> Tool:
    """创建工具实例（失败时抛出异常）

    Args:
        name: 工具名称
        **kwargs: 工具初始化参数

    Returns:
        Tool: 工具实例

    Raises:
        RuntimeError: 当工具未注册时

    Example:
        >>> tool = create_tool("web_search", api_key="xxx")
        >>> result = await tool.run(context)
    """
    tool = registry.create_tool(name, **kwargs)
    if not tool:
        raise RuntimeError(f"Tool '{name}' not registered")
    return tool


def create_agent(name: str, **kwargs) -> Agent:
    """创建 Agent 实例（失败时抛出异常）

    Args:
        name: Agent 名称
        **kwargs: Agent 初始化参数

    Returns:
        Agent: Agent 实例

    Raises:
        RuntimeError: 当 Agent 未注册时

    Example:
        >>> agent = create_agent("chat_agent", model="gpt-4")
        >>> response = await agent.run(context)
    """
    agent = registry.create_agent(name, **kwargs)
    if not agent:
        raise RuntimeError(f"Agent '{name}' not registered")
    return agent
