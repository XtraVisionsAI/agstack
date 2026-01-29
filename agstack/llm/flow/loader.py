#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

"""Flow 配置加载器"""

import json
from pathlib import Path
from typing import Any

from .exceptions import FlowConfigError
from .flow import Flow


class FlowLoader:
    """Flow 配置加载器"""

    @staticmethod
    def load_from_file(file_path: str | Path) -> Flow:
        """从文件加载 Flow 配置"""
        path = Path(file_path)
        if not path.exists():
            raise FlowConfigError("FLOW_FILE_NOT_FOUND", {"file_path": str(file_path)})

        with open(path, encoding="utf-8") as f:
            config = json.load(f)

        return FlowLoader.load_from_dict(config)

    @staticmethod
    def load_from_string(json_str: str) -> Flow:
        """从 JSON 字符串加载 Flow 配置"""
        try:
            config = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise FlowConfigError("FLOW_INVALID_JSON", {"error": str(e)}) from e

        return FlowLoader.load_from_dict(config)

    @staticmethod
    def load_from_dict(config: dict[str, Any]) -> Flow:
        """从字典加载 Flow 配置"""
        # 验证必需字段
        required_fields = ["flow_id", "name"]
        for field in required_fields:
            if field not in config:
                raise FlowConfigError("FLOW_MISSING_REQUIRED_FIELD", {"field": field})

        # 验证节点配置
        nodes = config.get("nodes", [])
        if not isinstance(nodes, list):
            raise FlowConfigError("FLOW_INVALID_NODES")

        for node in nodes:
            if not isinstance(node, dict) or "id" not in node or "type" not in node:
                raise FlowConfigError("FLOW_INVALID_NODE_CONFIG", {"node": node})

        return Flow(
            flow_id=config["flow_id"],
            name=config["name"],
            description=config.get("description", ""),
            nodes=nodes,
            edges=config.get("edges", []),
            variables=config.get("variables", {}),
        )
