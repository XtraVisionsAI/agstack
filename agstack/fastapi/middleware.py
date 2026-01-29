#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""FastAPI 中间件"""

import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class RequestIDMiddleware(BaseHTTPMiddleware):
    """请求 ID 中间件

    自动为每个请求生成或使用前端传入的 X-Request-ID
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 优先使用前端传入的 request_id，否则生成新的
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # 将 request_id 存储到 request.state（供后续使用）
        request.state.request_id = request_id

        # 调用下一个处理器
        response = await call_next(request)

        # 在响应头中返回 request_id
        response.headers["X-Request-ID"] = request_id

        return response
