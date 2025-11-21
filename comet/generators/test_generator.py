"""测试生成器"""

import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
from ..models import TestCase, TestMethod, Contract, Mutant
from ..knowledge.knowledge_base import KnowledgeBase
from ..utils.hash_utils import generate_id
from ..utils.code_utils import build_test_class, extract_imports, parse_java_class

logger = logging.getLogger(__name__)


class TestGenerator:
    """测试生成器 - 生成 JUnit5 测试方法"""

    def __init__(self, llm_client: LLMClient, knowledge_base: KnowledgeBase):
        """
        初始化测试生成器

        Args:
            llm_client: LLM 客户端
            knowledge_base: 知识库
        """
        self.llm = llm_client
        self.kb = knowledge_base
        self.prompt_manager = PromptManager()

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
        为方法生成测试（数量由 LLM 自主决定）

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
            method_name = method_signature.split('(')[0].strip().split()[-1]

            # 获取契约
            contracts = self.kb.get_contracts_for_method(class_name, method_name)
            contract = contracts[0] if contracts else None

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

            # 调用 LLM
            response = self.llm.chat_with_system(
                system_prompt=system,
                user_prompt=user,
                temperature=0.7,
                response_format={"type": "json_object"},
            )

            # DEBUG: 记录原始响应
            logger.debug(f"LLM 原始响应: {response[:500]}...")

            # 解析响应
            try:
                data = json.loads(response)
                logger.debug(f"解析后的数据结构: {list(data.keys())}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析失败: {e}")
                logger.error(f"响应内容: {response}")
                return None

            # 尝试多种 JSON 格式
            tests_data = data.get("tests", [])

            # 如果没有 "tests" 键，尝试其他可能的格式
            if not tests_data:
                # 检查是否直接返回了测试方法（单个对象）
                if "method_name" in data and "code" in data:
                    tests_data = [data]
                    logger.debug("检测到单个测试对象格式，已转换为列表")
                # 检查是否有 "test_methods" 键
                elif "test_methods" in data:
                    tests_data = data.get("test_methods", [])

            logger.debug(f"提取到 {len(tests_data) if isinstance(tests_data, list) else 1} 个测试数据")
            if not isinstance(tests_data, list):
                tests_data = [tests_data]

            # 创建 TestMethod 对象
            test_methods = []
            for idx, test_data in enumerate(tests_data):
                logger.debug(f"处理测试 #{idx+1}: {test_data.get('method_name', 'Unknown')}")

                # 验证必需字段
                if not test_data.get("code"):
                    logger.warning(f"跳过测试 #{idx+1}: 缺少 code 字段")
                    logger.debug(f"测试数据: {test_data}")
                    continue

                test_method = TestMethod(
                    method_name=test_data.get("method_name", f"test_{method_name}"),
                    code=test_data.get("code", ""),
                    target_method=method_name,
                    description=test_data.get("description"),
                    version=1,  # 新生成的方法，版本号为1
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                test_methods.append(test_method)
                logger.debug(f"成功创建测试 #{idx+1}")

            if not test_methods:
                logger.warning(f"未生成测试方法: {class_name}.{method_name}")
                logger.debug(f"原始数据包含 {len(tests_data)} 个测试，但都无效")
                return None

            # 解析类信息
            class_info = parse_java_class(class_code)
            package_name = class_info.get("package")

            # 提取导入语句
            imports = extract_imports(class_code)

            # 构建完整测试类
            test_class_name = f"{class_name}Test"
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

            logger.info(f"成功生成 {len(test_methods)} 个测试方法用于 {class_name}.{method_name}")
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
        完善现有测试（改进、扩展或修正）

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
            # 渲染提示词
            system_prompt, user_prompt = self.prompt_manager.render_refine_test(
                test_case=test_case,
                class_code=class_code,
                target_method=target_method,
                survived_mutants=survived_mutants or [],
                coverage_gaps=coverage_gaps or {},
                evaluation_feedback=evaluation_feedback,
            )

            # 调用 LLM
            response = self.llm.chat_with_system(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.6,
                response_format={"type": "json_object"},
            )

            # 解析响应
            data = json.loads(response)

            # 支持两种返回格式
            if "tests" in data:
                tests_data = data["tests"]
            elif "refined_tests" in data:
                tests_data = data["refined_tests"]
            else:
                logger.error("响应中未找到测试数据")
                return None

            if not isinstance(tests_data, list):
                tests_data = [tests_data]

            # 创建 TestMethod 对象
            test_methods = []
            for test_data in tests_data:
                if not test_data.get("code"):
                    continue

                test_method = TestMethod(
                    method_name=test_data.get("method_name", "test_method"),
                    code=test_data.get("code", ""),
                    target_method=test_data.get("target_method", ""),
                    description=test_data.get("description"),
                    version=1,  # 版本号将在保存时根据是否已存在自动更新
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                test_methods.append(test_method)

            if not test_methods:
                logger.warning("未生成有效的测试方法")
                return None

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
            logger.debug(f"返回的测试用例: ID={test_case.id}, version={test_case.version} (未更新版本号)")
            return test_case

        except Exception as e:
            logger.error(f"完善测试失败: {e}")
            return None

    def regenerate_with_feedback(
        self,
        test_case: TestCase,
        compile_error: str,
        max_retries: int = 3,
    ) -> Optional[TestCase]:
        """
        根据编译错误反馈重新生成测试

        Args:
            test_case: 原测试用例
            compile_error: 编译错误信息
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
                )

                # 调用 LLM 修复
                response = self.llm.chat_with_system(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.3,  # 较低温度以获得更确定的修复
                    response_format={"type": "json_object"},
                )

                # 解析响应
                data = json.loads(response)
                fixed_code = data.get("fixed_code")
                changes = data.get("changes", "未说明")

                if not fixed_code:
                    logger.warning(f"修复尝试 {attempt + 1} 失败: 未返回修复代码")
                    continue

                logger.info(f"修复说明: {changes}")

                # 更新测试用例
                test_case.full_code = fixed_code
                test_case.updated_at = datetime.now()

                return test_case

            except Exception as e:
                logger.error(f"修复尝试 {attempt + 1} 失败: {e}")
                continue

        logger.error(f"经过 {max_retries} 次尝试仍无法修复编译错误")
        return None
