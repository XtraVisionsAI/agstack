#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""LLM Embed 节点 — 文本向量化"""

from typing import TYPE_CHECKING, Any

from ...client import get_llm_client
from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class LLMEmbedNodeHandler(NodeHandler):
    """文本向量化节点

    输入：texts（字符串列表或单个字符串）
    输出：{"embeddings": [[0.1, 0.2, ...], ...]}
    """

    node_type = "llm_embed"

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        resolved_inputs = self.resolve_inputs(config, context)

        texts = resolved_inputs.get("texts", [])
        if isinstance(texts, str):
            texts = [texts]

        model = resolved_inputs.get("model") or config.get("model", "bge-m3")

        client = get_llm_client()
        embeddings = await client.embed(texts=texts, model=model)

        result = {"embeddings": embeddings}
        return result
