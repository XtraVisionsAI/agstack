#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""LLM Chat 节点 — 单轮 LLM 调用（支持流式/非流式）"""

import json as _json
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from openai.types.chat import ChatCompletionMessageParam

from ...client import get_llm_client
from .. import event
from ..context import Usage
from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class _SafeFormatDict(dict):
    """安全的模板变量替换，缺失 key 时保留原始占位符"""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


class LLMChatNodeHandler(NodeHandler):
    """单轮 LLM 对话节点

    与 agent 的区别：
    - agent = 多轮对话 + tool use 循环 + 消息历史隔离
    - llm_chat = 单轮 prompt → response，无状态，更轻量
    """

    node_type = "llm_chat"

    def _build_prompt(self, template: str, resolved_inputs: dict[str, Any]) -> str:
        """将模板中的 {var} 占位符替换为 resolved 的输入值"""
        format_dict = _SafeFormatDict(
            {k: v if isinstance(v, str) else _json.dumps(v, ensure_ascii=False) for k, v in resolved_inputs.items()}
        )
        return template.format_map(format_dict)

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        resolved_inputs = self.resolve_inputs(config, context)
        prompt_text = self._build_prompt(config.get("prompt", ""), resolved_inputs)

        model = resolved_inputs.get("model") or config.get("model", "gpt-4o")
        _temp = resolved_inputs.get("temperature")
        temperature: float = float(_temp) if _temp is not None else float(config.get("temperature", 0.7))
        max_tokens = resolved_inputs.get("max_tokens") or config.get("max_tokens")

        client = get_llm_client()
        messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": prompt_text}]

        # 如果有 system_prompt，支持变量替换并放在前面
        system_prompt = config.get("system_prompt")
        if system_prompt:
            system_text = self._build_prompt(system_prompt, resolved_inputs)
            messages.insert(0, {"role": "system", "content": system_text})

        response = await client.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
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

        return {"result": result_text}

    async def stream(self, node: dict, context: "FlowContext", node_id: str) -> AsyncIterator[dict[str, Any]]:
        config = node.get("config", {})
        use_stream = config.get("stream", False)

        if not use_stream:
            # 非流式：走默认 execute 路径
            step_name = self.get_step_name(node, node_id)
            yield event.step_started(step_name=step_name)
            result = await self.execute(node, context)
            context.set_output(node_id, result)
            yield event.step_finished(step_name=step_name)
            return

        # 流式输出
        step_name = self.get_step_name(node, node_id)
        yield event.step_started(step_name=step_name)

        resolved_inputs = self.resolve_inputs(config, context)
        prompt_text = self._build_prompt(config.get("prompt", ""), resolved_inputs)

        model = resolved_inputs.get("model") or config.get("model", "gpt-4o")
        _temp = resolved_inputs.get("temperature")
        temperature: float = float(_temp) if _temp is not None else float(config.get("temperature", 0.7))
        max_tokens = resolved_inputs.get("max_tokens") or config.get("max_tokens")

        client = get_llm_client()
        messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": prompt_text}]

        system_prompt = config.get("system_prompt")
        if system_prompt:
            system_text = self._build_prompt(system_prompt, resolved_inputs)
            messages.insert(0, {"role": "system", "content": system_text})

        msg_id = context.message_id or str(uuid4())
        yield event.text_message_start(message_id=msg_id, role="assistant")

        content_parts: list[str] = []
        stream_iter = await client.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        async for chunk in stream_iter:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                content_parts.append(delta.content)
                yield event.text_message_content(message_id=msg_id, delta=delta.content)

            if chunk.choices[0].finish_reason and chunk.usage:
                context.add_usage(
                    Usage(
                        prompt_tokens=chunk.usage.prompt_tokens or 0,
                        completion_tokens=chunk.usage.completion_tokens or 0,
                        total_tokens=chunk.usage.total_tokens or 0,
                    )
                )

        yield event.text_message_end(message_id=msg_id)

        result_text = "".join(content_parts)
        context.set_output(node_id, {"result": result_text})

        yield event.step_finished(step_name=step_name)
