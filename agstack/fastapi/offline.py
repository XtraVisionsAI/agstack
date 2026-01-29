#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.staticfiles import StaticFiles
from starlette.responses import HTMLResponse


# noinspection PyUnresolvedReferences
def make_offline(
    app: FastAPI,
    static_dir: str,
    static_url: str | None = "/static-offline-docs",
    docs_url: str | None = "/docs",
    redoc_url: str | None = "/redoc",
) -> None:
    """patch the FastAPI obj that doesn't rely on CDN for the documentation page"""
    openapi_url = app.openapi_url
    swagger_ui_oauth2_redirect_url = app.swagger_ui_oauth2_redirect_url

    def remove_route(url: str) -> None:
        """
        remove original route from app
        """
        index = None
        i = 0
        for i, r in enumerate(app.routes):
            path = getattr(r, "path", None)
            if path is not None and isinstance(path, str) and path.lower() == url.lower():
                index = i
                break
        if isinstance(index, int):
            app.routes.pop(i)

    # Set up static file mount
    if static_url is not None:
        app.mount(
            static_url,
            StaticFiles(directory=Path(static_dir).as_posix()),
            name="static-offline-docs",
        )

    if docs_url is not None and swagger_ui_oauth2_redirect_url is not None:
        remove_route(docs_url)
        remove_route(swagger_ui_oauth2_redirect_url)

        # Define the doc and redoc pages, pointing at the right files
        @app.get(docs_url, include_in_schema=False)
        async def custom_swagger_ui_html(request: Request) -> HTMLResponse:
            root = request.scope.get("root_path")
            favicon = f"{root}{static_url}/favicon.png"
            # noinspection PyUnresolvedReferences
            return get_swagger_ui_html(
                openapi_url=f"{root}{openapi_url}",
                title=app.title + " - Swagger UI",
                oauth2_redirect_url=swagger_ui_oauth2_redirect_url,
                swagger_js_url=f"{root}{static_url}/swagger-ui-bundle.js",
                swagger_css_url=f"{root}{static_url}/swagger-ui.css",
                swagger_favicon_url=favicon,
            )

        if swagger_ui_oauth2_redirect_url is not None:

            @app.get(swagger_ui_oauth2_redirect_url, include_in_schema=False)
            async def swagger_ui_redirect() -> HTMLResponse:
                return get_swagger_ui_oauth2_redirect_html()

    if redoc_url is not None:
        remove_route(redoc_url)

        @app.get(redoc_url, include_in_schema=False)
        async def redoc_html(request: Request) -> HTMLResponse:
            root = request.scope.get("root_path")
            favicon = f"{root}{static_url}/favicon.png"
            # noinspection PyUnresolvedReferences
            return get_redoc_html(
                openapi_url=f"{root}{openapi_url}",
                title=app.title + " - ReDoc",
                redoc_js_url=f"{root}{static_url}/redoc.standalone.js",
                with_google_fonts=False,
                redoc_favicon_url=favicon,
            )
