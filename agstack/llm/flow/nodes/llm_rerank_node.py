#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""LLM Rerank 节点 — 文档重排序"""

from typing import TYPE_CHECKING, Any

from ...client import get_llm_client
from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class LLMRerankNodeHandler(NodeHandler):
    """文档重排序节点

    输入：query（字符串）+ documents（字符串列表）
    输出：{"results": [{"index": 0, "score": 0.95, "text": "..."}, ...]}
    """

    node_type = "llm_rerank"

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        resolved_inputs = self.resolve_inputs(config, context)

        query = resolved_inputs.get("query", "")
        documents = resolved_inputs.get("documents", [])
        if isinstance(documents, str):
            documents = [documents]

        model = config.get("model", "bge-reranker-v2-m3")
        top_n = config.get("top_n", 10)

        client = get_llm_client()
        raw_results = await client.rerank(
            query=query,
            documents=documents,
            model=model,
            top_n=top_n,
        )

        # raw_results: list[tuple[int, float, str]]
        results = [{"index": idx, "score": score, "text": text} for idx, score, text in raw_results]

        result = {"results": results}
        return result
