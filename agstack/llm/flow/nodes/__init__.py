#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""内置节点处理器注册"""

from .agent_node import AgentNodeHandler
from .base import NodeHandler
from .detect_node import DetectNodeHandler
from .llm_chat_node import LLMChatNodeHandler
from .llm_embed_node import LLMEmbedNodeHandler
from .llm_rerank_node import LLMRerankNodeHandler
from .python_node import PythonNodeHandler
from .tool_node import ToolNodeHandler


# 所有内置 handler 实例
builtin_handlers: list[NodeHandler] = [
    AgentNodeHandler(),
    ToolNodeHandler(),
    PythonNodeHandler(),
    LLMChatNodeHandler(),
    LLMEmbedNodeHandler(),
    LLMRerankNodeHandler(),
    DetectNodeHandler(),
]

# 全局自定义节点注册
_global_node_handlers: dict[str, NodeHandler] = {}


def register_node_handler(node_type: str, handler: NodeHandler) -> None:
    """注册自定义节点处理器（全局，所有 Flow 实例共享）"""
    _global_node_handlers[node_type] = handler


__all__ = [
    "NodeHandler",
    "builtin_handlers",
    "register_node_handler",
]
