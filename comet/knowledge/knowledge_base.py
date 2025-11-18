"""知识库管理"""

import logging
from typing import List, Optional, Dict, Any

from ..models import Contract, Pattern
from ..store.knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """知识库管理类 - 管理 Patterns 和 Contracts"""

    def __init__(self, store: KnowledgeStore):
        """
        初始化知识库

        Args:
            store: 知识库存储实例
        """
        self.store = store
        self._contracts_cache: Dict[str, List[Contract]] = {}
        self._patterns_cache: Optional[List[Pattern]] = None

    def add_contract(self, contract: Contract) -> None:
        """
        添加契约到知识库

        Args:
            contract: 契约对象
        """
        self.store.save_contract(contract)
        # 清除缓存
        if contract.class_name in self._contracts_cache:
            del self._contracts_cache[contract.class_name]
        logger.info(f"添加契约: {contract.class_name}.{contract.method_name}")

    def get_contracts_for_class(self, class_name: str) -> List[Contract]:
        """
        获取类的所有契约

        Args:
            class_name: 类名

        Returns:
            契约列表
        """
        if class_name not in self._contracts_cache:
            self._contracts_cache[class_name] = self.store.get_contracts_by_class(class_name)
        return self._contracts_cache[class_name]

    def get_contracts_for_method(self, class_name: str, method_name: str) -> List[Contract]:
        """
        获取方法的契约

        Args:
            class_name: 类名
            method_name: 方法名

        Returns:
            契约列表
        """
        return self.store.get_contracts_by_method(class_name, method_name)

    def add_pattern(self, pattern: Pattern) -> None:
        """
        添加缺陷模式到知识库

        Args:
            pattern: 模式对象
        """
        self.store.save_pattern(pattern)
        # 清除缓存
        self._patterns_cache = None
        logger.info(f"添加模式: {pattern.name} ({pattern.category})")

    def get_all_patterns(self) -> List[Pattern]:
        """
        获取所有模式

        Returns:
            模式列表（按成功率和使用次数排序）
        """
        if self._patterns_cache is None:
            self._patterns_cache = self.store.get_all_patterns()
        return self._patterns_cache

    def get_patterns_by_category(self, category: str) -> List[Pattern]:
        """
        获取特定类别的模式

        Args:
            category: 模式类别

        Returns:
            模式列表
        """
        return self.store.get_patterns_by_category(category)

    def get_top_patterns(self, n: int = 5, min_confidence: float = 0.5) -> List[Pattern]:
        """
        获取最佳模式（根据成功率和置信度）

        Args:
            n: 返回数量
            min_confidence: 最小置信度

        Returns:
            模式列表
        """
        all_patterns = self.get_all_patterns()
        filtered = [p for p in all_patterns if p.confidence >= min_confidence]
        return filtered[:n]

    def update_pattern_usage(self, pattern_id: str, success: bool) -> None:
        """
        更新模式使用统计

        Args:
            pattern_id: 模式 ID
            success: 是否成功（发现了缺陷）
        """
        self.store.update_pattern_stats(pattern_id, success)
        # 清除缓存
        self._patterns_cache = None
        logger.debug(f"更新模式统计: {pattern_id}, 成功={success}")

    def get_relevant_patterns(self, class_code: str, max_patterns: int = 10) -> List[Pattern]:
        """
        获取与代码相关的模式（简化版：返回高成功率的模式）

        Args:
            class_code: 类代码
            max_patterns: 最大返回数量

        Returns:
            相关模式列表
        """
        # 简化版实现：返回成功率最高的模式
        # 未来可以基于代码内容做更智能的匹配
        return self.get_top_patterns(n=max_patterns)

    def get_stats(self) -> Dict[str, Any]:
        """
        获取知识库统计信息

        Returns:
            统计信息字典
        """
        all_contracts = self.store.get_all_contracts()
        all_patterns = self.get_all_patterns()

        return {
            "total_contracts": len(all_contracts),
            "total_patterns": len(all_patterns),
            "pattern_categories": len(set(p.category for p in all_patterns)),
            "avg_pattern_success_rate": (
                sum(p.success_rate for p in all_patterns) / len(all_patterns)
                if all_patterns else 0.0
            ),
        }

    def clear_cache(self) -> None:
        """清除缓存"""
        self._contracts_cache.clear()
        self._patterns_cache = None
