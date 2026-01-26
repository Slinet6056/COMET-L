"""目标选择器 - 选择待测试的类和方法"""

import logging
from typing import Dict, Any, List, Optional, Tuple
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
        min_method_lines: int = 5,
    ):
        """
        初始化目标选择器

        Args:
            project_path: 项目路径
            java_executor: Java 执行器
            database: 数据库
            min_method_lines: 目标方法的最小行数，默认为5
        """
        self.project_path = project_path
        self.java_executor = java_executor
        self.db = database
        self.min_method_lines = min_method_lines
        self._class_cache: Optional[List[str]] = None

    def select(
        self,
        criteria: str = "coverage",
        blacklist: Optional[set] = None,
        processed_targets: Optional[set] = None,
    ) -> Dict[str, Any]:
        """
        根据策略选择目标

        Args:
            criteria: 选择策略（coverage/killrate）
            blacklist: 黑名单（格式为"ClassName.methodName"）
            processed_targets: 已处理目标列表（格式为"ClassName.methodName"）

        Returns:
            目标信息字典
        """
        if blacklist is None:
            blacklist = set()
        if processed_targets is None:
            processed_targets = set()

        if criteria == "coverage":
            return self.select_by_coverage(blacklist, processed_targets)
        elif criteria == "killrate":
            return self.select_by_killrate(blacklist, processed_targets)
        elif criteria == "mutations":
            return self.select_by_mutations(blacklist, processed_targets)
        elif criteria == "priority":
            return self.select_by_priority(blacklist, processed_targets)
        elif criteria == "random":
            return self.select_random(blacklist, processed_targets)
        else:
            logger.warning(f"未知策略: {criteria}，使用默认策略")
            return self.select_by_priority(blacklist, processed_targets)

    def select_by_coverage(
        self, blacklist: Optional[set] = None, processed_targets: Optional[set] = None
    ) -> Dict[str, Any]:
        """
        选择覆盖率最低的方法

        优先选择覆盖率低于 80% 的方法，如果所有方法都达标则选择覆盖率最低的

        Args:
            blacklist: 黑名单（格式为"ClassName.methodName"）
            processed_targets: 已处理目标列表（格式为"ClassName.methodName"）

        Returns:
            目标信息字典
        """
        if blacklist is None:
            blacklist = set()
        if processed_targets is None:
            processed_targets = set()

        # 尝试从数据库获取低覆盖率方法
        low_cov_methods = self.db.get_low_coverage_methods(threshold=0.8)

        if low_cov_methods:
            # 优先选择未处理的目标
            unprocessed_selected = None
            processed_selected = None

            for selected in low_cov_methods:
                target_key = (
                    f"{selected.class_name}.{selected.method_name}"
                    if selected.method_name
                    else selected.class_name
                )
                if target_key in blacklist:
                    continue

                if target_key not in processed_targets:
                    # 找到未处理的目标，立即使用
                    unprocessed_selected = selected
                    break
                elif processed_selected is None:
                    # 记录第一个已处理但可用的目标
                    processed_selected = selected

            # 优先使用未处理的目标
            selected = unprocessed_selected or processed_selected

            if selected:
                target_key = (
                    f"{selected.class_name}.{selected.method_name}"
                    if selected.method_name
                    else selected.class_name
                )
                is_processed = target_key in processed_targets

                # 记录选择日志
                if is_processed:
                    logger.warning(
                        f"选择目标（低覆盖率，已处理）: {selected.class_name}.{selected.method_name} "
                        f"(覆盖率: {selected.line_coverage_rate:.1%})"
                    )
                else:
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
                        if (
                            isinstance(method, dict)
                            and method.get("name") == selected.method_name
                        ):
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

            logger.info("所有低覆盖率方法都在黑名单中或已处理")

        # 如果没有低覆盖率方法，尝试获取所有覆盖率数据
        all_coverage = self.db.get_all_method_coverage()

        if all_coverage:
            # 过滤黑名单
            filtered = [
                c
                for c in all_coverage
                if f"{c.class_name}.{c.method_name}" not in blacklist
            ]
            if not filtered:
                logger.warning("所有方法都在黑名单中，无法选择目标")
                return {"class_name": None, "method_name": None}

            # 优先选择未处理的目标
            unprocessed = [
                c
                for c in filtered
                if f"{c.class_name}.{c.method_name}" not in processed_targets
            ]

            if unprocessed:
                selected = min(unprocessed, key=lambda x: x.line_coverage_rate)
                logger.info(
                    f"选择目标（最低覆盖率）: {selected.class_name}.{selected.method_name} "
                    f"(覆盖率: {selected.line_coverage_rate:.1%})"
                )
            else:
                # 所有目标都已处理，选择已处理中覆盖率最低的
                selected = min(filtered, key=lambda x: x.line_coverage_rate)
                logger.warning(
                    f"选择目标（最低覆盖率，已处理）: {selected.class_name}.{selected.method_name} "
                    f"(覆盖率: {selected.line_coverage_rate:.1%})"
                )

            # 获取方法签名
            methods = self._get_public_methods(selected.class_name)
            selected_method_info = None
            method_signature = None

            if methods:
                for method in methods:
                    if (
                        isinstance(method, dict)
                        and method.get("name") == selected.method_name
                    ):
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

        # 遍历类，选择第一个有可用方法且不在黑名单中的目标
        for selected_class in all_classes:
            methods = self._get_public_methods(selected_class)
            if not methods:
                continue

            for method in methods:
                method_name = method.get("name") if isinstance(method, dict) else method
                target_key = f"{selected_class}.{method_name}"

                # 检查黑名单
                if target_key in blacklist:
                    continue

                # 优先选择未处理的目标
                if target_key in processed_targets:
                    continue

                method_signature = (
                    method.get("signature") if isinstance(method, dict) else None
                )

                logger.info(f"选择目标（默认）: {selected_class}.{method_name}")
                return {
                    "class_name": selected_class,
                    "method_name": method_name,
                    "method_signature": method_signature,
                    "method_info": method if isinstance(method, dict) else None,
                    "strategy": "coverage",
                }

        # 如果所有未处理目标都在黑名单中，尝试选择已处理但不在黑名单的目标
        for selected_class in all_classes:
            methods = self._get_public_methods(selected_class)
            if not methods:
                continue

            for method in methods:
                method_name = method.get("name") if isinstance(method, dict) else method
                target_key = f"{selected_class}.{method_name}"

                # 只检查黑名单，允许已处理的目标
                if target_key in blacklist:
                    continue

                method_signature = (
                    method.get("signature") if isinstance(method, dict) else None
                )

                logger.info(f"选择目标（默认，已处理）: {selected_class}.{method_name}")
                return {
                    "class_name": selected_class,
                    "method_name": method_name,
                    "method_signature": method_signature,
                    "method_info": method if isinstance(method, dict) else None,
                    "strategy": "coverage",
                }

        # 如果所有类都没有可用方法，返回 None
        logger.warning(
            "所有类都没有符合条件的方法（可能都在黑名单中或被最小行数配置过滤掉了）"
        )
        return {"class_name": None, "method_name": None}

    def select_by_mutations(
        self, blacklist: Optional[set] = None, processed_targets: Optional[set] = None
    ) -> Dict[str, Any]:
        """
        选择变异体最少的类

        Args:
            blacklist: 黑名单（格式为"ClassName.methodName"）
            processed_targets: 已处理目标列表（格式为"ClassName.methodName"）

        Returns:
            目标信息字典
        """
        if blacklist is None:
            blacklist = set()
        if processed_targets is None:
            processed_targets = set()

        all_classes = self._get_all_classes()

        if not all_classes:
            return {"class_name": None, "method_name": None}

        # 统计每个类的变异体数量
        mutant_counts = {}
        all_mutants = self.db.get_all_mutants()
        for class_name in all_classes:
            count = len([m for m in all_mutants if m.class_name == class_name])
            mutant_counts[class_name] = count

        # 按变异体数量升序遍历，找到不在黑名单的目标
        sorted_classes = sorted(
            all_classes, key=lambda x: mutant_counts.get(x, float("inf"))
        )

        # 先尝试找未处理的目标
        for candidate_class in sorted_classes:
            method_info, method_name, method_signature = (
                self._get_first_available_method(
                    candidate_class,
                    blacklist,
                    processed_targets,
                    prefer_unprocessed=True,
                )
            )
            if method_name is not None:
                target_key = f"{candidate_class}.{method_name}"
                is_processed = target_key in processed_targets

                if is_processed:
                    logger.warning(
                        f"选择目标（按变异体数量，已处理）: {candidate_class}.{method_name}"
                    )
                else:
                    logger.info(
                        f"选择目标（按变异体数量）: {candidate_class}.{method_name}"
                    )

                return {
                    "class_name": candidate_class,
                    "method_name": method_name,
                    "method_signature": method_signature,
                    "method_info": method_info,
                    "strategy": "mutations",
                }

        logger.warning("按变异体数量未找到可用目标（可能全部在黑名单或无 public 方法）")
        return {"class_name": None, "method_name": None}

    def select_by_priority(
        self, blacklist: Optional[set] = None, processed_targets: Optional[set] = None
    ) -> Dict[str, Any]:
        """
        综合评分选择目标

        Args:
            blacklist: 黑名单（格式为"ClassName.methodName"）
            processed_targets: 已处理目标列表（格式为"ClassName.methodName"）

        Returns:
            目标信息字典
        """
        if blacklist is None:
            blacklist = set()
        if processed_targets is None:
            processed_targets = set()

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

        # 按分数升序遍历，找到不在黑名单的目标
        sorted_classes = sorted(
            all_classes, key=lambda x: class_scores.get(x, float("inf"))
        )

        for candidate_class in sorted_classes:
            method_info, method_name, method_signature = (
                self._get_first_available_method(
                    candidate_class,
                    blacklist,
                    processed_targets,
                    prefer_unprocessed=True,
                )
            )
            if method_name is not None:
                target_key = f"{candidate_class}.{method_name}"
                is_processed = target_key in processed_targets

                if is_processed:
                    logger.warning(
                        f"选择目标（综合评分，已处理）: {candidate_class}.{method_name}"
                    )
                else:
                    logger.info(
                        f"选择目标（综合评分）: {candidate_class}.{method_name}"
                    )

                return {
                    "class_name": candidate_class,
                    "method_name": method_name,
                    "method_signature": method_signature,
                    "method_info": method_info,
                    "strategy": "priority",
                    "score": class_scores.get(candidate_class),
                }

        logger.warning("综合评分未找到可用目标（可能全部在黑名单或无 public 方法）")
        return {"class_name": None, "method_name": None}

    def select_random(
        self, blacklist: Optional[set] = None, processed_targets: Optional[set] = None
    ) -> Dict[str, Any]:
        """
        随机选择目标

        Args:
            blacklist: 黑名单（格式为"ClassName.methodName"）
            processed_targets: 已处理目标列表（格式为"ClassName.methodName"）

        Returns:
            目标信息字典
        """
        if blacklist is None:
            blacklist = set()
        if processed_targets is None:
            processed_targets = set()

        import random

        all_classes = self._get_all_classes()

        if not all_classes:
            return {"class_name": None, "method_name": None}

        # 过滤出有可用方法且不在黑名单的类
        unprocessed_targets = []
        processed_targets_list = []

        for class_name in all_classes:
            method_info, method_name, method_signature = (
                self._get_first_available_method(
                    class_name,
                    blacklist,
                    processed_targets,
                    prefer_unprocessed=False,
                    allow_first_only=False,
                )
            )
            if method_name is not None:
                target_key = f"{class_name}.{method_name}"
                target_tuple = (class_name, method_info, method_name, method_signature)

                if target_key in processed_targets:
                    processed_targets_list.append(target_tuple)
                else:
                    unprocessed_targets.append(target_tuple)

        # 优先从未处理的目标中随机选择
        if unprocessed_targets:
            selected_class, selected_method_info, method_name, method_signature = (
                random.choice(unprocessed_targets)
            )
            logger.info(f"选择目标（随机）: {selected_class}.{method_name}")
        elif processed_targets_list:
            selected_class, selected_method_info, method_name, method_signature = (
                random.choice(processed_targets_list)
            )
            logger.warning(f"选择目标（随机，已处理）: {selected_class}.{method_name}")
        else:
            logger.warning("随机选择未找到可用目标（可能全部在黑名单或无 public 方法）")
            return {"class_name": None, "method_name": None}

        return {
            "class_name": selected_class,
            "method_name": method_name,
            "method_signature": method_signature,
            "method_info": selected_method_info,
            "strategy": "random",
        }

    def select_by_killrate(
        self, blacklist: Optional[set] = None, processed_targets: Optional[set] = None
    ) -> Dict[str, Any]:
        """
        按杀死率选择目标（优先选择杀死率低的方法）

        选择有较多幸存变异体的方法，这些方法的测试质量需要改进。

        Args:
            blacklist: 黑名单（格式为"ClassName.methodName"）
            processed_targets: 已处理目标列表（格式为"ClassName.methodName"）

        Returns:
            目标信息字典
        """
        if blacklist is None:
            blacklist = set()
        if processed_targets is None:
            processed_targets = set()

        # 获取所有方法的变异体统计信息
        stats = self.db.get_method_mutant_stats()

        if not stats:
            logger.info("没有变异体统计数据，使用默认选择策略")
            # 如果没有变异体数据，回退到 coverage 策略
            return self.select_by_coverage(blacklist, processed_targets)

        # 过滤黑名单，只保留有变异体的方法
        candidates = []
        for key, stat in stats.items():
            if key in blacklist:
                continue

            # 只选择有变异体的方法（至少有1个变异体）
            if stat["total"] > 0:
                candidates.append((key, stat))

        if not candidates:
            logger.warning("所有有变异体的方法都在黑名单中")
            return {"class_name": None, "method_name": None}

        # 按杀死率升序排序（杀死率低的优先）
        candidates.sort(key=lambda x: x[1]["killrate"])

        # 优先选择未处理的目标
        unprocessed_candidates = [
            (k, s) for k, s in candidates if k not in processed_targets
        ]

        if unprocessed_candidates:
            selected_key, selected_stat = unprocessed_candidates[0]
            is_processed = False
        else:
            # 所有候选都已处理，选择已处理中杀死率最低的
            selected_key, selected_stat = candidates[0]
            is_processed = True
        class_name = selected_stat["class_name"]
        method_name = selected_stat["method_name"]

        if is_processed:
            logger.warning(
                f"选择目标（按杀死率，已处理）: {class_name}.{method_name} "
                f"(杀死率: {selected_stat['killrate']:.1%}, "
                f"已杀死: {selected_stat['killed']}/{selected_stat['total']}, "
                f"幸存: {selected_stat['survived']})"
            )
        else:
            logger.info(
                f"选择目标（按杀死率）: {class_name}.{method_name} "
                f"(杀死率: {selected_stat['killrate']:.1%}, "
                f"已杀死: {selected_stat['killed']}/{selected_stat['total']}, "
                f"幸存: {selected_stat['survived']})"
            )

        # 获取方法签名
        methods = self._get_public_methods(class_name)
        selected_method_info = None
        method_signature = None

        if methods:
            for method in methods:
                if isinstance(method, dict) and method.get("name") == method_name:
                    selected_method_info = method
                    method_signature = method.get("signature")
                    break

        return {
            "class_name": class_name,
            "method_name": method_name,
            "method_signature": method_signature,
            "method_info": selected_method_info,
            "strategy": "killrate",
            "killrate": selected_stat["killrate"],
            "killed_mutants": selected_stat["killed"],
            "total_mutants": selected_stat["total"],
            "survived_mutants": selected_stat["survived"],
        }

    def _get_all_classes(self) -> List[str]:
        """
        获取项目中所有的类名（缓存，排除接口）

        Returns:
            类名列表（不包括接口）
        """
        if self._class_cache is None:
            # 传入数据库对象，以便获取所有类名（包括同一文件中的多个类）
            all_classes = get_all_java_classes(self.project_path, db=self.db)

            # 过滤掉接口（接口没有实现代码，无法生成测试和变异体）
            if self.db:
                filtered_classes = []
                for class_name in all_classes:
                    mappings = self.db.get_all_class_mappings()
                    is_interface = False
                    for mapping in mappings:
                        if mapping.get("simple_name") == class_name and mapping.get(
                            "is_interface"
                        ):
                            is_interface = True
                            break
                    if not is_interface:
                        filtered_classes.append(class_name)

                if len(filtered_classes) < len(all_classes):
                    logger.info(
                        f"过滤掉 {len(all_classes) - len(filtered_classes)} 个接口，保留 {len(filtered_classes)} 个类"
                    )
                self._class_cache = filtered_classes
            else:
                self._class_cache = all_classes

            logger.info(f"找到 {len(self._class_cache)} 个 Java 类（不含接口）")
        return self._class_cache

    def _get_public_methods(self, class_name: str) -> List[str]:
        """
        获取类的所有 public 方法

        Args:
            class_name: 类名

        Returns:
            方法名列表（只返回属于指定类的方法，且满足最小行数要求）
        """
        from ..utils.project_utils import find_java_file

        # 优先从数据库查找类文件映射（支持同一文件中的多个类）
        file_path = find_java_file(self.project_path, class_name, db=self.db)

        if not file_path:
            logger.warning(f"未找到类文件: {class_name}")
            return []

        # 使用 JavaExecutor 获取 public 方法
        try:
            all_methods = self.java_executor.get_public_methods(str(file_path))
            if all_methods:
                # 过滤出属于指定类的方法，并检查行数要求
                class_methods = []
                skipped_count = 0

                for method in all_methods:
                    if isinstance(method, dict):
                        # 新格式：包含 className 字段
                        if method.get("className") == class_name:
                            # 检查方法行数是否满足最小行数要求
                            method_range = method.get("range")
                            if method_range and isinstance(method_range, dict):
                                begin_line = method_range.get("begin", 0)
                                end_line = method_range.get("end", 0)
                                method_lines = end_line - begin_line + 1

                                if method_lines < self.min_method_lines:
                                    logger.debug(
                                        f"跳过方法 {class_name}.{method.get('name')}：行数 {method_lines} 小于最小值 {self.min_method_lines}"
                                    )
                                    skipped_count += 1
                                    continue

                            class_methods.append(method)
                    else:
                        # 旧格式：字符串，无法区分类和行数，保留所有方法（向后兼容）
                        class_methods.append(method)

                if skipped_count > 0:
                    logger.debug(
                        f"类 {class_name}：根据最小行数配置跳过了 {skipped_count} 个方法"
                    )

                logger.debug(
                    f"类 {class_name} 有 {len(class_methods)} 个符合条件的 public 方法"
                )
                return class_methods
        except Exception as e:
            logger.error(f"获取 public 方法失败: {e}")

        # 如果失败，返回空列表
        return []

    def _get_first_available_method(
        self,
        class_name: str,
        blacklist: set,
        processed_targets: Optional[set] = None,
        prefer_unprocessed: bool = True,
        allow_first_only: bool = True,
    ) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
        """
        获取指定类中未命中黑名单的第一个 public 方法

        Args:
            class_name: 类名
            blacklist: 黑名单集合，元素格式 ClassName.methodName
            processed_targets: 已处理目标列表，元素格式 ClassName.methodName
            prefer_unprocessed: 是否优先返回未处理的方法
            allow_first_only: 为 False 时会返回可用列表中的第一个，同时用于随机策略筛选
        """
        if processed_targets is None:
            processed_targets = set()

        methods = self._get_public_methods(class_name)
        if not methods:
            return None, None, None

        unprocessed_candidates = []
        processed_candidates = []

        for method in methods:
            method_name = method.get("name") if isinstance(method, dict) else method
            target_key = f"{class_name}.{method_name}" if method_name else class_name
            if target_key in blacklist:
                continue

            method_signature = (
                method.get("signature") if isinstance(method, dict) else None
            )
            method_info = method if isinstance(method, dict) else None
            candidate = (method_info, method_name, method_signature)

            if target_key in processed_targets:
                processed_candidates.append(candidate)
            else:
                unprocessed_candidates.append(candidate)

            # 如果只需要第一个且优先未处理的，找到未处理的就返回
            if allow_first_only and prefer_unprocessed and unprocessed_candidates:
                break

        # 根据优先级返回
        if prefer_unprocessed:
            # 优先返回未处理的，没有的话返回已处理的
            if unprocessed_candidates:
                return unprocessed_candidates[0]
            elif processed_candidates:
                return processed_candidates[0]
        else:
            # 不区分优先级，优先返回已处理的，没有的话返回未处理的
            if processed_candidates:
                return processed_candidates[0]
            elif unprocessed_candidates:
                return unprocessed_candidates[0]

        return None, None, None

    def clear_cache(self) -> None:
        """清除缓存"""
        self._class_cache = None
