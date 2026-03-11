#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Python 沙箱节点处理器 — 从 sandbox.py 迁入"""

from typing import TYPE_CHECKING, Any

from .base import NodeHandler


if TYPE_CHECKING:
    from ..context import FlowContext


# ── 沙箱执行（原 sandbox.py） ──

import builtins

from RestrictedPython import compile_restricted, safe_globals, utility_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import guarded_unpack_sequence, safer_getattr


_ALLOWED_MODULES = frozenset(
    {
        "json",
        "re",
        "math",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "operator",
        "string",
    }
)

_builtins_import = builtins.__import__


def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
    """只允许导入白名单模块"""
    if name not in _ALLOWED_MODULES:
        raise ImportError(f"Import of '{name}' is not allowed in python node")
    return _builtins_import(name, *args, **kwargs)


def _full_write_guard(ob: Any) -> Any:
    """允许对 list/dict/set 等可变容器的写操作"""
    return ob


def execute_python_node(code: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """在 RestrictedPython 沙箱中执行用户代码

    Args:
        code: 用户代码，必须定义 main(**kwargs) -> dict 函数
        inputs: 传入 main 函数的参数

    Returns:
        main 函数的返回值（dict）
    """
    byte_code = compile_restricted(code, "<flow_python_node>", "exec")

    glb: dict[str, Any] = dict(safe_globals)
    glb["_getitem_"] = default_guarded_getitem
    glb["_getiter_"] = default_guarded_getiter
    glb["_unpack_sequence_"] = guarded_unpack_sequence
    glb["_getattr_"] = safer_getattr
    glb["_write_"] = _full_write_guard
    glb["__builtins__"] = {
        **glb["__builtins__"],
        **utility_builtins,
        "list": list,
        "dict": dict,
        "__import__": _safe_import,
    }

    loc: dict[str, Any] = {}
    exec(byte_code, glb, loc)  # noqa: S102

    main_fn = loc.get("main")
    if not callable(main_fn):
        raise ValueError("Python node code must define a callable 'main' function")

    result = main_fn(**inputs)
    if not isinstance(result, dict):
        raise TypeError(f"main() must return a dict, got {type(result).__name__}")

    return result


# ── NodeHandler ──


class PythonNodeHandler(NodeHandler):
    """Python 沙箱执行节点"""

    node_type = "python"

    async def execute(self, node: dict, context: "FlowContext") -> Any:
        config = node.get("config", {})
        resolved_inputs = self.resolve_inputs(config, context)
        code_str = config.get("code", "")
        return execute_python_node(code_str, resolved_inputs)
