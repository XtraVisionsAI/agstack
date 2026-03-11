#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""Python 沙箱执行（基于 RestrictedPython）"""

import builtins
from typing import Any

from RestrictedPython import compile_restricted, safe_globals
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import guarded_unpack_sequence, safer_getattr


# 白名单内置模块
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
    glb["__builtins__"] = {**glb["__builtins__"], "__import__": _safe_import}

    loc: dict[str, Any] = {}
    exec(byte_code, glb, loc)  # noqa: S102

    main_fn = loc.get("main")
    if not callable(main_fn):
        raise ValueError("Python node code must define a callable 'main' function")

    result = main_fn(**inputs)
    if not isinstance(result, dict):
        raise TypeError(f"main() must return a dict, got {type(result).__name__}")

    return result
