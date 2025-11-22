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

    def select(self, criteria: str = "coverage", blacklist: Optional[set] = None) -> Dict[str, Any]:
        """
        根据策略选择目标

        Args:
            criteria: 选择策略（coverage/mutations/priority/random）
            blacklist: 黑名单（格式为"ClassName.methodName"）

        Returns:
            目标信息字典
        """
        if blacklist is None:
            blacklist = set()

        if criteria == "coverage":
            return self.select_by_coverage(blacklist)
        elif criteria == "mutations":
            return self.select_by_mutations(blacklist)
        elif criteria == "priority":
            return self.select_by_priority(blacklist)
        elif criteria == "random":
            return self.select_random(blacklist)
        else:
            logger.warning(f"未知策略: {criteria}，使用默认策略")
            return self.select_by_priority(blacklist)

    def select_by_coverage(self, blacklist: Optional[set] = None) -> Dict[str, Any]:
        """
        选择覆盖率最低的方法

        优先选择覆盖率低于 80% 的方法，如果所有方法都达标则选择覆盖率最低的

        Args:
            blacklist: 黑名单（格式为"ClassName.methodName"）

        Returns:
            目标信息字典
        """
        if blacklist is None:
            blacklist = set()

        # 尝试从数据库获取低覆盖率方法
        low_cov_methods = self.db.get_low_coverage_methods(threshold=0.8)

        if low_cov_methods:
            # 过滤黑名单，选择覆盖率最低的方法
            for selected in low_cov_methods:
                target_key = f"{selected.class_name}.{selected.method_name}"
                if target_key not in blacklist:
                    # 不在黑名单中，选择这个目标
                    logger.info(
                        f"选择目标（低覆盖率）: {selected.class_name}.{selected.method_name} "
                        f"(覆盖率: {selected.line_coverage_rate:.1%})"
                    )

                    # 获取方法签名
                    methods = self._get_public_methods(selected.class_name)
                    selected_method_info = None
                    method_signature = None

                    if methods:
                        for method in methods:
                            if isinstance(method, dict) and method.get("name") == selected.method_name:
                                selected_method_info = method
                                method_signature = method.get("signature")
                                break

                    return {
                        "class_name": selected.class_name,
                        "method_name": selected.method_name,
                        "method_signature": method_signature,
                        "method_info": selected_method_info,
                        "strategy": "coverage",
                        "coverage_rate": selected.line_coverage_rate,
                        "missed_lines": selected.missed_lines,
                    }

            logger.info("所有低覆盖率方法都在黑名单中")

        # 如果没有低覆盖率方法，尝试获取所有覆盖率数据
        all_coverage = self.db.get_all_method_coverage()

        if all_coverage:
            # 过滤黑名单，选择覆盖率最低的方法
            filtered = [c for c in all_coverage if f"{c.class_name}.{c.method_name}" not in blacklist]
            if not filtered:
                logger.warning("所有方法都在黑名单中，无法选择目标")
                return {"class_name": None, "method_name": None}

            selected = min(filtered, key=lambda x: x.line_coverage_rate)
            logger.info(
                f"选择目标（最低覆盖率）: {selected.class_name}.{selected.method_name} "
                f"(覆盖率: {selected.line_coverage_rate:.1%})"
            )

            # 获取方法签名
            methods = self._get_public_methods(selected.class_name)
            selected_method_info = None
            method_signature = None

            if methods:
                for method in methods:
                    if isinstance(method, dict) and method.get("name") == selected.method_name:
                        selected_method_info = method
                        method_signature = method.get("signature")
                        break

            return {
                "class_name": selected.class_name,
                "method_name": selected.method_name,
                "method_signature": method_signature,
                "method_info": selected_method_info,
                "strategy": "coverage",
                "coverage_rate": selected.line_coverage_rate,
                "missed_lines": selected.missed_lines,  # 行号列表
                "covered_lines": selected.covered_lines,  # 行号列表
            }

        # 如果没有覆盖率数据，回退到默认逻辑
        logger.info("没有覆盖率数据，使用默认选择策略")
        all_classes = self._get_all_classes()

        if not all_classes:
            logger.warning("未找到任何 Java 类")
            return {"class_name": None, "method_name": None}

        # 选择第一个类的第一个方法
        selected_class = all_classes[0]
        methods = self._get_public_methods(selected_class)
        selected_method_info = None
        method_name = None
        method_signature = None

        if methods and len(methods) > 0:
            selected_method_info = methods[0]
            method_name = selected_method_info.get("name") if isinstance(selected_method_info, dict) else selected_method_info
            method_signature = selected_method_info.get("signature") if isinstance(selected_method_info, dict) else None

        logger.info(f"选择目标（默认）: {selected_class}.{method_name}")
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
