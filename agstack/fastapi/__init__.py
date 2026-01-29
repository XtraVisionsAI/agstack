#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from .exception import register_exception_handlers
from .middleware import RequestIDMiddleware
from .offline import make_offline


def setup_fastapi(title: str, version: str, debug: bool, static_url: str) -> FastAPI:
    app = FastAPI(
        title=title,
        version=version,
        license_info={"name": "END USER LICENSE AGREEMENT"},
        debug=debug,
    )

    # CORS 中间件（仅开发环境）
    if debug:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )

    # GZip 压缩（减少带宽消耗）
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # 请求 ID 中间件
    app.add_middleware(RequestIDMiddleware)

    # 注册异常处理器
    register_exception_handlers(app)

    make_offline(app, static_url)

    return app
