#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""Flow 状态管理"""

import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from .records import Status


if TYPE_CHECKING:
    from .records import Record


class FlowState(BaseModel):
    """流程状态"""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    input: str = ""
    output: str | None = None

    # 执行记录 - 树形结构
    record: "Record | None" = None

    # 上下文
    context: dict[str, Any] = Field(default_factory=dict)

    def get_current_record(self) -> "Record | None":
        """获取当前执行记录"""
        if not self.record:
            return None

        def find_running(r: "Record") -> "Record | None":
            if r.status == Status.RUNNING:
                return r
            for child in r.children:
                result = find_running(child)
                if result:
                    return result
            return None

        return find_running(self.record)
