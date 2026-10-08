#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""Flow 执行记录类型"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from ...schema import BaseSchema


class Status(str, Enum):
    """执行状态"""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class Record(BaseSchema):
    """统一执行记录"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str  # "flow", "task", "step"
    name: str  # 组件名称
    status: Status = Status.RUNNING
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    start_time: datetime = Field(default_factory=datetime.now)
    end_time: datetime | None = None
    children: list["Record"] = Field(default_factory=list)

    def complete(self, success: bool = True, error: str | None = None, outputs: dict[str, Any] | None = None):
        """完成记录"""
        self.status = Status.SUCCESS if success else Status.FAILED
        self.end_time = datetime.now()
        if error:
            self.error = error
        if outputs:
            self.outputs.update(outputs)

    def add_child(self, child: "Record"):
        """添加子记录"""
        self.children.append(child)
