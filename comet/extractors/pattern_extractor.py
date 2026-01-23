"""模式提取器"""

import json
import logging
from typing import Optional
from datetime import datetime

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
from ..models import Pattern
from ..utils.hash_utils import generate_id
from ..utils.json_utils import extract_json_from_response

logger = logging.getLogger(__name__)


class PatternExtractor:
    """模式提取器 - 从 Bug 报告和代码修复中学习缺陷模式"""

    def __init__(self, llm_client: LLMClient):
        """
        初始化模式提取器

        Args:
            llm_client: LLM 客户端
        """
        self.llm = llm_client
        self.prompt_manager = PromptManager()

    def extract_from_bug_report(
        self,
        bug_description: Optional[str] = None,
        diff_patch: Optional[str] = None,
        before_code: Optional[str] = None,
        after_code: Optional[str] = None,
    ) -> Optional[Pattern]:
        """
        从 Bug 报告中提取缺陷模式

        Args:
            bug_description: Bug 描述文本
            diff_patch: Git diff 补丁
            before_code: 修复前代码
            after_code: 修复后代码

        Returns:
            Pattern 对象，如果提取失败则返回 None
        """
        if not any([bug_description, diff_patch, before_code, after_code]):
            logger.error("必须提供至少一种输入")
            return None

        try:
            # 渲染提示词
            system, user = self.prompt_manager.render_extract_pattern(
                bug_description=bug_description,
                diff_patch=diff_patch,
                before_code=before_code,
                after_code=after_code,
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

            # 创建 Pattern 对象
            pattern = Pattern(
                id=generate_id("pattern", data.get("name", "unknown")),
                name=data.get("name", "unknown"),
                category=data.get("category", "general"),
                description=data.get("description", ""),
                template=data.get("template", ""),
                examples=data.get("examples", []),
                mutation_strategy=data.get("mutation_strategy"),
                confidence=0.7,  # 从 Bug 报告学习的初始置信度
                success_rate=0.0,
                usage_count=0,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

            logger.info(f"成功提取模式: {pattern.name} ({pattern.category})")
            return pattern

        except Exception as e:
            logger.error(f"模式提取失败: {e}")
            return None

    def extract_from_surviving_mutant(
        self,
        mutant_code: str,
        original_code: str,
        semantic_intent: str,
    ) -> Optional[Pattern]:
        """
        从幸存变异体中学习新模式

        Args:
            mutant_code: 变异后代码
            original_code: 原始代码
            semantic_intent: 变异意图

        Returns:
            Pattern 对象，如果提取失败则返回 None
        """
        # 将幸存变异体视为发现的潜在缺陷
        return self.extract_from_bug_report(
            bug_description=f"幸存变异体揭示的潜在缺陷: {semantic_intent}",
            before_code=original_code,
            after_code=mutant_code,
        )
