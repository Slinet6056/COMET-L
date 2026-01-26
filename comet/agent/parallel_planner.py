"""并行 Agent 调度器 - 批量并行处理 + 集中同步"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

from .state import ParallelAgentState, WorkerResult
from .tools import AgentTools
from .target_selector import TargetSelector
from ..llm.client import LLMClient
from ..executor.java_executor import JavaExecutor
from ..executor.coverage_parser import CoverageParser
from ..store.database import Database
from ..utils.sandbox import SandboxManager
from ..utils.log_context import log_context

logger = logging.getLogger(__name__)


class ParallelPlannerAgent:
    """
    并行调度器 Agent - 批量并行处理多个目标方法

    架构：批量并行 + 集中同步
    1. 并行阶段：多个 Worker 同时处理不同的目标方法
    2. 同步阶段：合并测试代码、验证、收集全局覆盖率
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: AgentTools,
        target_selector: TargetSelector,
        java_executor: JavaExecutor,
        sandbox_manager: SandboxManager,
        database: Database,
        project_path: str,
        workspace_path: str,
        max_parallel_targets: int = 4,
        max_eval_workers: int = 4,
        max_iterations: int = 10,
        budget: int = 1000,
        timeout_per_target: int = 300,
        excellent_mutation_score: float = 0.95,
        excellent_line_coverage: float = 0.90,
        excellent_branch_coverage: float = 0.85,
    ):
        """
        初始化并行调度器

        Args:
            llm_client: LLM 客户端
            tools: Agent 工具集
            target_selector: 目标选择器
            java_executor: Java 执行器
            sandbox_manager: 沙箱管理器
            database: 数据库
            project_path: 原始项目路径
            workspace_path: 工作空间路径（workspace 沙箱）
            max_parallel_targets: 最大并行目标数
            max_eval_workers: 变异体评估并行度
            max_iterations: 最大迭代次数（批次数）
            budget: LLM 调用预算
            timeout_per_target: 每个目标的超时时间（秒）
            excellent_mutation_score: 优秀变异分数阈值
            excellent_line_coverage: 优秀行覆盖率阈值
            excellent_branch_coverage: 优秀分支覆盖率阈值
        """
        self.llm = llm_client
        self.tools = tools
        self.target_selector = target_selector
        self.java_executor = java_executor
        self.sandbox_manager = sandbox_manager
        self.db = database
        self.project_path = project_path
        self.workspace_path = workspace_path

        # 并行配置
        self.max_parallel_targets = max_parallel_targets
        self.max_eval_workers = max_eval_workers
        self.timeout_per_target = timeout_per_target

        # 停止条件
        self.max_iterations = max_iterations
        self.budget = budget
        self.excellent_mutation_score = excellent_mutation_score
        self.excellent_line_coverage = excellent_line_coverage
        self.excellent_branch_coverage = excellent_branch_coverage

        # 状态管理
        self.state: ParallelAgentState = ParallelAgentState()
        self.state.budget = budget

        # 覆盖率解析器
        self.coverage_parser = CoverageParser()

        # 中断标志
        self._interrupted = False

    def run(
        self,
        stop_on_no_improvement_rounds: int = 3,
        min_improvement_threshold: float = 0.01,
    ) -> ParallelAgentState:
        """
        运行并行调度循环

        Args:
            stop_on_no_improvement_rounds: 无改进时停止的轮数
            min_improvement_threshold: 最小改进阈值

        Returns:
            最终状态
        """
        logger.info("=" * 60)
        logger.info("开始并行协同进化循环")
        logger.info(f"最大并行目标数: {self.max_parallel_targets}")
        logger.info(f"变异体评估并行度: {self.max_eval_workers}")
        logger.info("=" * 60)

        from datetime import datetime

        self.state.start_time = datetime.now()
        no_improvement_count = 0
        prev_mutation_score = 0.0
        prev_line_coverage = 0.0

        try:
            while not self._should_stop():
                batch_num = self.state.current_batch + 1
                logger.info(f"\n{'='*60}")
                logger.info(f"批次 {batch_num}/{self.max_iterations}")
                logger.info(f"{'='*60}")

                # === 批量并行阶段 ===
                # 1. 选择一批目标
                targets = self._select_batch_targets()
                if not targets:
                    logger.info("没有更多可选目标，停止")
                    break

                logger.info(f"已选择 {len(targets)} 个目标进行并行处理")
                for t in targets:
                    logger.info(f"  - {t['class_name']}.{t['method_name']}")

                # 2. 并行处理所有目标
                worker_results = self._process_targets_parallel(targets)

                # 3. 处理结果
                successful_results = [r for r in worker_results if r.success]
                failed_results = [r for r in worker_results if not r.success]

                logger.info(
                    f"批次完成: {len(successful_results)} 成功, {len(failed_results)} 失败"
                )

                # 将失败的目标加入黑名单
                for result in failed_results:
                    self.state.add_failed_target(
                        result.class_name,
                        result.method_name,
                        result.error or "处理失败",
                    )

                # === 集中同步阶段 ===
                if successful_results:
                    # 4. 合并测试代码到 workspace
                    self._merge_test_files(successful_results)

                    # 5. 编译验证，移除冲突文件（即使有冲突也要尝试）
                    self._validate_and_fix_conflicts()

                    # 6. 统一收集全局覆盖率（即使合并有问题也要尝试）
                    self._collect_global_coverage()

                # 7. 更新全局状态
                self._sync_global_state()

                # 8. 记录批次结果
                self.state.add_batch_result(worker_results)
                self.state.iteration += 1

                # 9. 检查改进
                has_improvement = self._check_improvement(
                    prev_mutation_score,
                    prev_line_coverage,
                    min_improvement_threshold,
                )

                if has_improvement:
                    logger.info("检测到改进，重置无改进计数器")
                    no_improvement_count = 0
                    self.state.add_improvement(
                        {
                            "batch": batch_num,
                            "mutation_score": self.state.global_mutation_score,
                            "line_coverage": self.state.line_coverage,
                            "mutation_score_delta": self.state.global_mutation_score
                            - prev_mutation_score,
                            "coverage_delta": self.state.line_coverage
                            - prev_line_coverage,
                        }
                    )
                else:
                    no_improvement_count += 1
                    logger.info(
                        f"无显著改进 (连续 {no_improvement_count}/{stop_on_no_improvement_rounds} 轮)"
                    )

                prev_mutation_score = self.state.global_mutation_score
                prev_line_coverage = self.state.line_coverage

                # 10. 检查停止条件
                if no_improvement_count >= stop_on_no_improvement_rounds:
                    logger.info(f"连续 {no_improvement_count} 轮无改进，停止")
                    break

                if self._check_excellent_quality():
                    logger.info("已达到优秀质量水平，停止")
                    break

        except KeyboardInterrupt:
            logger.warning("\n收到中断信号，正在优雅退出...")
            self._interrupted = True

        logger.info("=" * 60)
        logger.info("并行协同进化循环结束")
        self._log_final_summary()
        logger.info("=" * 60)

        return self.state

    def _select_batch_targets(self) -> List[Dict[str, Any]]:
        """
        选择一批目标进行并行处理

        Returns:
            目标列表
        """
        targets = []
        blacklist = set(ft.get("target") for ft in self.state.failed_targets)
        processed = set(self.state.processed_targets)

        # 获取当前活跃的目标，避免重复选择
        active_targets = set(self.state.get_active_targets())

        for _ in range(self.max_parallel_targets):
            # 使用目标选择器选择下一个目标
            target = self.target_selector.select(
                criteria="coverage",
                blacklist=blacklist | active_targets,
                processed_targets=processed,
            )

            if not target or not target.get("class_name"):
                break

            class_name = target["class_name"]
            method_name = target.get("method_name", "")
            target_id = f"{class_name}.{method_name}"

            # 尝试获取目标（原子操作）
            if self.state.acquire_target(class_name, method_name):
                targets.append(target)
                active_targets.add(target_id)
            else:
                # 目标已被占用，加入临时黑名单继续选择
                blacklist.add(target_id)

        return targets

    def _process_targets_parallel(
        self, targets: List[Dict[str, Any]]
    ) -> List[WorkerResult]:
        """
        并行处理多个目标

        Args:
            targets: 目标列表

        Returns:
            Worker 结果列表
        """
        results = []

        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            futures = {
                executor.submit(self._process_single_target, target): target
                for target in targets
            }

            for future in as_completed(futures):
                target = futures[future]
                try:
                    result = future.result(timeout=self.timeout_per_target)
                    results.append(result)
                except Exception as e:
                    logger.warning(
                        f"处理目标 {target.get('class_name')}.{target.get('method_name')} 失败: {e}"
                    )
                    results.append(
                        WorkerResult(
                            target_id=f"{target.get('class_name')}.{target.get('method_name')}",
                            class_name=target.get("class_name", ""),
                            method_name=target.get("method_name", ""),
                            success=False,
                            error=str(e),
                        )
                    )
                finally:
                    # 释放目标
                    self.state.release_target(
                        target.get("class_name", ""),
                        target.get("method_name", ""),
                        success=any(
                            r.success
                            and r.target_id
                            == f"{target.get('class_name')}.{target.get('method_name')}"
                            for r in results
                        ),
                    )

        return results

    def _process_single_target(self, target: Dict[str, Any]) -> WorkerResult:
        """
        处理单个目标（Worker 主逻辑）

        在独立沙箱中执行：并行生成测试和变异体 → 评估

        Args:
            target: 目标信息

        Returns:
            Worker 结果
        """
        class_name = target.get("class_name", "")
        method_name = target.get("method_name", "")
        target_id = f"{class_name}.{method_name}"

        # 设置日志上下文，便于在多线程日志中区分不同 Worker
        with log_context(f"Worker:{target_id}"):
            return self._process_single_target_impl(
                target, class_name, method_name, target_id
            )

    def _process_single_target_impl(
        self, target: Dict[str, Any], class_name: str, method_name: str, target_id: str
    ) -> WorkerResult:
        """_process_single_target 的实际实现"""
        start_time = time.time()

        logger.info(f"开始处理: {target_id}")

        result = WorkerResult(
            target_id=target_id,
            class_name=class_name,
            method_name=method_name,
        )

        # 为此目标创建独立沙箱
        sandbox_path = None
        sandbox_id = None

        try:
            sandbox_path = self.sandbox_manager.create_target_sandbox(
                self.project_path, class_name, method_name
            )
            # 从路径中提取 sandbox_id（路径的最后一部分）
            sandbox_id = Path(sandbox_path).name
            logger.debug(f"创建沙箱: {sandbox_path} (id: {sandbox_id})")

            # 1. 并行生成测试和变异体（两者都只读源代码，可并行）
            test_result = None
            mutant_result = None

            with ThreadPoolExecutor(max_workers=2) as gen_executor:
                test_future = gen_executor.submit(
                    self._generate_tests_in_sandbox,
                    sandbox_path,
                    class_name,
                    method_name,
                )
                mutant_future = gen_executor.submit(
                    self._generate_mutants_in_sandbox,
                    sandbox_path,
                    class_name,
                    method_name,
                )

                # 获取结果
                try:
                    test_result = test_future.result(timeout=180)
                except Exception as e:
                    logger.warning(f"测试生成异常: {e}")

                try:
                    mutant_result = mutant_future.result(timeout=180)
                except Exception as e:
                    logger.warning(f"变异体生成异常: {e}")

            # 处理测试生成结果
            if test_result:
                result.tests_generated = test_result.get("generated", 0)
                result.test_files = test_result.get("test_files", {})

            if result.tests_generated == 0:
                result.error = "测试生成失败"
                logger.warning(f"测试生成失败")
                return result

            # 处理变异体生成结果
            if mutant_result:
                result.mutants_generated = mutant_result.get("generated", 0)

            if result.mutants_generated == 0:
                # 没有变异体也算成功（可能方法太简单）
                logger.info(f"没有生成变异体")
                result.success = True
                return result

            # 2. 评估变异体（在独立沙箱中）
            eval_result = self._evaluate_in_sandbox(
                sandbox_path, class_name, method_name
            )
            if eval_result:
                result.mutants_evaluated = eval_result.get("evaluated", 0)
                result.mutants_killed = eval_result.get("killed", 0)
                result.local_mutation_score = eval_result.get("mutation_score", 0.0)

            result.success = True
            result.processing_time = time.time() - start_time

            logger.info(
                f"完成: "
                f"{result.tests_generated} 测试, "
                f"{result.mutants_generated} 变异体, "
                f"变异分数 {result.local_mutation_score:.1%}, "
                f"耗时 {result.processing_time:.1f}s"
            )

        except Exception as e:
            result.error = str(e)
            result.processing_time = time.time() - start_time
            logger.warning(f"处理异常: {e}")

        finally:
            # 清理沙箱
            if sandbox_id:
                try:
                    self.sandbox_manager.cleanup_sandbox(sandbox_id)
                except Exception as e:
                    logger.warning(f"清理沙箱失败: {e}")

        return result

    def _generate_tests_in_sandbox(
        self, sandbox_path: str, class_name: str, method_name: str
    ) -> Optional[Dict[str, Any]]:
        """在沙箱中生成测试"""
        try:
            # 检查是否已有测试
            existing_tests = self.db.get_tests_by_target_method(class_name, method_name)
            if existing_tests:
                # 已有测试，读取测试文件内容
                test_files = {}
                for tc in existing_tests:
                    test_file_path = Path(sandbox_path) / "src" / "test" / "java"
                    if tc.package_name:
                        test_file_path = test_file_path / tc.package_name.replace(
                            ".", "/"
                        )
                    test_file_path = test_file_path / f"{tc.class_name}.java"
                    if test_file_path.exists():
                        test_files[str(test_file_path)] = test_file_path.read_text()

                return {
                    "generated": sum(len(tc.methods) for tc in existing_tests),
                    "test_files": test_files,
                }

            # 调用工具生成测试
            result = self.tools.call(
                "generate_tests", class_name=class_name, method_name=method_name
            )

            if result and result.get("generated", 0) > 0:
                # 收集生成的测试文件（只收集与当前目标相关的）
                test_files = self._collect_test_files(
                    sandbox_path, class_name, method_name
                )
                result["test_files"] = test_files

            return result

        except Exception as e:
            logger.warning(f"生成测试失败: {e}")
            return None

    def _generate_mutants_in_sandbox(
        self, sandbox_path: str, class_name: str, method_name: str
    ) -> Optional[Dict[str, Any]]:
        """在沙箱中生成变异体"""
        try:
            # 检查是否已有变异体
            existing_mutants = self.db.get_mutants_by_method(
                class_name, method_name, status="valid"
            )
            if existing_mutants:
                return {"generated": len(existing_mutants)}

            # 调用工具生成变异体
            result = self.tools.call(
                "generate_mutants", class_name=class_name, method_name=method_name
            )
            return result

        except Exception as e:
            logger.warning(f"生成变异体失败: {e}")
            return None

    def _evaluate_in_sandbox(
        self, sandbox_path: str, class_name: str, method_name: str
    ) -> Optional[Dict[str, Any]]:
        """在沙箱中评估变异体"""
        try:
            from ..utils.project_utils import write_test_file

            # 获取变异体和测试用例
            mutants = self.db.get_mutants_by_method(
                class_name, method_name, status="valid"
            )
            test_cases = self.db.get_tests_by_target_method(class_name, method_name)

            if not mutants or not test_cases:
                return {"evaluated": 0, "killed": 0, "mutation_score": 0.0}

            # 关键修复：将测试文件写入 Worker 沙箱
            # 这样变异体沙箱（从 Worker 沙箱复制）才会包含测试文件
            formatting_enabled, formatting_style = self.tools._get_formatting_config()
            for tc in test_cases:
                write_test_file(
                    project_path=sandbox_path,
                    package_name=tc.package_name,
                    test_code=tc.full_code,
                    test_class_name=tc.class_name,
                    formatting_enabled=formatting_enabled,
                    formatting_style=formatting_style,
                )
            logger.debug(f"已将 {len(test_cases)} 个测试用例写入沙箱: {sandbox_path}")

            # 过滤未评估的变异体
            unevaluated = [m for m in mutants if m.evaluated_at is None]
            if not unevaluated:
                # 所有变异体都已评估，返回统计
                killed = len([m for m in mutants if not m.survived])
                return {
                    "evaluated": len(mutants),
                    "killed": killed,
                    "mutation_score": killed / len(mutants) if mutants else 0.0,
                }

            # 使用并行评估
            from ..executor.mutation_evaluator import MutationEvaluator

            evaluator = MutationEvaluator(self.java_executor, self.sandbox_manager)
            kill_matrix = evaluator.build_kill_matrix(
                mutants=unevaluated,
                test_cases=test_cases,
                project_path=sandbox_path,
                max_workers=self.max_eval_workers,
            )

            # 保存评估结果
            for mutant in unevaluated:
                self.db.save_mutant(mutant)

            killed = len([m for m in mutants if not m.survived])
            return {
                "evaluated": len(unevaluated),
                "killed": killed,
                "mutation_score": killed / len(mutants) if mutants else 0.0,
            }

        except Exception as e:
            logger.warning(f"评估变异体失败: {e}")
            return None

    def _collect_test_files(
        self, sandbox_path: str, class_name: str, method_name: str = ""
    ) -> Dict[str, str]:
        """
        收集沙箱中与当前目标相关的测试文件

        只收集与 class_name 相关的测试文件，避免收集其他 Worker 的文件
        造成合并冲突
        """
        test_files = {}
        test_dir = Path(sandbox_path) / "src" / "test" / "java"

        if not test_dir.exists():
            return test_files

        # 构建匹配模式：只收集与当前类相关的测试文件
        # 例如：PaymentService -> PaymentService_*Test.java 或 PaymentServiceTest.java
        target_patterns = [
            f"{class_name}_",  # PaymentService_processPaymentTest.java
            f"{class_name}Test",  # PaymentServiceTest.java
        ]

        for test_file in test_dir.rglob("*Test.java"):
            file_name = test_file.stem  # 不含扩展名的文件名

            # 检查文件名是否匹配当前目标
            is_target_file = any(
                file_name.startswith(pattern) for pattern in target_patterns
            )

            if not is_target_file:
                logger.debug(f"跳过非目标测试文件: {test_file.name}")
                continue

            try:
                content = test_file.read_text()
                rel_path = test_file.relative_to(test_dir)
                test_files[str(rel_path)] = content
                logger.debug(f"收集测试文件: {rel_path}")
            except Exception as e:
                logger.warning(f"读取测试文件失败: {test_file}: {e}")

        return test_files

    def _merge_test_files(self, results: List[WorkerResult]) -> int:
        """
        合并所有 Worker 生成的测试文件到 workspace

        Args:
            results: Worker 结果列表

        Returns:
            成功合并的文件数量
        """
        logger.info("开始合并测试文件到 workspace...")

        test_dir = Path(self.workspace_path) / "src" / "test" / "java"
        test_dir.mkdir(parents=True, exist_ok=True)

        merged_count = 0
        conflict_count = 0
        skipped_identical = 0

        # 收集所有要合并的文件，去重
        files_to_merge: Dict[str, str] = {}
        for result in results:
            if not result.success:
                continue
            for rel_path, content in result.test_files.items():
                if rel_path in files_to_merge:
                    # 文件已在待合并列表中，检查内容是否相同
                    if files_to_merge[rel_path] == content:
                        skipped_identical += 1
                        continue
                    else:
                        # 内容不同，保留先到的
                        conflict_count += 1
                        self.state.record_merge_conflict()
                        logger.warning(f"Worker 间测试文件冲突: {rel_path}")
                        continue
                files_to_merge[rel_path] = content

        # 写入去重后的文件
        for rel_path, content in files_to_merge.items():
            target_path = test_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if target_path.exists():
                # workspace 中已存在，检查是否相同
                existing_content = target_path.read_text()
                if existing_content == content:
                    skipped_identical += 1
                    continue
                else:
                    # 内容不同，使用新内容覆盖（新生成的可能更好）
                    logger.debug(f"更新已存在的测试文件: {rel_path}")

            try:
                target_path.write_text(content)
                merged_count += 1
                logger.debug(f"合并测试文件: {rel_path}")
            except Exception as e:
                logger.warning(f"写入测试文件失败: {rel_path}: {e}")

        logger.info(
            f"测试文件合并完成: {merged_count} 个新文件, "
            f"{skipped_identical} 个相同跳过, {conflict_count} 个冲突"
        )
        return merged_count

    def _validate_and_fix_conflicts(self) -> bool:
        """
        验证合并结果，移除冲突文件

        Returns:
            是否验证通过
        """
        logger.info("验证合并结果...")

        # 编译测试
        compile_result = self.java_executor.compile_tests(self.workspace_path)

        if compile_result.get("success", False):
            logger.info("测试编译通过")
            return True

        # 编译失败，尝试定位问题文件
        logger.warning("测试编译失败，尝试定位问题文件...")

        # 简单策略：逐个验证测试文件
        test_dir = Path(self.workspace_path) / "src" / "test" / "java"
        test_files = list(test_dir.rglob("*Test.java"))

        removed_count = 0
        for test_file in test_files:
            # 暂时移除文件
            backup_content = test_file.read_text()
            test_file.unlink()

            # 重新编译
            check_result = self.java_executor.compile_tests(self.workspace_path)

            if check_result.get("success", False):
                # 这个文件有问题，不恢复
                logger.warning(f"移除冲突文件: {test_file}")
                removed_count += 1
                self.state.record_merge_conflict()
            else:
                # 不是这个文件的问题，恢复
                test_file.write_text(backup_content)

        if removed_count > 0:
            logger.info(f"已移除 {removed_count} 个冲突文件")

        # 最终验证
        final_result = self.java_executor.compile_tests(self.workspace_path)
        return final_result.get("success", False)

    def _collect_global_coverage(self) -> None:
        """统一收集全局覆盖率"""
        logger.info("收集全局覆盖率...")

        try:
            # 运行所有测试并收集覆盖率
            result = self.java_executor.run_tests_with_coverage(self.workspace_path)

            if not result.get("success", False):
                logger.warning("测试运行失败，跳过覆盖率收集")
                return

            # 解析 JaCoCo 报告
            jacoco_path = (
                Path(self.workspace_path) / "target" / "site" / "jacoco" / "jacoco.xml"
            )

            if jacoco_path.exists():
                coverage = self.coverage_parser.aggregate_global_coverage_from_xml(
                    str(jacoco_path)
                )
                if coverage:
                    self.state.line_coverage = coverage.get("line_coverage", 0.0)
                    self.state.branch_coverage = coverage.get("branch_coverage", 0.0)
                    logger.info(
                        f"全局覆盖率: 行 {self.state.line_coverage:.1%}, "
                        f"分支 {self.state.branch_coverage:.1%}"
                    )
            else:
                logger.warning(f"JaCoCo 报告不存在: {jacoco_path}")

        except Exception as e:
            logger.warning(f"收集覆盖率失败: {e}")

    def _sync_global_state(self) -> None:
        """同步全局状态"""
        try:
            # 从数据库获取全局变异体统计
            all_mutants = self.db.get_all_evaluated_mutants()
            total = len(all_mutants)
            killed = len([m for m in all_mutants if not m.survived])

            self.state.update_global_stats_from_batch(
                total_mutants=total,
                killed_mutants=killed,
                line_coverage=self.state.line_coverage,
                branch_coverage=self.state.branch_coverage,
            )

            # 同步测试数量
            all_tests = self.db.get_all_test_cases()
            self.state.total_tests = sum(len(tc.methods) for tc in all_tests)

            logger.debug(
                f"全局状态已同步: "
                f"{total} 变异体, {killed} 被击杀, "
                f"变异分数 {self.state.global_mutation_score:.1%}"
            )

        except Exception as e:
            logger.warning(f"同步全局状态失败: {e}")

    def _check_improvement(
        self,
        prev_mutation_score: float,
        prev_line_coverage: float,
        threshold: float,
    ) -> bool:
        """检查是否有显著改进"""
        mutation_delta = self.state.global_mutation_score - prev_mutation_score
        coverage_delta = self.state.line_coverage - prev_line_coverage

        has_improvement = mutation_delta >= threshold or coverage_delta >= threshold

        if has_improvement:
            logger.info(
                f"检测到改进: "
                f"变异分数 Δ{mutation_delta:+.1%}, "
                f"覆盖率 Δ{coverage_delta:+.1%}"
            )

        return has_improvement

    def _check_excellent_quality(self) -> bool:
        """检查是否达到优秀质量水平"""
        is_excellent = (
            self.state.global_mutation_score >= self.excellent_mutation_score
            and self.state.line_coverage >= self.excellent_line_coverage
            and self.state.branch_coverage >= self.excellent_branch_coverage
        )

        if is_excellent:
            logger.info(
                f"达到优秀质量: "
                f"变异分数 {self.state.global_mutation_score:.1%}, "
                f"行覆盖率 {self.state.line_coverage:.1%}, "
                f"分支覆盖率 {self.state.branch_coverage:.1%}"
            )

        return is_excellent

    def _should_stop(self) -> bool:
        """检查是否应该停止"""
        if self._interrupted:
            return True

        if self.state.iteration >= self.max_iterations:
            logger.info("达到最大迭代次数")
            return True

        if self.state.llm_calls >= self.budget:
            logger.info("达到 LLM 调用预算")
            return True

        return False

    def _log_final_summary(self) -> None:
        """记录最终总结"""
        logger.info("=" * 60)
        logger.info("并行协同进化最终总结")
        logger.info("=" * 60)
        logger.info(f"总批次数: {self.state.current_batch}")
        logger.info(f"LLM 调用次数: {self.state.llm_calls}/{self.budget}")
        logger.info("")
        logger.info("全局统计:")
        logger.info(f"  变异分数: {self.state.global_mutation_score:.1%}")
        logger.info(f"  总变异体数: {self.state.global_total_mutants}")
        logger.info(
            f"  已击杀: {self.state.global_killed_mutants}, "
            f"幸存: {self.state.global_survived_mutants}"
        )
        logger.info("")
        logger.info("覆盖率:")
        logger.info(f"  行覆盖率: {self.state.line_coverage:.1%}")
        logger.info(f"  分支覆盖率: {self.state.branch_coverage:.1%}")
        logger.info(f"  总测试数: {self.state.total_tests}")
        logger.info("")
        logger.info("并行统计:")
        for key, value in self.state.parallel_stats.items():
            logger.info(f"  {key}: {value}")
        logger.info("=" * 60)

    def save_state(self, file_path: str) -> None:
        """保存状态"""
        self.state.save(file_path)

    def load_state(self, file_path: str) -> bool:
        """加载状态"""
        state = ParallelAgentState.load(file_path)
        if state:
            self.state = state
            return True
        return False
