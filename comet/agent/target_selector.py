"""目标选择器 - 选择待测试的类和方法"""

import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

from ..executor.java_executor import JavaExecutor
from ..store.database import Database
from ..utils.project_utils import find_java_files, get_all_java_classes

logger = logging.getLogger(__name__)


class TargetSelector:
    """目标选择器 - 实现多种目标选择策略"""

    def __init__(
        self,
        project_path: str,
        java_executor: JavaExecutor,
        database: Database,
    ):
        """
        初始化目标选择器

        Args:
            project_path: 项目路径
            java_executor: Java 执行器
            database: 数据库
        """
        self.project_path = project_path
        self.java_executor = java_executor
        self.db = database
        self._class_cache: Optional[List[str]] = None

    def select(self, criteria: str = "coverage") -> Dict[str, Any]:
        """
        根据策略选择目标

        Args:
            criteria: 选择策略（coverage/mutations/priority/random）

        Returns:
            目标信息字典
        """
        if criteria == "coverage":
            return self.select_by_coverage()
        elif criteria == "mutations":
            return self.select_by_mutations()
        elif criteria == "priority":
            return self.select_by_priority()
        elif criteria == "random":
            return self.select_random()
        else:
            logger.warning(f"未知策略: {criteria}，使用默认策略")
            return self.select_by_priority()

    def select_by_coverage(self) -> Dict[str, Any]:
        """
        选择覆盖率最低的类

        Returns:
            目标信息字典
        """
        # 获取所有类
        all_classes = self._get_all_classes()

        if not all_classes:
            logger.warning("未找到任何 Java 类")
            return {"class_name": None, "method_name": None}

        # 简化实现：选择第一个类
        # 未来可以基于覆盖率数据做更智能的选择
        selected_class = all_classes[0]

        # 获取类的 public 方法
        methods = self._get_public_methods(selected_class)
        selected_method_info = None
        method_name = None
        method_signature = None

        if methods and len(methods) > 0:
            selected_method_info = methods[0]
            method_name = selected_method_info.get("name") if isinstance(selected_method_info, dict) else selected_method_info
            method_signature = selected_method_info.get("signature") if isinstance(selected_method_info, dict) else None

        logger.info(f"选择目标（按覆盖率）: {selected_class}.{method_name}")
        return {
            "class_name": selected_class,
            "method_name": method_name,
            "method_signature": method_signature,
            "method_info": selected_method_info if isinstance(selected_method_info, dict) else None,
            "strategy": "coverage",
        }

    def select_by_mutations(self) -> Dict[str, Any]:
        """
        选择变异体最少的类

        Returns:
            目标信息字典
        """
        all_classes = self._get_all_classes()

        if not all_classes:
            return {"class_name": None, "method_name": None}

        # 统计每个类的变异体数量
        mutant_counts = {}
        for class_name in all_classes:
            mutants = self.db.get_all_mutants()
            count = len([m for m in mutants if m.class_name == class_name])
            mutant_counts[class_name] = count

        # 选择变异体最少的类
        if not mutant_counts:
            selected_class = all_classes[0]
        else:
            selected_class = min(mutant_counts, key=lambda x: mutant_counts[x])

        methods = self._get_public_methods(selected_class)
        selected_method_info = None
        method_name = None
        method_signature = None

        if methods and len(methods) > 0:
            selected_method_info = methods[0]
            method_name = selected_method_info.get("name") if isinstance(selected_method_info, dict) else selected_method_info
            method_signature = selected_method_info.get("signature") if isinstance(selected_method_info, dict) else None

        logger.info(f"选择目标（按变异体数量）: {selected_class}.{method_name}")
        return {
            "class_name": selected_class,
            "method_name": method_name,
            "method_signature": method_signature,
            "method_info": selected_method_info if isinstance(selected_method_info, dict) else None,
            "strategy": "mutations",
        }

    def select_by_priority(self) -> Dict[str, Any]:
        """
        综合评分选择目标

        Returns:
            目标信息字典
        """
        all_classes = self._get_all_classes()

        if not all_classes:
            return {"class_name": None, "method_name": None}

        # 综合评分：优先选择变异体少、测试少的类
        class_scores = {}

        all_mutants = self.db.get_all_mutants()
        all_tests = self.db.get_all_test_cases()

        for class_name in all_classes:
            mutant_count = len([m for m in all_mutants if m.class_name == class_name])
            test_count = len([t for t in all_tests if t.target_class == class_name])

            # 分数越低越优先（缺少测试和变异体的类）
            score = mutant_count * 0.3 + test_count * 0.7
            class_scores[class_name] = score

        # 选择分数最低的类
        if not class_scores:
            selected_class = all_classes[0]
        else:
            selected_class = min(class_scores, key=lambda x: class_scores[x])

        methods = self._get_public_methods(selected_class)
        selected_method_info = None
        method_name = None
        method_signature = None

        if methods and len(methods) > 0:
            selected_method_info = methods[0]
            method_name = selected_method_info.get("name") if isinstance(selected_method_info, dict) else selected_method_info
            method_signature = selected_method_info.get("signature") if isinstance(selected_method_info, dict) else None

        logger.info(f"选择目标（综合评分）: {selected_class}.{method_name}")
        return {
            "class_name": selected_class,
            "method_name": method_name,
            "method_signature": method_signature,
            "method_info": selected_method_info if isinstance(selected_method_info, dict) else None,
            "strategy": "priority",
            "score": class_scores[selected_class],
        }

    def select_random(self) -> Dict[str, Any]:
        """
        随机选择目标

        Returns:
            目标信息字典
        """
        import random

        all_classes = self._get_all_classes()

        if not all_classes:
            return {"class_name": None, "method_name": None}

        selected_class = random.choice(all_classes)

        methods = self._get_public_methods(selected_class)
        selected_method_info = None
        method_name = None
        method_signature = None

        if methods and len(methods) > 0:
            selected_method_info = random.choice(methods)
            method_name = selected_method_info.get("name") if isinstance(selected_method_info, dict) else selected_method_info
            method_signature = selected_method_info.get("signature") if isinstance(selected_method_info, dict) else None

        logger.info(f"选择目标（随机）: {selected_class}.{method_name}")
        return {
            "class_name": selected_class,
            "method_name": method_name,
            "method_signature": method_signature,
            "method_info": selected_method_info if isinstance(selected_method_info, dict) else None,
            "strategy": "random",
        }

    def _get_all_classes(self) -> List[str]:
        """
        获取项目中所有的类名（缓存）

        Returns:
            类名列表
        """
        if self._class_cache is None:
            self._class_cache = get_all_java_classes(self.project_path)
            logger.info(f"找到 {len(self._class_cache)} 个 Java 类")
        return self._class_cache

    def _get_public_methods(self, class_name: str) -> List[str]:
        """
        获取类的所有 public 方法

        Args:
            class_name: 类名

        Returns:
            方法名列表
        """
        from ..utils.project_utils import find_java_file

        # 查找文件
        file_path = find_java_file(self.project_path, class_name)
        if not file_path:
            logger.warning(f"未找到类文件: {class_name}")
            return []

        # 使用 JavaExecutor 获取 public 方法
        try:
            methods = self.java_executor.get_public_methods(str(file_path))
            if methods:
                logger.debug(f"类 {class_name} 有 {len(methods)} 个 public 方法")
                return methods
        except Exception as e:
            logger.error(f"获取 public 方法失败: {e}")

        # 如果失败，返回空列表
        return []

    def clear_cache(self) -> None:
        """清除缓存"""
        self._class_cache = None
