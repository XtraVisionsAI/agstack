#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel as PyBaseModel
from pydantic import ConfigDict, Field


__all__ = [
    "ConfigDict",
    "Field",
    "BaseSchema",
    "BaseRequestSchema",
    "BaseResponseSchema",
    "ErrorDict",
    "DataResponseModel",
]


DataT = TypeVar("DataT")

TYPES_ENCODERS: dict[type[object], Callable[[Any], Any]] = {
    datetime: lambda x: x.astimezone(),
    UUID: lambda x: str(x),
}

RESPONSE_TYPES_ENCODERS: dict[type[object], Callable[[Any], Any]] = {
    **TYPES_ENCODERS,
    datetime: lambda x: x.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
}


class BaseSchema(PyBaseModel):
    """
    所有模式的基础模型，支持 bson
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders=TYPES_ENCODERS,
        populate_by_name=True,
        from_attributes=True,
        extra="ignore",
    )


class BaseRequestSchema(BaseSchema):
    """
    所有请求数据模式的基础模型，默认字典格式排除空字段
    """

    def model_dump(self, *args, **kwargs):
        if kwargs and kwargs.get("exclude_none") is not None:
            kwargs["exclude_none"] = True

        return BaseSchema.model_dump(self, *args, **kwargs)


class ErrorDict(BaseSchema):
    """
    响应模式的错误字典
    """

    msg: str
    metadata: Any | None = None


class BaseResponseSchema(BaseSchema):
    """
    所有响应数据模式的基础模型，支持 bson 和日期时间
    """

    model_config = ConfigDict(json_encoders=RESPONSE_TYPES_ENCODERS)

    status: int = 200
    error: ErrorDict | None = None


class DataResponseModel[DataT](BaseResponseSchema):
    """
    响应模式的通用响应模型，支持日期时间
    """

    data: DataT | None = None
