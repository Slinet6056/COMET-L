"""契约提取器"""

import json
import logging
from typing import Optional, List
from datetime import datetime

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
from ..models import Contract
from ..utils.hash_utils import generate_id

logger = logging.getLogger(__name__)


class SpecExtractor:
    """契约提取器 - 从源代码中提取前置条件、后置条件和异常条件"""

    def __init__(self, llm_client: LLMClient):
        """
        初始化契约提取器

        Args:
            llm_client: LLM 客户端
        """
        self.llm = llm_client
        self.prompt_manager = PromptManager()

    def extract_from_method(
        self,
        class_name: str,
        method_signature: str,
        source_code: str,
        javadoc: Optional[str] = None,
    ) -> Optional[Contract]:
        """
        从方法中提取契约

        Args:
            class_name: 类名
            method_signature: 方法签名
            source_code: 方法源代码
            javadoc: Javadoc 注释

        Returns:
            Contract 对象，如果提取失败则返回 None
        """
        try:
            # 渲染提示词
            system, user = self.prompt_manager.render_extract_contract(
                class_name=class_name,
                method_signature=method_signature,
                source_code=source_code,
                javadoc=javadoc,
            )

            # 调用 LLM
            response = self.llm.chat_with_system(
                system_prompt=system,
                user_prompt=user,
                temperature=0.3,  # 较低温度以保证输出稳定
                response_format={"type": "json_object"},
            )

            # 解析响应
            data = json.loads(response)

            # 提取方法名
            method_name = method_signature.split('(')[0].strip().split()[-1]

            # 创建 Contract 对象
            contract = Contract(
                id=generate_id("contract", f"{class_name}.{method_name}"),
                class_name=class_name,
                method_name=method_name,
                method_signature=method_signature,
                preconditions=data.get("preconditions", []),
                postconditions=data.get("postconditions", []),
                exceptions=data.get("exceptions", []),
                description=data.get("description"),
                source="llm_extraction",
                confidence=0.8,  # LLM 提取的置信度
                created_at=datetime.now(),
            )

            logger.info(f"成功提取契约: {class_name}.{method_name}")
            return contract

        except Exception as e:
            logger.error(f"契约提取失败: {e}")
            return None

    def extract_from_class(
        self,
        class_name: str,
        class_code: str,
        method_names: Optional[List[str]] = None,
    ) -> List[Contract]:
        """
        从类中提取所有 public 方法的契约

        Args:
            class_name: 类名
            class_code: 类代码
            method_names: 要提取的方法名列表（如果为 None 则提取所有 public 方法）

        Returns:
            Contract 对象列表
        """
        # 简化实现：这里应该使用 JavaParser 解析类，提取方法
        # 目前假设由 Java 侧提供方法列表
        logger.warning("extract_from_class 需要 Java 侧支持来解析方法")
        return []
