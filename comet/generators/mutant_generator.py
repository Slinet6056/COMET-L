"""变异生成器"""

import json
import logging
from typing import List, Optional
from datetime import datetime

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
from ..models import Mutant, MutationPatch, Contract, Pattern
from ..knowledge.knowledge_base import KnowledgeBase
from ..utils.hash_utils import generate_id
from ..utils.code_utils import add_line_numbers

logger = logging.getLogger(__name__)


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
                    logger.info(f"成功生成足够的变异体: {len(all_mutants)}/{num_mutations}")
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
                data = json.loads(response)
                logger.debug(f"解析后的数据结构: {list(data.keys())}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析失败: {e}")
                logger.error(f"响应内容: {response}")
                return []

            mutations_data = data.get("mutations", [])
            logger.debug(f"提取到 {len(mutations_data) if isinstance(mutations_data, list) else 1} 个变异数据")
            if not isinstance(mutations_data, list):
                # 如果返回的是单个对象，转换为列表
                mutations_data = [mutations_data]

            # 创建 Mutant 对象
            mutants = []
            for idx, mut_data in enumerate(mutations_data):
                try:
                    logger.debug(f"处理变异 #{idx+1}: {mut_data.get('intent', 'Unknown')}")

                    # 验证必需字段
                    if not mut_data.get("original") or not mut_data.get("mutated"):
                        logger.warning(f"跳过变异 #{idx+1}: 缺少 original 或 mutated 字段")
                        logger.debug(f"变异数据: {mut_data}")
                        continue

                    patch = MutationPatch(
                        file_path="",  # 将由调用者设置
                        line_start=mut_data.get("line_start", 1),
                        line_end=mut_data.get("line_end", 1),
                        original_code=mut_data.get("original", ""),
                        mutated_code=mut_data.get("mutated", ""),
                    )

                    mutant = Mutant(
                        id=generate_id("mutant", f"{class_name}_{mut_data.get('intent', '')}"),
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

    def generate_targeted_mutants(
        self,
        class_name: str,
        class_code: str,
        target_lines: List[int],
        num_mutations: int = 3,
    ) -> List[Mutant]:
        """
        针对特定代码行生成变异体（用于覆盖缺口）

        Args:
            class_name: 类名
            class_code: 类代码
            target_lines: 目标行号列表
            num_mutations: 每个目标生成的变异体数量

        Returns:
            Mutant 对象列表
        """
        # 简化实现：生成通用变异体
        # 未来可以根据 target_lines 做更精准的变异
        return self.generate_mutants(class_name, class_code, num_mutations * len(target_lines))
