#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Echo 节点 — 将 context 中的文本作为 TEXT_MESSAGE 事件流式输出（不调用 LLM）"""

from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from agstack.llm.flow import event
from agstack.llm.flow.nodes.base import NodeHandler


if TYPE_CHECKING:
    from agstack.llm.flow.context import FlowContext


class EchoNodeHandler(NodeHandler):
    """Echo 节点：从 context 读取文本并流式输出为 TEXT_MESSAGE 事件

    用于将内部 agent 的结果展示给用户，不产生额外 LLM 调用。
    """

    node_type = "echo"

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        resolved = self.resolve_inputs(config, context)
        content = resolved.get("content", "")
        if isinstance(content, dict):
            content = content.get("result", "")
        return {"result": content}

    async def stream(self, node: dict, context: "FlowContext", node_id: str) -> AsyncIterator[dict[str, Any]]:
        config = node.get("config", {})
        step_name = self.get_step_name(node, node_id)
        sid = str(uuid4())

        yield event.step_started(step_name=step_name, step_id=sid)

        resolved = self.resolve_inputs(config, context)
        content = resolved.get("content", "")
        if isinstance(content, dict):
            content = content.get("result", "")

        if content:
            msg_id = context.message_id or str(uuid4())
            yield event.text_message_start(message_id=msg_id, role="assistant")

            chunk_size = 80
            for i in range(0, len(content), chunk_size):
                yield event.text_message_content(message_id=msg_id, delta=content[i : i + chunk_size])

            yield event.text_message_end(message_id=msg_id)

        context.set_output(node_id, {"result": content})
        yield event.step_finished(step_name=step_name, step_id=sid)
