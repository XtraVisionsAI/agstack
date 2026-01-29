#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""全局异常处理器

统一处理所有异常类型：
    - ApplicationException: 业务异常
    - RequestValidationError: FastAPI 验证错误
    - HTTPException: HTTP 异常
    - Exception: 未捕获的异常（500）
"""

import logging
import traceback
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..exceptions import AppException
from ..status import Code


logger = logging.getLogger(__name__)


def _build_error_response(error_key: str, args: dict | None = None, request_id: str | None = None) -> dict:
    """构建统一错误响应格式"""
    response = {
        "error_key": error_key,
        "args": args or {},
        "timestamp": datetime.now().isoformat(),
    }
    if request_id:
        response["request_id"] = request_id
    return response


def register_exception_handlers(app: FastAPI):
    async def app_exception_handler(req: Request, exc: AppException) -> JSONResponse:
        """业务异常处理器"""
        request_id = getattr(req.state, "request_id", None)

        logger.error(
            f"Application exception, request_id={request_id}, "
            f"url={req.url}, error_key={exc.error_key}, args={exc.arguments}"
        )

        return JSONResponse(
            status_code=exc.http_status,
            content=_build_error_response(exc.error_key, exc.arguments, request_id),
        )

    async def validation_exception_handler(req: Request, exc: RequestValidationError) -> JSONResponse:
        """验证错误处理器"""
        status_code = Code.STATUS_400_BAD_REQUEST.value
        request_id = getattr(req.state, "request_id", None)

        # 提取字段错误信息
        errors = []
        for error in exc.errors():
            field = ".".join(str(loc) for loc in error["loc"][1:])  # 跳过 'body'
            errors.append(
                {
                    "field": field,
                    "type": error["type"],  # 前端 i18n key
                    "message": error["msg"],  # 降级方案：原始英文消息
                    "ctx": error.get("ctx", {}),  # 参数（如 limit_value, min_length）
                }
            )

        logger.error(f"Validation error, request_id={request_id}, url={req.url}, errors={errors}")
        return JSONResponse(
            status_code=status_code,
            content=_build_error_response("VALIDATION_ERROR", {"errors": errors}, request_id),
        )

    async def http_exception_handler(req: Request, exc: HTTPException) -> JSONResponse:
        """HTTP 异常处理器"""
        status_code = exc.status_code
        request_id = getattr(req.state, "request_id", None)

        logger.error(f"HTTP error, request_id={request_id}, url={req.url}, status={status_code}, detail={exc.detail}")

        return JSONResponse(
            status_code=status_code,
            content=_build_error_response("HTTP_ERROR", {"detail": str(exc.detail)}, request_id),
        )

    async def general_exception_handler(req: Request, exc: Exception) -> JSONResponse:
        """通用异常处理器（兜底）"""
        request_id = getattr(req.state, "request_id", None)

        # 记录完整错误信息
        logger.exception(
            f"Unhandled exception, request_id={request_id}, url={req.url}, method={req.method}, client={req.client}"
        )

        # 生产环境：隐藏详细信息
        if not app.debug:
            return JSONResponse(
                status_code=500,
                content=_build_error_response("INTERNAL_SERVER_ERROR", {}, request_id),
            )

        # 开发环境：返回详细信息
        return JSONResponse(
            status_code=500,
            content=_build_error_response(
                "INTERNAL_SERVER_ERROR",
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
                request_id,
            ),
        )

    # 注册异常处理器（按优先级顺序）
    app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[reportArgumentType]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[reportArgumentType]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[reportArgumentType]
    app.add_exception_handler(Exception, general_exception_handler)
