#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""运行时上下文"""

import uuid
from contextvars import ContextVar, Token


_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str:
    return _request_id.get() or str(uuid.uuid4())


def set_request_id(value: str) -> Token:
    return _request_id.set(value)


def reset_request_id(token: Token) -> None:
    _request_id.reset(token)
