#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Agent 定义和执行"""

import json
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from ..client import get_llm_client
from .context import Usage
from .events import EventType
from .exceptions import FlowError


if TYPE_CHECKING:
    from .context import FlowContext
    from .tool import Tool


class Agent:
    """Agent 定义"""

    def __init__(
        self,
        name: str,
        instructions: str = "",
        tools: list["Tool"] | None = None,
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_turns: int = 10,
    ):
        """初始化 Agent

        :param name: Agent 名称
        :param instructions: 系统指令
        :param tools: 可用工具列表
        :param model: 模型名称
        :param temperature: 温度参数
        :param max_tokens: 最大 token 数
        :param max_turns: 最大轮次
        """
        self.name = name
        self.instructions = instructions or f"You are {name}, a helpful AI assistant."
        self.tools = tools or []
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_turns = max_turns

    def get_system_message(self) -> dict[str, Any]:
        """获取系统消息"""
        return {"role": "system", "content": self.instructions}

    def get_tools_schema(self) -> list[dict[str, Any]]:
        """获取工具 schema"""
        return [tool.to_openai_tool() for tool in self.tools]

    def get_tool_by_name(self, name: str) -> "Tool | None":
        """根据名称获取工具"""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    async def run(self, context: "FlowContext") -> str:
        """执行 Agent 逻辑"""
        content_parts = []
        async for event in self.stream(context):
            # AG-UI 事件格式
            if isinstance(event, dict):
                if event.get("type") == EventType.TEXT_MESSAGE_CONTENT:
                    content_parts.append(event.get("delta", ""))
                elif event.get("type") == EventType.RUN_ERROR:
                    raise FlowError("AGENT_EXECUTION_FAILED", 500, {"error": event.get("error")})
        return "".join(content_parts)

    async def stream(self, context: "FlowContext") -> AsyncIterator[dict[str, Any]]:
        """流式执行 Agent，输出 AG-UI 标准事件"""

        query = context.get_variable("query", "")
        message_id = str(uuid4())

        # 添加用户消息
        context.add_message("user", query)
        context.last_agent = self.name

        # AG-UI: TEXT_MESSAGE_START
        yield {"type": EventType.TEXT_MESSAGE_START, "messageId": message_id, "role": "assistant"}

        # 构建消息列表
        messages = [self.get_system_message()] + context.messages
        tools_schema = self.get_tools_schema() if self.tools else None

        # 获取 LLM 客户端
        client = get_llm_client()

        # Agent 循环
        for _ in range(self.max_turns):
            context.increment_turn()

            # 调用模型
            assistant_content = ""
            tool_calls: list[dict[str, Any]] = []
            tool_calls_buffer: dict[int, dict[str, Any]] = {}

            try:
                kwargs: dict[str, Any] = {
                    "messages": messages,
                    "model": self.model,
                    "temperature": self.temperature,
                }

                if self.max_tokens:
                    kwargs["max_tokens"] = self.max_tokens

                if tools_schema:
                    kwargs["tools"] = tools_schema
                    kwargs["tool_choice"] = "auto"

                stream = await client.chat(stream=True, **kwargs)

                async for chunk in stream:
                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]
                    delta = choice.delta

                    # 内容增量 - AG-UI: TEXT_MESSAGE_CONTENT
                    if delta.content:
                        assistant_content += delta.content
                        yield {
                            "type": EventType.TEXT_MESSAGE_CONTENT,
                            "messageId": message_id,
                            "delta": delta.content,
                        }

                    # 工具调用
                    if delta.tool_calls:
                        for tool_call_delta in delta.tool_calls:
                            idx = tool_call_delta.index  # noqa
                            if idx not in tool_calls_buffer:
                                tool_calls_buffer[idx] = {
                                    "id": tool_call_delta.id or "",  # noqa
                                    "name": "",
                                    "arguments": "",
                                }

                            if tool_call_delta.id:  # noqa
                                tool_calls_buffer[idx]["id"] = tool_call_delta.id  # noqa
                            if tool_call_delta.function and tool_call_delta.function.name:  # noqa
                                tool_calls_buffer[idx]["name"] = tool_call_delta.function.name  # noqa
                            if tool_call_delta.function and tool_call_delta.function.arguments:  # noqa
                                tool_calls_buffer[idx]["arguments"] += tool_call_delta.function.arguments  # noqa

                    # 完成
                    if choice.finish_reason:
                        # AG-UI: 工具调用事件
                        for tool_call_data in tool_calls_buffer.values():
                            tool_calls.append(tool_call_data)

                            # TOOL_CALL_START
                            yield {
                                "type": EventType.TOOL_CALL_START,
                                "toolCallId": tool_call_data["id"],
                                "toolCallName": tool_call_data["name"],
                            }

                            # TOOL_CALL_ARGS
                            yield {
                                "type": EventType.TOOL_CALL_ARGS,
                                "toolCallId": tool_call_data["id"],
                                "delta": tool_call_data["arguments"],
                            }

                            # TOOL_CALL_END
                            yield {"type": EventType.TOOL_CALL_END, "toolCallId": tool_call_data["id"]}

                        # 更新 usage
                        if hasattr(chunk, "usage") and chunk.usage:
                            context.add_usage(
                                Usage(
                                    prompt_tokens=chunk.usage.prompt_tokens or 0,
                                    completion_tokens=chunk.usage.completion_tokens or 0,
                                    total_tokens=chunk.usage.total_tokens or 0,
                                )
                            )

            except Exception as e:
                error_msg = str(e)
                # AG-UI: RUN_ERROR
                yield {"type": EventType.RUN_ERROR, "error": error_msg}
                raise FlowError("AGENT_EXECUTION_FAILED", 500, {"error": error_msg}) from e

            # 保存 assistant 消息
            if tool_calls:
                context.add_message(
                    "assistant",
                    content=assistant_content or None,
                    tool_calls=tool_calls,
                )
            else:
                context.add_message("assistant", assistant_content)

            # 如果没有工具调用，结束循环
            if not tool_calls:
                # AG-UI: TEXT_MESSAGE_END
                yield {"type": EventType.TEXT_MESSAGE_END, "messageId": message_id}
                return

            # 执行工具调用
            for tool_call in tool_calls:
                tool = self.get_tool_by_name(tool_call["name"])
                if not tool:
                    error_msg = f"Tool not found: {tool_call['name']}"
                    context.add_message(
                        "tool",
                        content=json.dumps({"error": error_msg}),
                        tool_call_id=tool_call["id"],
                    )
                    # AG-UI: TOOL_CALL_RESULT (错误)
                    yield {
                        "type": EventType.TOOL_CALL_RESULT,
                        "toolCallId": tool_call["id"],
                        "content": json.dumps({"error": error_msg}),
                    }
                    continue

                # 执行工具
                result = await tool.execute_async(context)

                # 保存工具结果
                result_content = json.dumps(result.result) if result.success else json.dumps({"error": result.error})
                context.add_message("tool", content=result_content, tool_call_id=tool_call["id"])

                # AG-UI: TOOL_CALL_RESULT
                yield {"type": EventType.TOOL_CALL_RESULT, "toolCallId": tool_call["id"], "content": result_content}

            # 更新消息列表，继续下一轮
            messages = [self.get_system_message()] + context.messages
