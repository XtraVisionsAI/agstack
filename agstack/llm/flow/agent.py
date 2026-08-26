#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Agent 定义和执行"""

import asyncio
import json
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from ..client import get_llm_client
from . import event
from .context import Usage
from .event import EventType
from .exceptions import AgentError, FlowError


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
        *,
        tool_choice: str = "auto",
        on_max_turns: str = "finalize",
        label: str | None = None,
        echo: bool = False,
    ):
        """初始化 Agent

        :param name: Agent 名称
        :param instructions: 系统指令
        :param tools: 可用工具列表
        :param model: 模型名称
        :param temperature: 温度参数
        :param max_tokens: 最大 token 数
        :param max_turns: 最大轮次
        :param on_max_turns: max_turns 耗尽时的行为，"finalize"（降级输出并标记 truncated）或 "error"（抛出异常）
        :param label: 面向用户的展示名称（控制 STEP 进度事件可见性）
        :param echo: 是否转发 TEXT_MESSAGE 给用户
        """
        self.name = name
        self.instructions = instructions or f"You are {name}, a helpful AI assistant."
        self.tools = tools or []
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_turns = max_turns
        self.tool_choice = tool_choice
        self.on_max_turns = on_max_turns
        self.label = label
        self.echo = echo

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

    def _group_tool_calls(self, tool_calls: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """按声明分组：连续的 concurrency_safe 调用聚为一组并发执行，其余单独成组串行

        未注册/未声明的工具一律按不安全处理（fail closed），
        默认全 False 时每组恰好一个调用，行为与串行完全一致。
        """
        groups: list[list[dict[str, Any]]] = []
        prev_safe = False
        for tc in tool_calls:
            tool = self.get_tool_by_name(tc["name"])
            safe = bool(tool and tool.concurrency_safe)
            if safe and prev_safe:
                groups[-1].append(tc)
            else:
                groups.append([tc])
            prev_safe = safe
        return groups

    async def _stream_tool_call(
        self,
        context: "FlowContext",
        tool_call: dict[str, Any],
        message_sink: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """执行单个 tool_call，yield 其 AG-UI 事件

        tool 消息默认即时写回 context；并发分组执行时传入 message_sink 收集，
        由调用方在组完成后按 tool_call 原始顺序统一写回
        （OpenAI 协议要求 tool 消息与 assistant.tool_calls 顺序对应）。
        """

        def _emit_message(**kwargs: Any) -> None:
            if message_sink is None:
                context.add_message(self.name, "tool", **kwargs)
            else:
                message_sink.append(kwargs)

        tool = self.get_tool_by_name(tool_call["name"])
        if not tool:
            error_content = json.dumps({"error": f"Tool not found: {tool_call['name']}"}, ensure_ascii=False)
            _emit_message(content=error_content, tool_call_id=tool_call["id"])
            # AG-UI: TOOL_CALL_RESULT (错误)
            yield event.tool_call_result(tool_call_id=tool_call["id"], content=error_content)
            return

        # 解析 LLM 返回的工具参数；解析失败作为该次调用的失败反馈给模型，由模型自行重试
        try:
            tool_args = json.loads(tool_call["arguments"]) if tool_call["arguments"] else {}
        except json.JSONDecodeError as e:
            error_content = json.dumps(
                {
                    "error": f"Invalid tool arguments (JSON parse failed): {e}",
                    "raw_arguments": tool_call["arguments"][:500],
                },
                ensure_ascii=False,
            )
            _emit_message(content=error_content, tool_call_id=tool_call["id"])
            # AG-UI: TOOL_CALL_RESULT (错误)
            yield event.tool_call_result(tool_call_id=tool_call["id"], content=error_content)
            return

        # 执行前进度事件
        progress_label = tool.get_progress_label(tool_args)
        if progress_label:
            yield event.custom(
                name="skill_progress",
                value={
                    "progressId": tool_call["id"],
                    "description": progress_label,
                    "status": "running",
                },
            )

        # 执行工具（传入 LLM 解析的参数作为 inputs）
        result = await tool.execute_async(context, tool_args)

        # 执行后进度事件
        if progress_label:
            yield event.custom(
                name="skill_progress",
                value={
                    "progressId": tool_call["id"],
                    "status": "completed" if result.success else "failed",
                },
            )

        # 使用 result.content 作为 LLM 上下文（Tool 已计算好）
        result_content = result.content or (
            json.dumps(result.result, ensure_ascii=False)
            if result.success
            else json.dumps({"error": result.error}, ensure_ascii=False)
        )
        _emit_message(content=result_content, tool_call_id=tool_call["id"], summary=result.summary)

        # AG-UI: TOOL_CALL_RESULT
        yield event.tool_call_result(tool_call_id=tool_call["id"], content=result_content)

        # 实时用户进度 — 有 summary 时告知前端
        if result.summary:
            yield event.custom(
                name="tool_progress",
                value={
                    "tool_call_id": tool_call["id"],
                    "tool_name": result.name,
                    "success": result.success,
                    "summary": result.summary,
                },
            )

        # 业务自定义事件 — flush pending
        for pending_evt in context.pop_pending_custom_events():
            yield pending_evt

    async def _gather_tool_calls(
        self, context: "FlowContext", group: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """并发执行一组 concurrency_safe 的 tool_calls

        返回 (按完成顺序展平的事件列表, 按原始顺序展平的 tool 消息 kwargs 列表)。
        组内事件先缓冲、gather 结束后统一交调用方 yield，保证单 generator 语义；
        单个调用的意外异常兜底转失败结果，不影响组内其它调用。
        注意：并发期间 execution_records / pending_custom_events 的追加顺序不再确定。
        """
        event_buffers: list[list[dict[str, Any]]] = [[] for _ in group]
        message_buffers: list[list[dict[str, Any]]] = [[] for _ in group]
        finish_order: list[int] = []

        async def _run_one(i: int, tc: dict[str, Any]) -> None:
            try:
                async for evt in self._stream_tool_call(context, tc, message_sink=message_buffers[i]):
                    event_buffers[i].append(evt)
            except Exception as e:
                error_content = json.dumps({"error": str(e)}, ensure_ascii=False)
                message_buffers[i].append({"content": error_content, "tool_call_id": tc["id"]})
                event_buffers[i].append(event.tool_call_result(tool_call_id=tc["id"], content=error_content))
            finally:
                finish_order.append(i)

        await asyncio.gather(*[_run_one(i, tc) for i, tc in enumerate(group)])
        events = [evt for i in finish_order for evt in event_buffers[i]]
        messages = [msg for msgs in message_buffers for msg in msgs]
        return events, messages

    async def run(self, context: "FlowContext", inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行 Agent 逻辑"""
        content_parts = []
        async for evt in self.stream(context, inputs):
            # AG-UI 事件格式
            if isinstance(evt, dict):
                if evt.get("type") == EventType.TEXT_MESSAGE_CONTENT:
                    content_parts.append(evt.get("delta", ""))
                elif evt.get("type") == EventType.RUN_ERROR:
                    raise FlowError("AGENT_EXECUTION_FAILED", 500, {"error": evt.get("message")})
        return {"result": "".join(content_parts)}

    async def stream(
        self, context: "FlowContext", inputs: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """流式执行 Agent，输出 AG-UI 标准事件"""

        # 注入 agent_call_id 供 tool 审计关联
        agent_call_id = str(uuid4())
        context.set_variable("_agent_call_id", agent_call_id)

        # 输入来源：优先 inputs 参数，回退到 context.variables
        user_input = ""
        if inputs:
            user_input = inputs.get("input", "")
        if not user_input:
            user_input = context.get_variable("input") or context.get_variable("query", "")
        msg_id = context.message_id or str(uuid4())

        # 添加用户消息（scoped by agent name）
        context.add_message(self.name, "user", user_input)
        context.last_agent = self.name

        # AG-UI: TEXT_MESSAGE_START
        yield event.text_message_start(message_id=msg_id, role="assistant")

        # 构建消息列表：system + 共享历史 + 当前 agent 的隔离消息
        messages = [self.get_system_message()] + context.history + context.get_messages(self.name)
        tools_schema = self.get_tools_schema() if self.tools else None

        # 获取 LLM 客户端
        client = get_llm_client()

        # Agent 循环
        assistant_content = ""
        for _ in range(self.max_turns):
            # 协作式取消检查点：不再开始新的 LLM 轮次
            if context.is_cancelled:
                if not context.get_variable("_cancel_emitted"):
                    context.set_variable("_cancel_emitted", True)
                    yield event.run_error(message="FLOW_CANCELLED", code="CANCELLED")
                return

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
                    kwargs["tool_choice"] = self.tool_choice

                stream = await client.chat(stream=True, **kwargs)

                async for chunk in stream:
                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]
                    delta = choice.delta

                    # 内容增量 - AG-UI: TEXT_MESSAGE_CONTENT
                    if delta.content:
                        assistant_content += delta.content
                        yield event.text_message_content(
                            message_id=msg_id,
                            delta=delta.content,
                        )

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
                            yield event.tool_call_start(
                                tool_call_id=tool_call_data["id"],
                                tool_call_name=tool_call_data["name"],
                            )

                            # TOOL_CALL_ARGS
                            yield event.tool_call_args(
                                tool_call_id=tool_call_data["id"],
                                delta=tool_call_data["arguments"],
                            )

                            # TOOL_CALL_END
                            yield event.tool_call_end(tool_call_id=tool_call_data["id"])

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
                yield event.run_error(message=error_msg)
                raise FlowError("AGENT_EXECUTION_FAILED", 500, {"error": error_msg}) from e

            # 保存 assistant 消息（tool_calls 转为 OpenAI 标准格式）
            if tool_calls:
                openai_tool_calls = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in tool_calls
                ]
                context.add_message(
                    self.name,
                    "assistant",
                    content=assistant_content or None,
                    tool_calls=openai_tool_calls,
                )
            else:
                context.add_message(self.name, "assistant", assistant_content)

            # 如果没有工具调用，结束循环
            if not tool_calls:
                # 存储结果供 Flow/A2A 使用
                context.set_output(self.name, {"result": assistant_content})
                # AG-UI: TEXT_MESSAGE_END
                yield event.text_message_end(message_id=msg_id)
                context.set_variable("_agent_call_id", None)
                return

            # 执行工具调用：连续的 concurrency_safe 调用聚组并发，其余保持串行（默认全串行）
            for group in self._group_tool_calls(tool_calls):
                # 协作式取消检查点：不再开始新的工具执行（不中断在途工具）
                if context.is_cancelled:
                    if not context.get_variable("_cancel_emitted"):
                        context.set_variable("_cancel_emitted", True)
                        yield event.run_error(message="FLOW_CANCELLED", code="CANCELLED")
                    return

                if len(group) == 1:
                    # 串行路径：事件实时 yield，tool 消息即时写回
                    async for evt in self._stream_tool_call(context, group[0]):
                        yield evt
                else:
                    # 并发组：事件按完成顺序 yield，tool 消息按原始顺序写回
                    group_events, group_messages = await self._gather_tool_calls(context, group)
                    for evt in group_events:
                        yield evt
                    for msg in group_messages:
                        context.add_message(self.name, "tool", **msg)

            # 更新消息列表，继续下一轮
            messages = [self.get_system_message()] + context.history + context.get_messages(self.name)

        # max_turns 耗尽：必须显式收尾，禁止静默截断
        if self.on_max_turns == "error":
            error_msg = f"Agent {self.name} exceeded max_turns={self.max_turns}"
            yield event.run_error(message=error_msg, code="AGENT_MAX_TURNS_EXCEEDED")
            raise AgentError("AGENT_MAX_TURNS_EXCEEDED", 500, {"agent": self.name})

        # finalize：最后一轮已生成的部分文本作为降级输出，带截断标记
        yield event.custom(
            name="agent_max_turns",
            value={"agentName": self.name, "maxTurns": self.max_turns},
        )
        context.set_output(self.name, {"result": assistant_content, "truncated": True})
        yield event.text_message_end(message_id=msg_id)
        context.set_variable("_agent_call_id", None)
