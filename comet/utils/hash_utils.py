"""哈希工具函数"""

import hashlib
from typing import Any
import json


def code_hash(code: str) -> str:
    """
    计算代码的哈希值（用于去重）

    Args:
        code: 代码字符串

    Returns:
        SHA256 哈希值
    """
    # 移除空白符差异
    normalized = "".join(code.split())
    return hashlib.sha256(normalized.encode()).hexdigest()


def signature_hash(obj: Any) -> str:
    """
    计算对象签名的哈希值

    Args:
        obj: 任意可序列化对象

    Returns:
        SHA256 哈希值
    """
    if hasattr(obj, "model_dump"):
        # Pydantic 模型
        data = obj.model_dump()
    elif hasattr(obj, "__dict__"):
        data = obj.__dict__
    else:
        data = obj

    json_str = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(json_str.encode()).hexdigest()


def generate_id(prefix: str, content: str) -> str:
    """
    生成唯一 ID

    Args:
        prefix: ID 前缀
        content: 内容（用于生成哈希）

    Returns:
        唯一 ID
    """
    hash_val = code_hash(content)[:12]
    return f"{prefix}_{hash_val}"
