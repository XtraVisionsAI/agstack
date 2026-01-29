#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from .logger import setup_logger
from .types import AppConfig


def _load_config_file(config_path: Path) -> dict[str, Any]:
    """加载配置文件（容错处理）"""
    if not config_path.exists():
        return {}

    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    except Exception:  # noqa
        print(f"Failed to load config file: {config_path}")
        return {}


def _load_env_overrides(prefix: str = "APP") -> dict[str, Any]:
    """加载环境变量覆盖配置

    支持格式：
    - APP_DATABASE_HOST=localhost
    - APP_WEBAPI_DEBUG=true
    - APP_MODELS_EMBEDDING_ENGINE=bedrock
    """
    config_data = {}
    prefix = prefix.upper() + "_"

    for key, value in os.environ.items():
        if key.startswith(prefix):
            config_key = key[len(prefix) :].lower()
            _set_nested_value(config_data, config_key, _parse_env_value(value))

    return config_data


def _set_nested_value(data: dict[str, Any], key: str, value: Any) -> None:
    """设置嵌套字典值（支持下划线分隔）"""
    if "_" in key:
        parts = key.split("_", 1)
        if parts[0] not in data:
            data[parts[0]] = {}
        _set_nested_value(data[parts[0]], parts[1], value)
    else:
        data[key] = value


def _parse_env_value(value: str) -> Any:
    """解析环境变量值（自动类型转换）"""
    # 布尔值
    if value.lower() in ("true", "false"):
        return value.lower() == "true"

    # 数字
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass

    # JSON
    if value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass

    return value


def _determine_log_level(config_data: dict[str, Any]) -> str:
    """确定日志级别（优先级：环境变量 > debug 模式 > 配置文件）"""
    # 1. 环境变量优先级最高
    env_level = os.environ.get("METAMATRIX_LOG_LEVEL")
    if env_level:
        level = env_level.upper()
        if level in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            return level

    # 2. debug 模式强制使用 DEBUG
    mode = config_data.get("mode", "").lower()
    if mode == "dev":
        return "DEBUG"

    # 3. 使用配置文件中的级别
    return config_data.get("logger", {}).get("level", "INFO")


def setup_config(appname: str, envprefix: str = "APP") -> AppConfig:
    """初始化配置

    Args:
        appname: 应用名称
        envprefix: 环境变量前缀

    Returns:
        AppConfig: 配置实例
    """
    approot = Path.cwd()
    config_path = approot / "config.toml"

    # 1. 加载配置文件（容错）
    config_data = _load_config_file(config_path)

    # 2. 加载环境变量覆盖
    env_overrides = _load_env_overrides(envprefix)
    config_data.update(env_overrides)

    # 3. 设置应用信息
    config_data.update({"appname": appname, "approot": approot})

    # 4. 确定日志级别（优先级处理）
    log_level = _determine_log_level(config_data)
    config_data.setdefault("logger", {})["level"] = log_level

    # 5. 初始化日志（使用字典配置）
    setup_logger(appname, **config_data["logger"])

    # 8. 创建配置实例（最后验证）
    config = AppConfig.model_validate(config_data)

    return config
