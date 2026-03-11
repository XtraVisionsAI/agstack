#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Detect 节点 — 分类/检测，输出路由键"""

import json as _json
from typing import TYPE_CHECKING, Any

from openai.types.chat import ChatCompletionMessageParam

from ...client import get_llm_client
from ..context import Usage
from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class DetectNodeHandler(NodeHandler):
    """分类/检测节点

    对输入文本进行分类，输出路由键。结果直接用于 _extract_route_key() 路由。

    输入：query（待检测文本）+ instruction + options
    输出：{"result": "<option>"} 的 JSON 字符串
    """

    node_type = "detect"

    def _build_classification_prompt(
        self, instruction: str, options: list[str], query: str
    ) -> list[ChatCompletionMessageParam]:
        options_text = "\n".join(f"- {opt}" for opt in options)
        system = (
            "You are a precise classifier. "
            "Given the user's input and instruction, classify it into exactly one of the provided options. "
            'Respond with ONLY a JSON object: {"result": "<option>"}. '
            "Do not include any other text."
        )
        user = f"Instruction: {instruction}\n\nOptions:\n{options_text}\n\nInput: {query}"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        resolved_inputs = self.resolve_inputs(config, context)

        query = resolved_inputs.get("query", "")
        instruction = config.get("instruction", "Classify the input")
        options = config.get("options", [])
        model = config.get("model", "gpt-4o-mini")
        temperature = config.get("temperature", 0.0)

        messages = self._build_classification_prompt(instruction, options, query)

        client = get_llm_client()
        response = await client.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            stream=False,
        )

        result_text = ""
        if response.choices:
            result_text = response.choices[0].message.content or ""

        if response.usage:
            context.add_usage(
                Usage(
                    prompt_tokens=response.usage.prompt_tokens or 0,
                    completion_tokens=response.usage.completion_tokens or 0,
                    total_tokens=response.usage.total_tokens or 0,
                )
            )

        # 尝试解析为 JSON，确保返回 {"result": "<option>"} 格式
        try:
            parsed = _json.loads(result_text)
            if isinstance(parsed, dict) and "result" in parsed:
                return _json.dumps(parsed, ensure_ascii=False)
        except (ValueError, TypeError):
            pass

        # 如果 LLM 返回的是纯文本选项，包装为标准格式
        stripped = result_text.strip()
        if stripped in options:
            return _json.dumps({"result": stripped}, ensure_ascii=False)

        # 兜底：返回原始文本
        return _json.dumps({"result": stripped}, ensure_ascii=False)
