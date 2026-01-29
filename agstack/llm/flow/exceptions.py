#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""统一异常定义"""

from ...exceptions import AppException


class FlowError(AppException):
    """Flow 基础异常"""

    def __init__(self, error_key: str, http_status: int = 500, args: dict | None = None):
        super().__init__(error_key, http_status, args)


class AgentError(FlowError):
    """Agent 异常"""

    def __init__(self, error_key: str, http_status: int = 500, args: dict | None = None):
        super().__init__(error_key, http_status, args)


class ToolExecutionError(AgentError):
    """工具执行异常"""


class ModelError(AgentError):
    """模型调用异常"""


class FlowConfigError(FlowError):
    """Flow 配置异常"""

    def __init__(self, error_key: str, args: dict | None = None):
        super().__init__(error_key, 400, args)


class FlowExecutionError(FlowError):
    """Flow 执行异常"""

    def __init__(self, error_key: str, args: dict | None = None):
        super().__init__(error_key, 500, args)


class NodeExecutionError(FlowError):
    """节点执行异常"""

    def __init__(self, error_key: str, args: dict | None = None):
        super().__init__(error_key, 500, args)
