#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""统一异常定义

提供基于 KEY 值的异常系统，支持多语言和参数化错误消息。

设计原则：
    - 业务层只关心错误代码，不关心 HTTP 状态码
    - HTTP 状态码由全局异常处理器根据错误代码映射
    - 保持业务逻辑与 HTTP 层解耦
"""

from typing import Any


class AppException(Exception):
    """业务异常基类（使用 KEY 值形式，支持多语言）

    注意：避免与 Python 内置 BaseException 冲突
    """

    def __init__(self, error_key: str, http_status: int = 400, args: dict[str, Any] | None = None):
        """初始化异常

        :param error_key: 错误标识键，用于多语言映射
        :param http_status: HTTP 状态码，默认 400
        :param args: 错误参数，用于消息模板插值
        """
        if args is None:
            args = {}
        super().__init__(error_key, args)
        self._error_key = error_key
        self._http_status = http_status
        self._arguments = args

    @property
    def error_key(self) -> str:
        """错误标识键"""
        return self._error_key

    @property
    def http_status(self) -> int:
        """HTTP 状态码"""
        return self._http_status

    @property
    def arguments(self) -> dict[str, Any]:
        """错误参数"""
        return self._arguments

    def __str__(self) -> str:
        if self._arguments:
            return f"{self._error_key}({self._arguments})"
        return self._error_key

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(error_key={self._error_key!r}, args={self._arguments!r})"
