"""测试生成器"""

import logging
from typing import List, Optional, Dict, Any, Union
from datetime import datetime

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
from ..models import TestCase, TestMethod, Contract, Mutant
from ..knowledge.knowledge_base import KnowledgeBase, RAGKnowledgeBase
from ..utils.hash_utils import generate_id
from ..utils.code_utils import build_test_class, extract_imports, parse_java_class
from ..utils.parsers import (
    parse_test_method_response,
    parse_test_methods_response,
    parse_test_class_response,
    extract_test_method_name,
)

logger = logging.getLogger(__name__)


class TestGenerator:
    """测试生成器 - 生成 JUnit5 测试方法（支持 RAG 增强）"""

    def __init__(
        self,
        llm_client: LLMClient,
        knowledge_base: Union[KnowledgeBase, RAGKnowledgeBase],
    ):
        """
        初始化测试生成器

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
        method_name: str,
        method_signature: Optional[str] = None,
        source_code: Optional[str] = None,
    ) -> str:
        """
        获取 RAG 检索的上下文（如果启用）

        Args:
            class_name: 类名
            method_name: 方法名
            method_signature: 方法签名
            source_code: 源代码

        Returns:
            RAG 上下文文本，未启用时返回空字符串
        """
        if not self._is_rag_enabled:
            return ""

        try:
            context = self.kb.retrieve_for_test_generation(
                class_name, method_name, method_signature, source_code
            )
            if context:
                logger.debug(f"获取到 RAG 上下文: {len(context)} 字符")
            return context
        except Exception as e:
            logger.warning(f"获取 RAG 上下文失败: {e}")
            return ""

    def generate_tests_for_method(
        self,
        class_name: str,
        method_signature: str,
        class_code: str,
        survived_mutants: Optional[List[Mutant]] = None,
        coverage_gaps: Optional[Dict[str, Any]] = None,
        existing_tests: Optional[List[TestCase]] = None,
    ) -> Optional[TestCase]:
        """
        为方法生成测试（数量由 LLM 自主决定，支持 RAG 增强）

        Args:
            class_name: 类名
            method_signature: 方法签名
            class_code: 完整类代码
            survived_mutants: 幸存的变异体列表
            coverage_gaps: 覆盖缺口信息
            existing_tests: 现有测试用例列表（用于参考，避免重复）

        Returns:
            TestCase 对象，如果生成失败则返回 None
        """
        try:
            # 提取方法名
            method_name = method_signature.split("(")[0].strip().split()[-1]

            # 获取契约
            contracts = self.kb.get_contracts_for_method(class_name, method_name)
            contract = contracts[0] if contracts else None

            # 获取 RAG 上下文（如果启用）
            rag_context = self._get_rag_context(
                class_name, method_name, method_signature, class_code
            )

            # 渲染提示词
            system, user = self.prompt_manager.render_generate_test(
                class_name=class_name,
                method_signature=method_signature,
                class_code=class_code,
                contracts=contract,
                survived_mutants=survived_mutants or [],
                coverage_gaps=coverage_gaps or {},
                existing_tests=existing_tests or [],
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

            # 使用新的多方法解析器解析响应
            test_method_codes = parse_test_methods_response(response)

            if not test_method_codes:
                logger.warning(f"未能解析测试方法: {class_name}.{method_name}")
                return None

            # 创建 TestMethod 对象列表
            test_methods = []
            for idx, test_code in enumerate(test_method_codes):
                # 提取方法名
                test_method_name = extract_test_method_name(test_code)
                if not test_method_name:
                    # 如果无法提取方法名，使用默认名称
                    test_method_name = f"test_{method_name}_{idx + 1}"
                    logger.warning(
                        f"无法提取测试方法名，使用默认名称: {test_method_name}"
                    )

                test_method = TestMethod(
                    method_name=test_method_name,
                    code=test_code,
                    target_method=method_name,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                test_methods.append(test_method)
            logger.debug(f"成功创建测试方法: {test_method_name}")

            # 解析类信息
            class_info = parse_java_class(class_code)
            package_name = class_info.get("package")

            # 提取导入语句
            imports = extract_imports(class_code)

            # 构建完整测试类
            # 处理内部类：将 $ 替换为下划线，使测试类名合法
            # 命名规则：{class}_{method}Test
            # 例如：Calculator.add -> Calculator_addTest
            clean_class_name = class_name.replace("$", "_")
            test_class_name = f"{clean_class_name}_{method_name}Test"
            method_codes = [m.code for m in test_methods]

            full_code = build_test_class(
                test_class_name=test_class_name,
                target_class=class_name,
                package_name=package_name,
                imports=imports,
                test_methods=method_codes,
            )

            # 创建 TestCase 对象
            test_case = TestCase(
                id=generate_id("test", f"{class_name}_{method_name}"),
                class_name=test_class_name,
                target_class=class_name,
                package_name=package_name,
                imports=imports,
                methods=test_methods,
                full_code=full_code,
                compile_success=False,  # 需要验证
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

            logger.info(
                f"成功生成 {len(test_methods)} 个测试方法用于 {class_name}.{method_name}"
            )
            return test_case

        except Exception as e:
            logger.error(f"测试生成失败: {e}")
            return None

    def refine_tests(
        self,
        test_case: TestCase,
        class_code: str,
        target_method: Optional[str] = None,
        survived_mutants: Optional[List[Mutant]] = None,
        coverage_gaps: Optional[Dict[str, Any]] = None,
        evaluation_feedback: Optional[str] = None,
    ) -> Optional[TestCase]:
        """
        完善现有测试（改进、扩展或修正）- 针对单个测试方法优化

        Args:
            test_case: 现有测试用例
            class_code: 被测类的完整代码
            target_method: 目标方法名（指定重点优化的方法）
            survived_mutants: 幸存的变异体列表
            coverage_gaps: 覆盖缺口信息
            evaluation_feedback: 评估反馈信息

        Returns:
            完善后的 TestCase，如果失败则返回 None
        """
        logger.info(f"开始完善测试: {test_case.class_name}")
        if target_method:
            logger.info(f"目标方法: {target_method}")

        try:
            # 检查测试用例是否有方法
            if not test_case.methods:
                logger.error("测试用例没有方法，无法优化")
                return None

            # 渲染提示词（传入所有测试方法）
            system_prompt, user_prompt = self.prompt_manager.render_refine_test(
                test_case=test_case,
                class_code=class_code,
                target_method=target_method,
                survived_mutants=survived_mutants or [],
                coverage_gaps=coverage_gaps or {},
                evaluation_feedback=evaluation_feedback,
            )

            # 调用 LLM（不再使用 json_object 格式，使用配置文件的 temperature）
            response = self.llm.chat_with_system(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            # DEBUG: 记录原始响应
            logger.debug(f"LLM 原始响应: {response[:500]}...")

            # 使用新的多方法解析器解析响应
            refined_method_codes = parse_test_methods_response(response)

            if not refined_method_codes:
                logger.warning("未能解析优化后的测试方法")
                return None

            # 创建新的 TestMethod 列表（完全替换）
            test_methods = []
            for idx, refined_code in enumerate(refined_method_codes):
                # 提取方法名
                refined_method_name = extract_test_method_name(refined_code)
                if not refined_method_name:
                    # 如果无法提取方法名，使用默认名称
                    refined_method_name = f"test_{target_method or 'method'}_{idx + 1}"
                    logger.warning(
                        f"无法提取优化后的方法名，使用: {refined_method_name}"
                    )

                # 创建优化后的 TestMethod 对象
                refined_method = TestMethod(
                    method_name=refined_method_name,
                    code=refined_code,
                    target_method=target_method
                    or (
                        test_case.methods[0].target_method if test_case.methods else ""
                    ),
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                test_methods.append(refined_method)

            logger.info(f"完全替换测试方法，新方法数量: {len(test_methods)}")

            # 构建完整测试类
            method_codes = [m.code for m in test_methods]
            full_code = build_test_class(
                test_class_name=test_case.class_name,
                target_class=test_case.target_class,
                package_name=test_case.package_name,
                imports=test_case.imports,
                test_methods=method_codes,
            )

            # 更新 TestCase
            test_case.methods = test_methods
            test_case.full_code = full_code
            test_case.updated_at = datetime.now()

            logger.info(f"成功完善测试，现有 {len(test_methods)} 个测试方法")
            logger.debug(f"返回的测试用例: ID={test_case.id}")
            return test_case

        except Exception as e:
            logger.error(f"完善测试失败: {e}")
            return None

    def regenerate_with_feedback(
        self,
        test_case: TestCase,
        compile_error: str,
        class_code: str = "",
        max_retries: int = 3,
    ) -> Optional[TestCase]:
        """
        根据编译错误反馈重新生成测试

        Args:
            test_case: 原测试用例
            compile_error: 编译错误信息
            class_code: 被测类源代码
            max_retries: 最大重试次数

        Returns:
            修正后的 TestCase，如果失败则返回 None
        """
        logger.info(f"尝试修复编译错误: {test_case.class_name}")

        for attempt in range(max_retries):
            logger.debug(f"修复尝试 {attempt + 1}/{max_retries}")

            try:
                # 使用提示词管理器生成修复提示词
                system_prompt, user_prompt = self.prompt_manager.render_fix_test(
                    test_code=test_case.full_code or "",
                    compile_error=compile_error,
                    class_code=class_code,
                )

                # 调用 LLM 修复（不再使用 json_object 格式，使用配置文件的 temperature）
                response = self.llm.chat_with_system(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )

                # DEBUG: 记录原始响应
                logger.debug(f"LLM 原始响应: {response[:500]}...")

                # 使用新的解析器解析响应
                fixed_code = parse_test_class_response(response)

                if not fixed_code:
                    logger.warning(f"修复尝试 {attempt + 1} 失败: 未返回修复代码")
                    continue

                logger.info(f"修复尝试 {attempt + 1} 成功")

                # 更新测试用例
                test_case.full_code = fixed_code
                test_case.updated_at = datetime.now()

                return test_case

            except Exception as e:
                logger.error(f"修复尝试 {attempt + 1} 失败: {e}")
                continue

        logger.error(f"经过 {max_retries} 次尝试仍无法修复编译错误")
        return None

    def fix_single_method(
        self,
        method_name: str,
        method_code: str,
        class_code: str,
        error_message: str,
        max_retries: int = 3,
    ) -> Optional[str]:
        """
        修复单个测试方法

        Args:
            method_name: 测试方法名
            method_code: 方法代码
            class_code: 被测类代码
            error_message: 错误信息
            max_retries: 最大重试次数

        Returns:
            修复后的方法代码，如果失败则返回 None
        """
        logger.info(f"尝试修复单个测试方法: {method_name}")

        for attempt in range(max_retries):
            logger.debug(f"修复尝试 {attempt + 1}/{max_retries}")

            try:
                # 使用提示词管理器生成修复提示词
                system_prompt, user_prompt = (
                    self.prompt_manager.render_fix_single_method(
                        method_code=method_code,
                        class_code=class_code,
                        error_message=error_message,
                    )
                )

                # 调用 LLM（不再使用 json_object 格式，使用配置文件的 temperature）
                response = self.llm.chat_with_system(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )

                # DEBUG: 记录原始响应
                logger.debug(f"LLM 原始响应: {response[:500]}...")

                # 使用新的解析器解析响应
                fixed_code = parse_test_method_response(response)

                if not fixed_code:
                    logger.warning(f"修复尝试 {attempt + 1} 失败: 未返回修复代码")
                    continue

                logger.info(f"修复尝试 {attempt + 1} 成功")
                return fixed_code

            except Exception as e:
                logger.error(f"修复尝试 {attempt + 1} 失败: {e}")
                continue

        logger.error(f"经过 {max_retries} 次尝试仍无法修复方法 {method_name}")
        return None
