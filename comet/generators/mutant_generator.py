"""变异生成器"""

import logging
from typing import List, Optional, Union
from datetime import datetime

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
from ..models import Mutant, MutationPatch, Contract, Pattern, TestCase
from ..knowledge.knowledge_base import KnowledgeBase, RAGKnowledgeBase
from ..utils.hash_utils import generate_id
from ..utils.code_utils import add_line_numbers
from ..utils.parsers import parse_mutation_response

logger = logging.getLogger(__name__)


class MutantGenerator:
    """变异生成器 - 基于 LLM 生成语义变异（支持 RAG 增强）"""

    def __init__(
        self,
        llm_client: LLMClient,
        knowledge_base: Union[KnowledgeBase, RAGKnowledgeBase],
    ):
        """
        初始化变异生成器

        Args:
            llm_client: LLM 客户端
            knowledge_base: 知识库（支持 RAG 增强）
        """
        self.llm = llm_client
        self.kb = knowledge_base
        self.prompt_manager = PromptManager()
        self._is_rag_enabled = isinstance(knowledge_base, RAGKnowledgeBase)

    def _get_rag_context(
        self,
        class_name: str,
        method_name: Optional[str] = None,
        source_code: Optional[str] = None,
    ) -> str:
        """
        获取 RAG 检索的上下文（如果启用）

        Args:
            class_name: 类名
            method_name: 方法名
            source_code: 源代码

        Returns:
            RAG 上下文文本，未启用时返回空字符串
        """
        if not self._is_rag_enabled:
            return ""

        try:
            context = self.kb.retrieve_for_mutation_generation(
                class_name, method_name or "", source_code
            )
            if context:
                logger.debug(f"获取到变异生成 RAG 上下文: {len(context)} 字符")
            return context
        except Exception as e:
            logger.warning(f"获取变异生成 RAG 上下文失败: {e}")
            return ""

    def generate_mutants(
        self,
        class_name: str,
        class_code: str,
        max_retries: int = 3,
        target_method: Optional[str] = None,
    ) -> List[Mutant]:
        """
        为类生成变异体（带重试机制）

        Args:
            class_name: 类名
            class_code: 类代码
            max_retries: 最大重试次数
            target_method: 目标方法名（如果指定，则只生成该方法的变异体）

        Returns:
            Mutant 对象列表（数量由 LLM 自主决定）
        """
        for attempt in range(max_retries):
            try:
                logger.info(f"生成变异体 (尝试 {attempt + 1}/{max_retries})...")
                mutants = self._generate_mutants_once(
                    class_name, class_code, target_method
                )

                if mutants:
                    logger.info(f"成功生成 {len(mutants)} 个变异体")
                    return mutants

                logger.warning(f"第 {attempt + 1} 次尝试未生成任何变异体")

            except Exception as e:
                logger.warning(f"第 {attempt + 1} 次生成变异体失败: {e}")
                if attempt == max_retries - 1:
                    logger.warning("已达最大重试次数，返回空列表")

        return []

    def _generate_mutants_once(
        self,
        class_name: str,
        class_code: str,
        target_method: Optional[str] = None,
    ) -> List[Mutant]:
        """
        单次生成变异体（内部方法，支持 RAG 增强）

        Args:
            class_name: 类名
            class_code: 类代码
            target_method: 目标方法名

        Returns:
            Mutant 对象列表
        """
        try:
            # 获取相关的契约和模式
            contracts = self.kb.get_contracts_for_class(class_name)
            patterns = self.kb.get_relevant_patterns(class_code, max_patterns=10)

            # 获取 RAG 上下文（如果启用）
            rag_context = self._get_rag_context(class_name, target_method, class_code)

            # 为代码添加行号
            code_with_lines = add_line_numbers(class_code)

            # 渲染提示词（不再传递 num_mutations）
            system, user = self.prompt_manager.render_generate_mutation(
                class_name=class_name,
                source_code_with_lines=code_with_lines,
                contracts=contracts,
                patterns=patterns,
                target_method=target_method,
            )

            # 如果有 RAG 上下文，将其添加到用户提示词前面
            if rag_context:
                user = f"## 相关知识（RAG 检索）\n\n{rag_context}\n\n---\n\n{user}"

            # 调用 LLM（不再使用 json_object 格式，使用配置文件的 temperature）
            response = self.llm.chat_with_system(
                system_prompt=system,
                user_prompt=user,
            )

            # DEBUG: 记录原始响应
            logger.debug(f"LLM 原始响应: {response[:500]}...")

            # 使用新的解析器解析响应
            mutations_data = parse_mutation_response(response)

            if not mutations_data:
                logger.warning("未能解析出任何变异体")
                return []

            # 创建 Mutant 对象
            mutants = []
            for idx, mut_data in enumerate(mutations_data):
                try:
                    logger.debug(f"处理变异 #{idx+1}")

                    patch = MutationPatch(
                        file_path="",  # 将由调用者设置
                        line_start=mut_data["line_start"],
                        line_end=mut_data["line_end"],
                        original_code=mut_data["original"],
                        mutated_code=mut_data["mutated"],
                    )

                    mutant = Mutant(
                        id=generate_id("mutant", f"{class_name}_{idx}"),
                        class_name=class_name,
                        method_name=target_method,
                        patch=patch,
                        status="pending",
                        created_at=datetime.now(),
                    )
                    mutants.append(mutant)
                    logger.debug(f"成功创建变异 #{idx+1}")
                except Exception as e:
                    logger.warning(f"跳过无效的变异数据 #{idx+1}: {e}")
                    logger.debug(f"变异数据: {mut_data}")

            logger.info(f"成功生成 {len(mutants)} 个变异体用于 {class_name}")
            return mutants

        except Exception as e:
            logger.warning(f"变异生成失败: {e}")
            return []

    def refine_mutants(
        self,
        class_name: str,
        class_code: str,
        existing_mutants: List[Mutant],
        test_cases: List[TestCase],
        kill_rate: float,
        target_method: Optional[str] = None,
        max_retries: int = 3,
    ) -> List[Mutant]:
        """
        基于现有测试生成更具针对性的变异体

        分析测试方法的断言和覆盖逻辑，找出测试盲区，
        生成针对测试弱点的变异体

        Args:
            class_name: 类名
            class_code: 类代码
            existing_mutants: 现有变异体列表
            test_cases: 测试用例列表
            kill_rate: 当前击杀率
            target_method: 目标方法名
            max_retries: 最大重试次数

        Returns:
            新的变异体列表（数量由 LLM 自主决定）
        """
        for attempt in range(max_retries):
            try:
                logger.info(f"完善变异体 (尝试 {attempt + 1}/{max_retries})...")
                mutants = self._refine_mutants_once(
                    class_name=class_name,
                    class_code=class_code,
                    existing_mutants=existing_mutants,
                    test_cases=test_cases,
                    kill_rate=kill_rate,
                    target_method=target_method,
                )

                if mutants:
                    logger.info(f"成功完善 {len(mutants)} 个变异体")
                    return mutants

                logger.warning(f"第 {attempt + 1} 次尝试未生成任何变异体")

            except Exception as e:
                logger.warning(f"第 {attempt + 1} 次完善变异体失败: {e}")
                if attempt == max_retries - 1:
                    logger.warning("已达最大重试次数，返回空列表")

        return []

    def _refine_mutants_once(
        self,
        class_name: str,
        class_code: str,
        existing_mutants: List[Mutant],
        test_cases: List[TestCase],
        kill_rate: float,
        target_method: Optional[str] = None,
    ) -> List[Mutant]:
        """
        单次完善变异体（内部方法，支持 RAG 增强）

        Args:
            class_name: 类名
            class_code: 类代码
            existing_mutants: 现有变异体列表
            test_cases: 测试用例列表
            kill_rate: 当前击杀率
            target_method: 目标方法名

        Returns:
            变异体列表
        """
        try:
            # 获取相关的契约和模式
            contracts = self.kb.get_contracts_for_class(class_name)
            patterns = self.kb.get_relevant_patterns(class_code, max_patterns=10)

            # 获取 RAG 上下文（如果启用）
            rag_context = self._get_rag_context(class_name, target_method, class_code)

            # 为代码添加行号
            code_with_lines = add_line_numbers(class_code)

            # 渲染提示词（不再传递 num_mutations）
            system, user = self.prompt_manager.render_refine_mutation(
                class_name=class_name,
                source_code_with_lines=code_with_lines,
                existing_mutants=existing_mutants,
                test_cases=test_cases,
                kill_rate=kill_rate,
                contracts=contracts,
                patterns=patterns,
                target_method=target_method,
            )

            # 如果有 RAG 上下文，将其添加到用户提示词前面
            if rag_context:
                user = f"## 相关知识（RAG 检索）\n\n{rag_context}\n\n---\n\n{user}"

            # 调用 LLM（不再使用 json_object 格式，使用配置文件的 temperature）
            response = self.llm.chat_with_system(
                system_prompt=system,
                user_prompt=user,
            )

            # DEBUG: 记录原始响应
            logger.debug(f"LLM 原始响应: {response[:500]}...")

            # 使用新的解析器解析响应
            mutations_data = parse_mutation_response(response)

            if not mutations_data:
                logger.warning("未能解析出任何变异体")
                return []

            # 创建 Mutant 对象
            mutants = []
            for idx, mut_data in enumerate(mutations_data):
                try:
                    logger.debug(f"处理变异 #{idx+1}")

                    patch = MutationPatch(
                        file_path="",  # 将由调用者设置
                        line_start=mut_data["line_start"],
                        line_end=mut_data["line_end"],
                        original_code=mut_data["original"],
                        mutated_code=mut_data["mutated"],
                    )

                    mutant = Mutant(
                        id=generate_id("mutant", f"{class_name}_refined_{idx}"),
                        class_name=class_name,
                        method_name=target_method,
                        patch=patch,
                        status="pending",
                        created_at=datetime.now(),
                    )
                    mutants.append(mutant)
                    logger.debug(f"成功创建变异 #{idx+1}")
                except Exception as e:
                    logger.warning(f"跳过无效的变异数据 #{idx+1}: {e}")
                    logger.debug(f"变异数据: {mut_data}")

            logger.info(f"成功完善 {len(mutants)} 个变异体用于 {class_name}")
            return mutants

        except Exception as e:
            logger.warning(f"变异完善失败: {e}")
            return []
