#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

import logging
import sys
from datetime import time, timedelta
from pathlib import Path

from loguru import RetentionFunction, RotationFunction, logger


class InterceptHandler(logging.Handler):
    """拦截标准 logging 并重定向到 loguru"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 直接使用 logging.LogRecord 的信息，避免栈帧查找
        logger.patch(
            lambda r: r.update(
                name=record.name,
                file={"name": record.pathname, "path": record.pathname},  # type: ignore[arg-type]
                line=record.lineno,
                function=record.funcName,
            )
        ).log(level, record.getMessage())

        # CRITICAL/FATAL 级别自动退出
        if record.levelno >= logging.CRITICAL:
            sys.exit(1)


def setup_logger(
    appname: str,
    output: str | Path,
    level: str | int = "INFO",
    rotation: str | int | time | timedelta | RotationFunction | None = "1 day",
    retention: str | int | timedelta | RetentionFunction | None = "30 days",
) -> None:
    """初始化日志系统

    :param appname: 应用名称
    :param output: 日志输出目录
    :param level: 日志级别（默认 INFO）
    :param rotation: 日志轮转策略（默认每天轮转）
    :param retention: 日志保留策略（默认保留 30 天）
    """
    log_path = Path(output)
    log_path.mkdir(parents=True, exist_ok=True)

    log_format = "[{time:YYYY-MM-DD HH:mm:ss}] | {level: <8} | {name}:{line} - {message}"

    # 移除默认 handler
    logger.remove()

    # 添加控制台 handler
    logger.add(
        sys.stderr,
        format=log_format,
        level=level,
        colorize=True,
    )

    # 添加文件 handler（带轮转）
    logger.add(
        log_path / f"{appname}.log",
        format=log_format,
        level=level,
        rotation=rotation,
        retention=retention,
        encoding="utf8",
    )

    # 拦截标准 logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in logging.root.manager.loggerDict.keys():
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
