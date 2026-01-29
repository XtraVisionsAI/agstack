#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

from pathlib import Path
from typing import Any, Literal, TypeVar, overload

from pydantic import TypeAdapter

from ..schema import BaseSchema


T = TypeVar("T")


class ConfigBase(BaseSchema):
    """配置基类"""

    model_config = {"extra": "allow", "validate_assignment": True}


class LogConfig(ConfigBase):
    """日志配置"""

    level: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    rotation: str = "1 day"
    retention: str = "30 days"
    log_dir: str | None = "logs"
    enable_console: bool = True


class AppConfig(ConfigBase):
    """应用配置"""

    appname: str = "app"
    approot: Path = Path.cwd()
    mode: str = "prod"
    version: str = "0.1.0"
    logger: LogConfig = LogConfig()

    def __getattr__(self, name: str) -> Any:
        if "__pydantic_extra__" in self.__dict__ and name in self.__dict__["__pydantic_extra__"]:
            return self.__dict__["__pydantic_extra__"][name]
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    @overload
    def get_opt(self, key: str, type_: type[T], default: T) -> T: ...

    @overload
    def get_opt(self, key: str, type_: type[T], default: None = None) -> T | None: ...

    def get_opt(self, key: str, type_: type[T], default: T | None = None) -> T | None:
        """获取字段并转换为指定类型

        Args:
            key: 字段名，支持点号访问如 "log.level" 或 "redis.host"
            type_: 目标类型，可以是基础类型或 Pydantic Model
            default: 默认值，当字段不存在时返回
        """
        # 解析点号路径
        keys = key.split(".")
        value: Any = self

        for k in keys:
            if isinstance(value, ConfigBase):
                # 尝试从模型字段获取
                try:
                    value = getattr(value, k)
                    continue
                except AttributeError:
                    pass

            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        # 类型转换
        if value is None:
            return default

        # 如果是 Pydantic Model，使用 TypeAdapter 验证和转换
        if isinstance(type_, type) and issubclass(type_, BaseSchema):
            if isinstance(value, dict):
                return type_(**value)  # noqa
            return default

        # 基础类型直接转换
        try:
            return TypeAdapter(type_).validate_python(value)
        except Exception:  # noqa
            return default

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return self.model_dump()
