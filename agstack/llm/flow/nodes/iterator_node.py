#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Iterator 节点处理器 — 通用迭代原语，管理遍历状态并通过 edge 组合实现路由"""

from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

from .. import event
from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


class IteratorNodeHandler(NodeHandler):
    """Iterator 节点：管理数组遍历状态，暴露当前元素供下游引用，收集每轮结果。

    通过 edge-driven 执行被多次访问：
    - 首次访问：初始化迭代，暴露第一个元素，走迭代体 edge
    - Cycle-back：收集上一轮结果，推进 index，继续或走 completion edge
    """

    node_type = "iterator"

    def _resolve_items(self, config: dict, context: "FlowContext") -> list:
        items_ref = config.get("items", "")
        items = context.resolve_reference(items_ref) if isinstance(items_ref, str) else items_ref
        if items is None:
            return []
        if not isinstance(items, list):
            return [items]
        return items

    def _set_item_output(self, node_id: str, state: dict, config: dict, context: "FlowContext") -> None:
        collect_to = config.get("collect_to", "results")
        idx = state["index"]
        context.set_output(
            node_id,
            {
                "current_item": state["items"][idx],
                "index": idx,
                "count": len(state["items"]),
                "done": False,
                collect_to: list(state["collected"]),
            },
        )

    def _set_done_output(self, node_id: str, state: dict, config: dict, context: "FlowContext") -> None:
        collect_to = config.get("collect_to", "results")
        context.set_output(
            node_id,
            {
                "done": True,
                "count": len(state["items"]),
                collect_to: list(state["collected"]),
            },
        )

    def _build_event(
        self, config: dict, event_key: str, state: dict, *, error: str | None = None
    ) -> dict[str, Any] | None:
        events_config = config.get("events")
        if not events_config:
            return None
        evt_cfg = events_config.get(event_key)
        if not evt_cfg:
            return None

        name = evt_cfg.get("name", event_key)
        value_template = evt_cfg.get("value", {})
        value = self._interpolate_event_value(value_template, state, error=error)
        return event.custom(name=name, value=value)

    def _interpolate_event_value(self, template: Any, state: dict, *, error: str | None = None) -> Any:
        if isinstance(template, str):
            if template == "$index":
                return state["index"]
            if template == "$item":
                return state["items"][state["index"]] if state["index"] < len(state["items"]) else None
            if template == "$count":
                return len(state["items"])
            if template == "$error":
                return error
            return template
        if isinstance(template, dict):
            return {k: self._interpolate_event_value(v, state, error=error) for k, v in template.items()}
        if isinstance(template, list):
            return [self._interpolate_event_value(v, state, error=error) for v in template]
        return template

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        items = self._resolve_items(config, context)
        collect_to = config.get("collect_to", "results")
        return {"done": True, "count": len(items), collect_to: [], "current_item": None, "index": 0}

    async def stream(self, node: dict, context: "FlowContext", node_id: str) -> AsyncIterator[dict[str, Any]]:
        config = node.get("config", {})
        state_key = f"_iter_{node_id}"
        step_name = self.get_step_name(node, node_id)

        state = context.get_variable(state_key)

        if state is None:
            # ═══ 首次访问：初始化 ═══
            items = self._resolve_items(config, context)
            state = {"items": items, "index": 0, "collected": []}
            context.set_variable(state_key, state)

            sid = str(uuid4())
            yield event.step_started(step_name=step_name, step_id=sid)

            evt = self._build_event(config, "on_start", state)
            if evt:
                yield evt

            if not items:
                self._set_done_output(node_id, state, config, context)
                yield event.step_finished(step_name=step_name, step_id=sid)
                return

            self._set_item_output(node_id, state, config, context)
            evt = self._build_event(config, "on_item_start", state)
            if evt:
                yield evt

            yield event.step_finished(step_name=step_name, step_id=sid)

        else:
            # ═══ Cycle-back：收集 + 推进 ═══
            sid = str(uuid4())
            yield event.step_started(step_name=step_name, step_id=sid)

            prev_node_id = context.get_variable("_prev_node_id")
            error = context.get_variable(f"_iter_{node_id}_error")

            if error:
                state["collected"].append(
                    {
                        "error": error,
                        "item": state["items"][state["index"]] if state["index"] < len(state["items"]) else None,
                    }
                )
                evt = self._build_event(config, "on_item_error", state, error=error)
                if evt:
                    yield evt
                context.set_variable(f"_iter_{node_id}_error", None)
            else:
                prev_output = context.outputs.get(prev_node_id) if prev_node_id else None
                state["collected"].append(prev_output)
                evt = self._build_event(config, "on_item_end", state)
                if evt:
                    yield evt

            state["index"] += 1
            max_iter = config.get("max_iterations")

            if state["index"] >= len(state["items"]) or (max_iter and state["index"] >= max_iter):
                self._set_done_output(node_id, state, config, context)
                yield event.step_finished(step_name=step_name, step_id=sid)
                return

            self._set_item_output(node_id, state, config, context)
            evt = self._build_event(config, "on_item_start", state)
            if evt:
                yield evt

            yield event.step_finished(step_name=step_name, step_id=sid)

    def get_step_name(self, node: dict, node_id: str) -> str:
        return f"iterator:{node_id}"
