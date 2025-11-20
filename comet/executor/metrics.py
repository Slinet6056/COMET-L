"""度量收集器"""

import logging
from typing import List, Dict, Any
from datetime import datetime

from ..models import Mutant, TestCase, KillMatrix, Metrics, CoverageInfo

logger = logging.getLogger(__name__)


class MetricsCollector:
    """度量收集器 - 维护和计算系统指标"""

    def __init__(self):
        """初始化度量收集器"""
        self.history: List[Metrics] = []

    def collect_metrics(
        self,
        iteration: int,
        mutants: List[Mutant],
        test_cases: List[TestCase],
        kill_matrix: KillMatrix,
        coverage_info: CoverageInfo = None,
        llm_calls: int = 0,
    ) -> Metrics:
        """
        收集当前迭代的度量

        Args:
            iteration: 迭代次数
            mutants: 变异体列表
            test_cases: 测试用例列表
            kill_matrix: 击杀矩阵
            coverage_info: 覆盖率信息
            llm_calls: LLM 调用次数

        Returns:
            Metrics 对象
        """
        total_mutants = len(mutants)
        survived_mutants = len([m for m in mutants if m.survived])
        killed_mutants = total_mutants - survived_mutants

        metrics = Metrics(
            iteration=iteration,
            total_mutants=total_mutants,
            killed_mutants=killed_mutants,
            survived_mutants=survived_mutants,
            total_tests=len(test_cases),
            llm_calls=llm_calls,
            timestamp=datetime.now(),
        )

        # 计算变异分数
        metrics.calculate_mutation_score()

        # 添加覆盖率信息
        if coverage_info:
            metrics.line_coverage = coverage_info.line_coverage
            metrics.branch_coverage = coverage_info.branch_coverage

        # 保存到历史
        self.history.append(metrics)

        logger.info(
            f"迭代 {iteration} 度量: "
            f"变异分数={metrics.mutation_score:.3f}, "
            f"行覆盖率={metrics.line_coverage:.3f}, "
            f"测试数={metrics.total_tests}"
        )

        return metrics

    def get_latest_metrics(self) -> Metrics:
        """获取最新度量"""
        if not self.history:
            return Metrics(iteration=0)
        return self.history[-1]

    def get_improvement(self) -> Dict[str, float]:
        """
        计算相对于上一次迭代的改进

        Returns:
            改进指标字典
        """
        if len(self.history) < 2:
            return {
                "mutation_score_delta": 0.0,
                "coverage_delta": 0.0,
                "tests_added": 0,
            }

        latest = self.history[-1]
        previous = self.history[-2]

        return {
            "mutation_score_delta": latest.mutation_score - previous.mutation_score,
            "coverage_delta": latest.line_coverage - previous.line_coverage,
            "tests_added": latest.total_tests - previous.total_tests,
        }

    def has_improvement(self, threshold: float = 0.01) -> bool:
        """
        检查是否有显著改进

        Args:
            threshold: 改进阈值

        Returns:
            是否有改进
        """
        improvement = self.get_improvement()
        return (
            improvement["mutation_score_delta"] > threshold or
            improvement["coverage_delta"] > threshold
        )

    def get_summary(self) -> Dict[str, Any]:
        """
        获取度量摘要

        Returns:
            摘要字典
        """
        if not self.history:
            return {}

        latest = self.get_latest_metrics()
        initial = self.history[0]

        return {
            "total_iterations": len(self.history),
            "initial_mutation_score": initial.mutation_score,
            "final_mutation_score": latest.mutation_score,
            "mutation_score_improvement": latest.mutation_score - initial.mutation_score,
            "initial_coverage": initial.line_coverage,
            "final_coverage": latest.line_coverage,
            "coverage_improvement": latest.line_coverage - initial.line_coverage,
            "total_tests_generated": latest.total_tests - initial.total_tests,
            "total_llm_calls": latest.llm_calls,
        }

    def get_survived_mutants_for_method(
        self,
        class_name: str,
        method_name: str,
        all_mutants: List[Mutant] = None,
    ) -> List[Mutant]:
        """
        获取特定方法的幸存变异体

        Args:
            class_name: 类名
            method_name: 方法名
            all_mutants: 所有变异体列表（如果为 None 则返回空列表）

        Returns:
            幸存的变异体列表
        """
        if not all_mutants:
            return []

        survived = [
            m for m in all_mutants
            if m.survived
            and m.class_name == class_name
            and (m.method_name == method_name or method_name is None)
        ]

        return survived

    def get_coverage_gaps(
        self,
        class_name: str,
        method_name: str,
        coverage_info: CoverageInfo = None,
        db = None,
    ) -> Dict[str, Any]:
        """
        分析覆盖缺口（优先使用数据库中的真实覆盖率数据）

        Args:
            class_name: 类名
            method_name: 方法名
            coverage_info: 覆盖率信息（已废弃，保留向后兼容）
            db: 数据库实例

        Returns:
            覆盖缺口信息字典
        """
        # 优先从数据库获取最新覆盖率
        if db:
            try:
                coverage = db.get_method_coverage(class_name, method_name)
                if coverage:
                    return {
                        "coverage_rate": coverage.line_coverage_rate,
                        "total_lines": coverage.total_lines,
                        "covered_lines": len(coverage.covered_lines),
                        "uncovered_lines": coverage.missed_lines,  # 行号列表
                    }
            except Exception as e:
                logger.warning(f"从数据库获取覆盖率失败: {e}")

        # 回退到传入的 coverage_info
        if not coverage_info:
            return {
                "uncovered_lines": [],
                "coverage_rate": 0.0,
                "total_lines": 0,
            }

        # 简化实现：返回未覆盖的行
        total_lines = coverage_info.total_lines
        covered_lines = coverage_info.covered_lines

        # 假设行号是连续的，从1开始
        all_lines = set(range(1, total_lines + 1))
        covered_set = set(covered_lines)
        uncovered_lines = list(all_lines - covered_set)

        return {
            "uncovered_lines": uncovered_lines,
            "coverage_rate": coverage_info.line_coverage,
            "total_lines": total_lines,
        }

    def update_from_evaluation(
        self,
        mutants: List[Mutant],
        test_cases: List[TestCase],
        kill_matrix: KillMatrix,
        coverage_data: Dict[str, Any] = None,
    ) -> None:
        """
        从评估结果更新度量指标

        Args:
            mutants: 变异体列表
            test_cases: 测试用例列表
            kill_matrix: 击杀矩阵
            coverage_data: 覆盖率数据
        """
        # 获取当前迭代次数
        iteration = len(self.history)

        # 构建覆盖率信息
        coverage_info = None
        if coverage_data:
            # 尝试从覆盖率数据中提取信息
            try:
                coverage_info = CoverageInfo(
                    class_name=coverage_data.get("class_name", "unknown"),
                    covered_lines=coverage_data.get("covered_lines", []),
                    total_lines=coverage_data.get("total_lines", 0),
                    covered_branches=coverage_data.get("covered_branches", 0),
                    total_branches=coverage_data.get("total_branches", 0),
                    line_coverage=coverage_data.get("line_coverage", 0.0),
                    branch_coverage=coverage_data.get("branch_coverage", 0.0),
                )
            except Exception as e:
                logger.warning(f"解析覆盖率数据失败: {e}")

        # 收集度量
        self.collect_metrics(
            iteration=iteration,
            mutants=mutants,
            test_cases=test_cases,
            kill_matrix=kill_matrix,
            coverage_info=coverage_info,
            llm_calls=self.get_latest_metrics().llm_calls if self.history else 0,
        )

    def get_mutation_score(self) -> float:
        """
        获取最新的变异分数

        Returns:
            变异分数（0.0 - 1.0）
        """
        if not self.history:
            return 0.0
        return self.get_latest_metrics().mutation_score

    def increment_llm_calls(self, count: int = 1) -> None:
        """
        增加 LLM 调用次数

        Args:
            count: 增加的次数
        """
        if self.history:
            self.history[-1].llm_calls += count
