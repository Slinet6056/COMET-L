"""契约提取器"""

import json
import logging
from typing import Optional, List
from datetime import datetime

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
from ..models import Contract
from ..utils.hash_utils import generate_id
from ..utils.json_utils import extract_json_from_response
from ..executor.java_executor import JavaExecutor

logger = logging.getLogger(__name__)


class SpecExtractor:
    """契约提取器 - 从源代码中提取前置条件、后置条件和异常条件"""

    def __init__(
        self, llm_client: LLMClient, java_executor: Optional[JavaExecutor] = None
    ):
        """
        初始化契约提取器

        Args:
            llm_client: LLM 客户端
            java_executor: Java 执行器（用于代码解析）
        """
        self.llm = llm_client
        self.prompt_manager = PromptManager()
        self.java_executor = java_executor

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

            # 调用 LLM（使用配置文件的 temperature）
            response = self.llm.chat_with_system(
                system_prompt=system,
                user_prompt=user,
                response_format={"type": "json_object"},
            )

            # 解析响应
            cleaned_response = extract_json_from_response(response)
            data = json.loads(cleaned_response)

            # 提取方法名
            method_name = method_signature.split("(")[0].strip().split()[-1]

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
        file_path: str,
        class_name: Optional[str] = None,
        method_names: Optional[List[str]] = None,
    ) -> List[Contract]:
        """
        从类文件中提取所有 public 方法的契约

        Args:
            file_path: Java 源文件路径
            class_name: 类名（可选，如果不提供则从文件解析）
            method_names: 要提取的方法名列表（如果为 None 则提取所有 public 方法）

        Returns:
            Contract 对象列表
        """
        if not self.java_executor:
            logger.error("需要 JavaExecutor 来解析 Java 代码")
            return []

        try:
            # 使用 JavaExecutor 获取 public 方法列表
            methods_data = self.java_executor.get_public_methods(file_path)

            if not methods_data:
                logger.warning(f"无法从 {file_path} 提取方法")
                return []

            # 读取源代码
            with open(file_path, "r", encoding="utf-8") as f:
                source_code = f.read()

            contracts = []
            for method in methods_data:
                method_name = method.get("name")

                # 如果指定了方法名列表，只提取列表中的方法
                if method_names and method_name not in method_names:
                    continue

                # 提取契约
                contract = self.extract_from_method(
                    class_name=class_name or method.get("className", "Unknown"),
                    method_signature=method.get("signature", ""),
                    source_code=method.get("body", ""),
                    javadoc=method.get("javadoc"),
                )

                if contract:
                    contracts.append(contract)

            logger.info(f"从 {file_path} 提取了 {len(contracts)} 个方法契约")
            return contracts

        except Exception as e:
            logger.error(f"提取类契约失败: {e}")
            return []
