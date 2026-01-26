"""变异评估器"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Set, Optional
from datetime import datetime
from pathlib import Path

from ..models import Mutant, TestCase, KillMatrix, EvaluationResult
from .java_executor import JavaExecutor
from .surefire_parser import SurefireParser
from ..utils.sandbox import SandboxManager
from ..utils.log_context import log_context

logger = logging.getLogger(__name__)


class MutationEvaluator:
    """变异评估器 - 构建击杀矩阵和识别幸存变异体"""

    def __init__(
        self,
        java_executor: JavaExecutor,
        sandbox_manager: SandboxManager,
    ):
        """
        初始化变异评估器

        Args:
            java_executor: Java 执行器
            sandbox_manager: 沙箱管理器
        """
        self.java_executor = java_executor
        self.sandbox_manager = sandbox_manager
        self.surefire_parser = SurefireParser()

    def evaluate_mutant(
        self,
        mutant: Mutant,
        test_cases: List[TestCase],
        project_path: str,
    ) -> Dict[str, bool]:
        """
        评估单个变异体

        Args:
            mutant: 变异体
            test_cases: 测试用例列表
            project_path: 项目路径

        Returns:
            测试结果字典 {test_id: passed}
        """
        # 创建沙箱 - 修复：添加时间戳和线程ID确保唯一性，避免并发冲突
        import time
        import threading

        thread_id = threading.get_ident()
        timestamp_ns = time.time_ns()
        sandbox_id = f"mutant_{mutant.id}_{timestamp_ns}_{thread_id}"

        # 修复：检查沙箱是否已存在且正在被使用，避免并发冲突
        try:
            sandbox_path = self.sandbox_manager.create_sandbox(project_path, sandbox_id)
        except Exception as e:
            logger.warning(
                f"创建沙箱失败（可能已被其他线程使用）: {mutant.id}, 错误: {e}"
            )
            # 跳过此变异体，避免并发冲突
            return {}

        try:
            # 应用变异到沙箱
            # 构建变异补丁 JSON
            import json
            import time
            import os

            # 确定源文件路径
            original_file_path = mutant.patch.file_path
            if not original_file_path:
                logger.warning(f"变异体 {mutant.id} 没有指定源文件路径")
                return {}

            # 确定沙箱中的目标文件路径
            from pathlib import Path

            # 从完整路径中提取相对路径（从 src/main/java 开始）
            file_path_obj = Path(original_file_path)

            # 尝试找到 src/main/java 或 src/test/java 的位置
            parts = file_path_obj.parts
            try:
                src_idx = parts.index("src")
                rel_path = Path(*parts[src_idx:])
            except ValueError:
                # 如果找不到 src，尝试直接使用最后几个部分
                rel_path = Path(*parts[-5:]) if len(parts) >= 5 else file_path_obj.name

            # 沙箱中的源文件和目标文件路径（同一个文件，原地修改）
            sandbox_file = str(Path(sandbox_path) / rel_path)

            # 修复：patch_json 应该使用沙箱中的文件路径，而不是原始项目路径
            patch_json = json.dumps(
                {
                    "file_path": sandbox_file,  # 使用沙箱路径而非原始路径
                    "line_start": mutant.patch.line_start,
                    "line_end": mutant.patch.line_end,
                    "original": mutant.patch.original_code,
                    "mutated": mutant.patch.mutated_code,
                }
            )

            # 修复：验证沙箱文件是否存在，避免NoSuchFileException
            if not os.path.exists(sandbox_file):
                logger.error(f"沙箱文件不存在（沙箱可能被意外清理）: {sandbox_file}")
                logger.error(f"  变异体ID: {mutant.id}")
                logger.error(f"  沙箱路径: {sandbox_path}")
                return {}

            # 应用变异（源文件和输出文件都在沙箱中）
            mutation_result = self.java_executor.apply_mutation(
                source_file=sandbox_file,
                patch_json=patch_json,
                output_path=sandbox_file,
            )

            if not mutation_result.get("success", False):
                logger.warning(f"应用变异失败: {mutant.id}")
                logger.warning(
                    f"  行范围: {mutant.patch.line_start}-{mutant.patch.line_end}"
                )
                if mutation_result.get("stderr"):
                    logger.warning(f"  Java 错误: {mutation_result['stderr'][:500]}")

                # 跳过此变异体，不运行测试
                return {}

            logger.debug(f"变异应用成功: {mutant.id}")
            logger.debug(f"  沙箱路径: {sandbox_path}")
            logger.debug(f"  变异文件: {sandbox_file}")

            # 运行测试
            logger.debug(f"开始运行测试，沙箱: {sandbox_path}")
            test_result = self.java_executor.run_tests(sandbox_path)
            logger.debug(f"测试运行结果: success={test_result.get('success')}")
            if test_result.get("stderr"):
                logger.debug(f"  测试stderr: {test_result['stderr'][:200]}")
            if test_result.get("stdout"):
                logger.debug(f"  测试stdout: {test_result['stdout'][:200]}")

            # 构建测试用例名称到ID的映射
            results = {}

            if test_result.get("success"):
                # 所有测试通过 = 变异体幸存
                logger.debug(f"  所有测试通过，变异体 {mutant.id} 幸存")
                for test_case in test_cases:
                    results[test_case.id] = True
            else:
                # 测试失败：先检查是否存在 Surefire 报告
                # 如果不存在，说明是编译错误，直接杀死变异体
                reports_dir = str(Path(sandbox_path) / "target" / "surefire-reports")
                reports_path = Path(reports_dir)

                if not reports_path.exists():
                    # 编译错误或其他构建错误 - 变异体被杀死
                    logger.debug(
                        f"  Surefire 报告目录不存在（可能是编译错误），变异体 {mutant.id} 被杀死"
                    )
                    for test_case in test_cases:
                        results[test_case.id] = False
                else:
                    # 解析 Surefire 报告，获取精确的测试结果
                    failed_tests = self.surefire_parser.get_failed_test_names(
                        reports_dir
                    )
                    logger.debug(f"  检测到 {len(failed_tests)} 个失败的测试")

                    if not failed_tests:
                        # 有报告但没有失败的测试，可能是其他错误（如测试超时）
                        # 保守策略：标记所有测试为失败
                        logger.debug(
                            f"  未找到具体失败的测试，保守策略标记所有测试为失败"
                        )
                        for test_case in test_cases:
                            results[test_case.id] = False
                    else:
                        # 构建测试用例的完整名称映射
                        # 从 TestCase 的 methods 中提取每个测试方法的名称
                        test_full_names = {}  # {test_case.id: [完整测试名称列表]}
                        for test_case in test_cases:
                            full_names = []
                            for method in test_case.methods:
                                # 构建完整的测试名称: package.class_name.method_name
                                # 例如: com.example.CalculatorTest.testAddTwoPositiveNumbers
                                if test_case.package_name:
                                    full_name = f"{test_case.package_name}.{test_case.class_name}.{method.method_name}"
                                else:
                                    full_name = (
                                        f"{test_case.class_name}.{method.method_name}"
                                    )
                                full_names.append(full_name)
                            test_full_names[test_case.id] = full_names

                        for test_case in test_cases:
                            # 检查这个测试用例中的任何一个测试方法是否失败
                            test_failed = False

                            for full_name in test_full_names[test_case.id]:
                                if full_name in failed_tests:
                                    test_failed = True
                                    logger.debug(
                                        f"    测试方法 {full_name} (测试用例 {test_case.id}) 击杀了变异体"
                                    )
                                    break

                            # True = 测试通过（变异体幸存）
                            # False = 测试失败（变异体被击杀）
                            results[test_case.id] = not test_failed

                        # 如果有失败但没有匹配到任何测试用例，可能是测试名称不匹配
                        if not any(not passed for passed in results.values()):
                            logger.warning(
                                f"  检测到测试失败但无法匹配到具体测试用例: {failed_tests}"
                            )
                            logger.debug(f"  可用的测试名称: {test_full_names}")
                            # 保守策略：标记所有测试为失败
                            for test_case in test_cases:
                                results[test_case.id] = False

            return results

        finally:
            # 清理沙箱
            self.sandbox_manager.cleanup_sandbox(sandbox_id)

    def build_kill_matrix(
        self,
        mutants: List[Mutant],
        test_cases: List[TestCase],
        project_path: str,
        max_workers: Optional[int] = None,
    ) -> KillMatrix:
        """
        构建击杀矩阵（支持并行评估）

        Args:
            mutants: 变异体列表
            test_cases: 测试用例列表
            project_path: 项目路径
            max_workers: 最大并行工作线程数，None 表示串行执行，
                        正整数表示并行度（建议 2-8）

        Returns:
            击杀矩阵
        """
        kill_matrix = KillMatrix()

        if not mutants:
            logger.info("没有变异体需要评估")
            return kill_matrix

        # 串行模式（向后兼容）
        if max_workers is None or max_workers <= 1:
            return self._build_kill_matrix_serial(mutants, test_cases, project_path)

        # 并行模式
        return self._build_kill_matrix_parallel(
            mutants, test_cases, project_path, max_workers
        )

    def _build_kill_matrix_serial(
        self,
        mutants: List[Mutant],
        test_cases: List[TestCase],
        project_path: str,
    ) -> KillMatrix:
        """串行构建击杀矩阵（原有逻辑）"""
        kill_matrix = KillMatrix()

        for mutant in mutants:
            logger.info(f"评估变异体: {mutant.id}")

            # 评估变异体
            test_results = self.evaluate_mutant(mutant, test_cases, project_path)

            # 更新击杀矩阵
            for test_id, passed in test_results.items():
                if not passed:
                    # 测试失败 = 击杀变异体
                    kill_matrix.add_kill(mutant.id, test_id)

            # 更新变异体状态
            if kill_matrix.is_killed(mutant.id):
                mutant.survived = False
                mutant.killed_by = kill_matrix.get_killers(mutant.id)
            else:
                mutant.survived = True

            mutant.evaluated_at = datetime.now()

        logger.info(
            f"击杀矩阵构建完成: "
            f"{len([m for m in mutants if not m.survived])}/{len(mutants)} 被击杀"
        )
        return kill_matrix

    def _build_kill_matrix_parallel(
        self,
        mutants: List[Mutant],
        test_cases: List[TestCase],
        project_path: str,
        max_workers: int,
    ) -> KillMatrix:
        """并行构建击杀矩阵"""
        kill_matrix = KillMatrix()
        results_lock = threading.Lock()

        # 统计信息
        total = len(mutants)
        completed = 0
        completed_lock = threading.Lock()

        logger.info(f"开始并行评估 {total} 个变异体（并行度: {max_workers}）")

        def evaluate_and_update(mutant: Mutant) -> None:
            """评估单个变异体并更新状态"""
            nonlocal completed

            # 设置日志上下文，便于在多线程日志中区分不同变异体评估任务
            with log_context(f"Eval:{mutant.id[:8]}"):
                try:
                    # 评估变异体（每个变异体在独立沙箱中运行）
                    test_results = self.evaluate_mutant(
                        mutant, test_cases, project_path
                    )

                    # 线程安全地更新击杀矩阵和变异体状态
                    with results_lock:
                        for test_id, passed in test_results.items():
                            if not passed:
                                kill_matrix.add_kill(mutant.id, test_id)

                        # 更新变异体状态
                        if kill_matrix.is_killed(mutant.id):
                            mutant.survived = False
                            mutant.killed_by = kill_matrix.get_killers(mutant.id)
                        else:
                            mutant.survived = True

                        mutant.evaluated_at = datetime.now()

                    # 更新进度
                    with completed_lock:
                        completed += 1
                        current = completed
                        if current % 5 == 0 or current == total:
                            logger.info(
                                f"评估进度: {current}/{total} ({current*100//total}%)"
                            )

                except Exception as e:
                    logger.warning(f"评估变异体时出错: {e}")
                    # 出错时标记为幸存（保守策略）
                    with results_lock:
                        mutant.survived = True
                        mutant.evaluated_at = datetime.now()

        # 使用线程池并行评估
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            futures = {
                executor.submit(evaluate_and_update, mutant): mutant
                for mutant in mutants
            }

            # 等待所有任务完成，处理异常
            for future in as_completed(futures):
                mutant = futures[future]
                try:
                    future.result()  # 获取结果，触发异常（如果有）
                except Exception as e:
                    logger.warning(f"变异体 {mutant.id} 评估任务失败: {e}")

        killed_count = len([m for m in mutants if not m.survived])
        logger.info(
            f"并行击杀矩阵构建完成: {killed_count}/{total} 被击杀 "
            f"(变异分数: {killed_count*100//total if total > 0 else 0}%)"
        )
        return kill_matrix

    def get_survived_mutants(self, mutants: List[Mutant]) -> List[Mutant]:
        """
        获取幸存的变异体

        Args:
            mutants: 变异体列表

        Returns:
            幸存的变异体列表
        """
        return [m for m in mutants if m.survived]
