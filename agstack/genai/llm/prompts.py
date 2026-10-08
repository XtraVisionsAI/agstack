#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

from typing import Any


class PromptTemplate:
    """提示词模板"""

    def __init__(self, template: str):
        self.template = template

    def format(self, **kwargs: Any) -> str:
        """格式化模板

        :param kwargs: 模板变量
        :return: 格式化后的字符串
        """
        return self.template.format(**kwargs)
