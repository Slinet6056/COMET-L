"""变异评估器"""

import logging
from typing import List, Dict
from datetime import datetime

from ..models import Mutant, TestCase, KillMatrix, EvaluationResult
from .java_executor import JavaExecutor
from ..utils.sandbox import SandboxManager

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
        # 创建沙箱
        sandbox_id = f"mutant_{mutant.id}"
        sandbox_path = self.sandbox_manager.create_sandbox(project_path, sandbox_id)

        try:
            # 应用变异到沙箱
            # 构建变异补丁 JSON
            import json
            patch_json = json.dumps({
                "file_path": mutant.patch.file_path,
                "line_start": mutant.patch.line_start,
                "line_end": mutant.patch.line_end,
                "original": mutant.patch.original_code,
                "mutated": mutant.patch.mutated_code,
            })

            # 确定源文件路径
            source_file = mutant.patch.file_path
            if not source_file:
                logger.error(f"变异体 {mutant.id} 没有指定源文件路径")
                return {}

            # 确定沙箱中的目标文件路径
            from pathlib import Path
            project_name = Path(project_path).name
            mutated_file = str(Path(sandbox_path) / Path(source_file).relative_to(Path(project_path)))

            # 应用变异
            mutation_result = self.java_executor.apply_mutation(
                source_file=source_file,
                patch_json=patch_json,
                output_path=mutated_file,
            )

            if not mutation_result.get("success", False):
                logger.error(f"应用变异失败: {mutant.id}")
                logger.error(f"  变异意图: {mutant.semantic_intent}")
                logger.error(f"  行范围: {mutant.patch.line_start}-{mutant.patch.line_end}")
                if mutation_result.get("stderr"):
                    logger.error(f"  Java 错误: {mutation_result['stderr'][:500]}")
                # 跳过此变异体，不运行测试
                return {}

            # 运行测试
            test_result = self.java_executor.run_tests(sandbox_path)

            # 解析结果
            results = {}
            if test_result.get("success"):
                # 所有测试通过 = 变异体幸存
                for test_case in test_cases:
                    results[test_case.id] = True
            else:
                # 测试失败 = 变异体被击杀
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
    ) -> KillMatrix:
        """
        构建击杀矩阵

        Args:
            mutants: 变异体列表
            test_cases: 测试用例列表
            project_path: 项目路径

        Returns:
            击杀矩阵
        """
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

    def get_survived_mutants(self, mutants: List[Mutant]) -> List[Mutant]:
        """
        获取幸存的变异体

        Args:
            mutants: 变异体列表

        Returns:
            幸存的变异体列表
        """
        return [m for m in mutants if m.survived]
