#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""内置节点处理器注册"""

from .agent_node import AgentNodeHandler
from .base import NodeHandler
from .detect_node import DetectNodeHandler
from .llm_chat_node import LLMChatNodeHandler
from .llm_embed_node import LLMEmbedNodeHandler
from .llm_rerank_node import LLMRerankNodeHandler
from .python_node import PythonNodeHandler
from .subflow_node import SubflowNodeHandler
from .switch_node import SwitchNodeHandler
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
    SwitchNodeHandler(),
    SubflowNodeHandler(),
]

__all__ = [
    "NodeHandler",
    "builtin_handlers",
]
