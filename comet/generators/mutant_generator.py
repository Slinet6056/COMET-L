"""变异生成器"""

import json
import logging
from typing import List, Optional
from datetime import datetime

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
from ..models import Mutant, MutationPatch, Contract, Pattern, TestCase
from ..knowledge.knowledge_base import KnowledgeBase
from ..utils.hash_utils import generate_id
from ..utils.code_utils import add_line_numbers
from ..utils.json_utils import extract_json_from_response

logger = logging.getLogger(__name__)


def _normalize_code(code: str) -> str:
    """
    规范化代码字符串，将字面的转义字符转换为实际的字符

    Args:
        code: 可能包含字面转义字符的代码字符串

    Returns:
        规范化后的代码字符串
    """
    if not code:
        return code

    # 如果代码中包含字面的 \n（反斜杠+n），但没有实际的换行符，进行转换
    # 这种情况发生在 JSON 中存储了字面的 "\\n" 字符串
    if "\\n" in code:
        # 检查是否包含实际的换行符（排除字面的 \n）
        has_actual_newline = "\n" in code.replace("\\n", "")
        if not has_actual_newline:
            # 使用 Python 的字符串转义处理
            try:
                code = code.encode().decode("unicode_escape")
            except (UnicodeDecodeError, UnicodeEncodeError):
                # 如果 unicode_escape 失败，直接替换
                code = code.replace("\\n", "\n")
        else:
            # 如果既有字面的 \n 又有实际的换行符，只替换字面的 \n
            code = code.replace("\\n", "\n")

    return code


class MutantGenerator:
    """变异生成器 - 基于 LLM 生成语义变异"""

    def __init__(self, llm_client: LLMClient, knowledge_base: KnowledgeBase):
        """
        初始化变异生成器

        Args:
            llm_client: LLM 客户端
            knowledge_base: 知识库
        """
        self.llm = llm_client
        self.kb = knowledge_base
        self.prompt_manager = PromptManager()

    def generate_mutants(
        self,
        class_name: str,
        class_code: str,
        num_mutations: int = 5,
        max_retries: int = 3,
        target_method: Optional[str] = None,
    ) -> List[Mutant]:
        """
        为类生成变异体（带重试机制）

        Args:
            class_name: 类名
            class_code: 类代码
            num_mutations: 要生成的变异体数量
            max_retries: 最大重试次数
            target_method: 目标方法名（如果指定，则只生成该方法的变异体）

        Returns:
            Mutant 对象列表
        """
        all_mutants = []

        for attempt in range(max_retries):
            try:
                logger.info(f"生成变异体 (尝试 {attempt + 1}/{max_retries})...")
                mutants = self._generate_mutants_once(
                    class_name, class_code, num_mutations, target_method
                )
                all_mutants.extend(mutants)

                if len(all_mutants) >= num_mutations:
                    logger.info(
                        f"成功生成足够的变异体: {len(all_mutants)}/{num_mutations}"
                    )
                    return all_mutants[:num_mutations]

                logger.warning(
                    f"第 {attempt + 1} 次尝试仅生成 {len(mutants)} 个变异体, "
                    f"累计 {len(all_mutants)}/{num_mutations}"
                )

            except Exception as e:
                logger.error(f"第 {attempt + 1} 次生成变异体失败: {e}")
                if attempt == max_retries - 1:
                    logger.error("已达最大重试次数，返回已生成的变异体")

        return all_mutants

    def _generate_mutants_once(
        self,
        class_name: str,
        class_code: str,
        num_mutations: int,
        target_method: Optional[str] = None,
    ) -> List[Mutant]:
        """
        单次生成变异体（内部方法）

        Args:
            class_name: 类名
            class_code: 类代码
            num_mutations: 要生成的变异体数量
            target_method: 目标方法名

        Returns:
            Mutant 对象列表
        """
        try:
            # 获取相关的契约和模式
            contracts = self.kb.get_contracts_for_class(class_name)
            patterns = self.kb.get_relevant_patterns(class_code, max_patterns=10)

            # 为代码添加行号
            code_with_lines = add_line_numbers(class_code)

            # 渲染提示词（传递目标方法）
            system, user = self.prompt_manager.render_generate_mutation(
                class_name=class_name,
                source_code_with_lines=code_with_lines,
                contracts=contracts,
                patterns=patterns,
                num_mutations=num_mutations,
                target_method=target_method,  # 传递目标方法
            )

            # 调用 LLM
            response = self.llm.chat_with_system(
                system_prompt=system,
                user_prompt=user,
                temperature=0.8,  # 较高温度以增加多样性
                response_format={"type": "json_object"},
            )

            # DEBUG: 记录原始响应
            logger.debug(f"LLM 原始响应: {response[:500]}...")

            # 解析响应
            try:
                # 先清理响应，去除可能的代码块标记
                cleaned_response = extract_json_from_response(response)
                data = json.loads(cleaned_response)
                logger.debug(f"解析后的数据结构: {list(data.keys())}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析失败: {e}")
                logger.error(f"响应内容: {response}")
                return []

            mutations_data = data.get("mutations", [])
            logger.debug(
                f"提取到 {len(mutations_data) if isinstance(mutations_data, list) else 1} 个变异数据"
            )
            if not isinstance(mutations_data, list):
                # 如果返回的是单个对象，转换为列表
                mutations_data = [mutations_data]

            # 创建 Mutant 对象
            mutants = []
            for idx, mut_data in enumerate(mutations_data):
                try:
                    logger.debug(
                        f"处理变异 #{idx+1}: {mut_data.get('intent', 'Unknown')}"
                    )

                    # 验证必需字段
                    if not mut_data.get("original") or not mut_data.get("mutated"):
                        logger.warning(
                            f"跳过变异 #{idx+1}: 缺少 original 或 mutated 字段"
                        )
                        logger.debug(f"变异数据: {mut_data}")
                        continue

                    # 处理转义字符：将字面的 \n 转换为实际的换行符
                    original_code = _normalize_code(mut_data.get("original", ""))
                    mutated_code = _normalize_code(mut_data.get("mutated", ""))

                    patch = MutationPatch(
                        file_path="",  # 将由调用者设置
                        line_start=mut_data.get("line_start", 1),
                        line_end=mut_data.get("line_end", 1),
                        original_code=original_code,
                        mutated_code=mutated_code,
                    )

                    mutant = Mutant(
                        id=generate_id(
                            "mutant", f"{class_name}_{mut_data.get('intent', '')}"
                        ),
                        class_name=class_name,
                        method_name=target_method,  # 使用传入的目标方法名
                        patch=patch,
                        semantic_intent=mut_data.get("intent", "Unknown intent"),
                        pattern_id=mut_data.get("pattern_id"),
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
            logger.error(f"变异生成失败: {e}")
            return []

    def refine_mutants(
        self,
        class_name: str,
        class_code: str,
        existing_mutants: List[Mutant],
        test_cases: List[TestCase],
        kill_rate: float,
        target_method: Optional[str] = None,
        num_mutations: int = 5,
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
            num_mutations: 要生成的变异体数量
            max_retries: 最大重试次数

        Returns:
            新的变异体列表
        """
        all_mutants = []

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
                    num_mutations=num_mutations,
                )
                all_mutants.extend(mutants)

                if len(all_mutants) >= num_mutations:
                    logger.info(
                        f"成功完善足够的变异体: {len(all_mutants)}/{num_mutations}"
                    )
                    return all_mutants[:num_mutations]

                logger.warning(
                    f"第 {attempt + 1} 次尝试仅生成 {len(mutants)} 个变异体, "
                    f"累计 {len(all_mutants)}/{num_mutations}"
                )

            except Exception as e:
                logger.error(f"第 {attempt + 1} 次完善变异体失败: {e}")
                if attempt == max_retries - 1:
                    logger.error("已达最大重试次数，返回已生成的变异体")

        return all_mutants

    def _refine_mutants_once(
        self,
        class_name: str,
        class_code: str,
        existing_mutants: List[Mutant],
        test_cases: List[TestCase],
        kill_rate: float,
        target_method: Optional[str] = None,
        num_mutations: int = 5,
    ) -> List[Mutant]:
        """
        单次完善变异体（内部方法）

        Args:
            class_name: 类名
            class_code: 类代码
            existing_mutants: 现有变异体列表
            test_cases: 测试用例列表
            kill_rate: 当前击杀率
            target_method: 目标方法名
            num_mutations: 要生成的变异体数量

        Returns:
            变异体列表
        """
        try:
            # 获取相关的契约和模式
            contracts = self.kb.get_contracts_for_class(class_name)
            patterns = self.kb.get_relevant_patterns(class_code, max_patterns=10)

            # 为代码添加行号
            code_with_lines = add_line_numbers(class_code)

            # 渲染提示词
            system, user = self.prompt_manager.render_refine_mutation(
                class_name=class_name,
                source_code_with_lines=code_with_lines,
                existing_mutants=existing_mutants,
                test_cases=test_cases,
                kill_rate=kill_rate,
                contracts=contracts,
                patterns=patterns,
                target_method=target_method,
                num_mutations=num_mutations,
            )

            # 调用 LLM
            response = self.llm.chat_with_system(
                system_prompt=system,
                user_prompt=user,
                temperature=0.8,  # 较高温度以增加多样性
                response_format={"type": "json_object"},
            )

            # DEBUG: 记录原始响应
            logger.debug(f"LLM 原始响应: {response[:500]}...")

            # 解析响应
            try:
                # 先清理响应，去除可能的代码块标记
                cleaned_response = extract_json_from_response(response)
                data = json.loads(cleaned_response)
                logger.debug(f"解析后的数据结构: {list(data.keys())}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析失败: {e}")
                logger.error(f"响应内容: {response}")
                return []

            mutations_data = data.get("mutations", [])
            logger.debug(
                f"提取到 {len(mutations_data) if isinstance(mutations_data, list) else 1} 个变异数据"
            )
            if not isinstance(mutations_data, list):
                mutations_data = [mutations_data]

            # 创建 Mutant 对象
            mutants = []
            for idx, mut_data in enumerate(mutations_data):
                try:
                    logger.debug(
                        f"处理变异 #{idx+1}: {mut_data.get('intent', 'Unknown')}"
                    )

                    # 验证必需字段
                    if not mut_data.get("original") or not mut_data.get("mutated"):
                        logger.warning(
                            f"跳过变异 #{idx+1}: 缺少 original 或 mutated 字段"
                        )
                        logger.debug(f"变异数据: {mut_data}")
                        continue

                    # 处理转义字符：将字面的 \n 转换为实际的换行符
                    original_code = _normalize_code(mut_data.get("original", ""))
                    mutated_code = _normalize_code(mut_data.get("mutated", ""))

                    patch = MutationPatch(
                        file_path="",  # 将由调用者设置
                        line_start=mut_data.get("line_start", 1),
                        line_end=mut_data.get("line_end", 1),
                        original_code=original_code,
                        mutated_code=mutated_code,
                    )

                    mutant = Mutant(
                        id=generate_id(
                            "mutant", f"{class_name}_{mut_data.get('intent', '')}"
                        ),
                        class_name=class_name,
                        method_name=target_method,  # 使用传入的目标方法名
                        patch=patch,
                        semantic_intent=mut_data.get("intent", "Unknown intent"),
                        pattern_id=mut_data.get("pattern_id"),
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
            logger.error(f"变异完善失败: {e}")
            return []
