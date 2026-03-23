"""并行 Agent 调度器 - 批量并行处理 + 集中同步"""

import logging
import math
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..executor.coverage_parser import CoverageParser
from ..executor.java_executor import JavaExecutor
from ..llm.client import LLMClient
from ..store.database import Database
from ..utils.log_context import log_context, submit_with_log_context
from ..utils.method_keys import build_method_key
from ..utils.sandbox import SandboxManager
from .state import ParallelAgentState, WorkerResult
from .target_selector import TargetSelector
from .tools import AgentTools

logger = logging.getLogger(__name__)


def _format_exception_summary(error: Exception) -> str:
    message = str(error).strip()
    if message:
        return f"{type(error).__name__}: {message}"
    return type(error).__name__


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
        self.mutation_enabled = self._resolve_mutation_enabled()

        # 状态管理
        self.state: ParallelAgentState = ParallelAgentState()
        self.state.budget = budget
        self.state.global_mutation_enabled = self.mutation_enabled

        # 覆盖率解析器
        self.coverage_parser = CoverageParser()

        # 中断标志
        self._interrupted = False

        self._llm_calls_base = self.llm.get_total_calls()

    def run(
        self,
        stop_on_no_improvement_rounds: int = 3,
        min_improvement_threshold: float = 0.01,
    ) -> ParallelAgentState:
        """
        运行并行调度循环

        Args:
            stop_on_no_improvement_rounds: 无改进时停止的轮数
            min_improvement_threshold: 最小改进绝对阈值（0.01 表示提升 1 个百分点）

        Returns:
            最终状态
        """
        logger.info("=" * 60)
        logger.info("开始并行协同进化循环")
        logger.info(f"改进判定阈值（绝对增量）: {min_improvement_threshold:.2%}")
        logger.info(f"最大并行目标数: {self.max_parallel_targets}")
        logger.info(f"变异体评估并行度: {self.max_eval_workers}")
        logger.info(f"变异分析开关: {'启用' if self.mutation_enabled else '禁用'}")
        logger.info("=" * 60)

        from datetime import datetime

        self.state.start_time = datetime.now()
        no_improvement_count = 0
        prev_mutation_score = 0.0
        prev_line_coverage = 0.0

        try:
            while not self._should_stop():
                batch_num = self.state.current_batch + 1
                logger.info(f"\n{'=' * 60}")
                logger.info(f"批次 {batch_num}/{self.max_iterations}")
                logger.info(f"{'=' * 60}")

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

                logger.info(f"批次完成: {len(successful_results)} 成功, {len(failed_results)} 失败")

                # 将失败的目标加入黑名单
                for result in failed_results:
                    self.state.add_failed_target(
                        result.class_name,
                        result.method_name,
                        result.error or "处理失败",
                        result.method_signature,
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
                    mutation_score = (
                        self.state.global_mutation_score
                        if self.state.global_mutation_enabled
                        else None
                    )
                    mutation_score_delta = (
                        self.state.global_mutation_score - prev_mutation_score
                        if self.state.global_mutation_enabled
                        and self.state.global_mutation_score is not None
                        else None
                    )
                    self.state.add_improvement(
                        {
                            "batch": batch_num,
                            "mutation_score": mutation_score,
                            "line_coverage": self.state.line_coverage,
                            "mutation_score_delta": mutation_score_delta,
                            "coverage_delta": self.state.line_coverage - prev_line_coverage,
                        }
                    )
                else:
                    no_improvement_count += 1
                    if self._has_untried_frontier():
                        logger.info(
                            "本轮无显著改进，但仍有未尝试目标，继续探索 "
                            f"(当前连续 {no_improvement_count}/{stop_on_no_improvement_rounds} 轮)"
                        )
                    else:
                        logger.info(
                            f"无显著改进 (连续 {no_improvement_count}/{stop_on_no_improvement_rounds} 轮)"
                        )

                if (
                    self.state.global_mutation_enabled
                    and self.state.global_mutation_score is not None
                ):
                    prev_mutation_score = self.state.global_mutation_score
                prev_line_coverage = self.state.line_coverage

                # 10. 检查停止条件
                if no_improvement_count >= stop_on_no_improvement_rounds:
                    if self._has_untried_frontier():
                        logger.info(
                            f"连续 {no_improvement_count} 轮无改进，但仍有未尝试目标，继续探索"
                        )
                    else:
                        logger.info(f"连续 {no_improvement_count} 轮无改进，且前沿已耗尽，停止")
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
        blacklist = {
            target
            for target in (ft.get("target") for ft in self.state.failed_targets)
            if isinstance(target, str)
        }
        processed = {target for target in self.state.processed_targets if isinstance(target, str)}

        # 获取当前活跃的目标，避免重复选择
        active_targets = {
            target for target in self.state.get_active_targets() if isinstance(target, str)
        }
        exploration_slots = self._calculate_exploration_slots(blacklist | active_targets, processed)

        if exploration_slots > 0:
            logger.info(f"本批次保留 {exploration_slots} 个探索槽位给未尝试目标")

        for index in range(self.max_parallel_targets):
            require_unprocessed = index < exploration_slots
            # 使用目标选择器选择下一个目标
            target = self.target_selector.select(
                criteria="coverage",
                blacklist=blacklist | active_targets,
                processed_targets=processed,
                require_unprocessed=require_unprocessed,
            )

            if require_unprocessed and (not target or not target.get("class_name")):
                target = self.target_selector.select(
                    criteria="coverage",
                    blacklist=blacklist | active_targets,
                    processed_targets=processed,
                )

            if not target or not target.get("class_name"):
                break

            class_name = target["class_name"]
            method_name = target.get("method_name", "")
            method_signature = target.get("method_signature")
            target_id = build_method_key(class_name, method_name, method_signature)

            # 尝试获取目标（原子操作）
            if self.state.acquire_target(
                class_name,
                method_name,
                method_signature=method_signature,
                metadata=target,
            ):
                targets.append(target)
                active_targets.add(target_id)
            else:
                # 目标已被占用，加入临时黑名单继续选择
                blacklist.add(target_id)

        return targets

    def _calculate_exploration_slots(self, blacklist: set[str], processed_targets: set[str]) -> int:
        if not self.target_selector.has_unprocessed_target(
            criteria="coverage",
            blacklist=blacklist,
            processed_targets=processed_targets,
        ):
            return 0

        return min(self.max_parallel_targets, max(1, math.ceil(self.max_parallel_targets * 0.25)))

    def _has_untried_frontier(self) -> bool:
        blacklist = {
            target
            for target in (
                failed_target.get("target") for failed_target in self.state.failed_targets
            )
            if isinstance(target, str)
        }
        processed = {target for target in self.state.processed_targets if isinstance(target, str)}
        active_targets = {
            target for target in self.state.get_active_targets() if isinstance(target, str)
        }
        return self.target_selector.has_unprocessed_target(
            criteria="coverage",
            blacklist=blacklist | active_targets,
            processed_targets=processed,
        )

    def _process_targets_parallel(self, targets: List[Dict[str, Any]]) -> List[WorkerResult]:
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
                executor.submit(self._process_single_target, target): target for target in targets
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
                            target_id=build_method_key(
                                target.get("class_name", ""),
                                target.get("method_name", ""),
                                target.get("method_signature"),
                            ),
                            class_name=target.get("class_name", ""),
                            method_name=target.get("method_name", ""),
                            method_signature=target.get("method_signature"),
                            success=False,
                            error=str(e),
                            method_coverage=target.get("method_coverage"),
                        )
                    )
                finally:
                    target_id = build_method_key(
                        target.get("class_name", ""),
                        target.get("method_name", ""),
                        target.get("method_signature"),
                    )
                    matched_result = next(
                        (r for r in results if r.target_id == target_id),
                        None,
                    )
                    # 释放目标
                    self.state.release_target(
                        target.get("class_name", ""),
                        target.get("method_name", ""),
                        target.get("method_signature"),
                        success=bool(matched_result and matched_result.success),
                        result=matched_result,
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
        method_signature = target.get("method_signature")
        target_id = build_method_key(class_name, method_name, method_signature)

        with log_context(target_id):
            return self._process_single_target_impl(target, class_name, method_name, target_id)

    def _process_single_target_impl(
        self, target: Dict[str, Any], class_name: str, method_name: str, target_id: str
    ) -> WorkerResult:
        """_process_single_target 的实际实现"""
        start_time = time.time()
        mutation_enabled = self._is_mutation_enabled()

        logger.info(f"开始处理: {target_id}")

        result = WorkerResult(
            target_id=target_id,
            class_name=class_name,
            method_name=method_name,
            method_signature=target.get("method_signature"),
            method_coverage=target.get("method_coverage"),
            mutation_enabled=mutation_enabled,
        )

        # 为此目标创建独立沙箱
        sandbox_path = None
        sandbox_id = None
        cleanup_deferred = False

        try:
            sandbox_path = self.sandbox_manager.create_target_sandbox(
                self.project_path, class_name, method_name
            )
            # 从路径中提取 sandbox_id（路径的最后一部分）
            sandbox_id = Path(sandbox_path).name
            logger.debug(f"创建沙箱: {sandbox_path} (id: {sandbox_id})")

            test_result = None
            mutant_result = None
            mutant_future: Future[Any] | None = None

            gen_executor = ThreadPoolExecutor(max_workers=2)
            try:
                test_future = submit_with_log_context(
                    gen_executor,
                    self._generate_tests_in_sandbox,
                    sandbox_path,
                    class_name,
                    method_name,
                    target.get("method_signature"),
                )
                if mutation_enabled:
                    mutant_future = submit_with_log_context(
                        gen_executor,
                        self._generate_mutants_in_sandbox,
                        sandbox_path,
                        class_name,
                        method_name,
                        target.get("method_signature"),
                    )

                # 获取结果
                generation_failed = False
                try:
                    test_result = test_future.result(timeout=180)
                except FutureTimeoutError:
                    logger.warning(f"测试生成超时: {target_id} (timeout=180s)")
                    generation_failed = True
                except Exception as e:
                    logger.warning(f"测试生成异常: {target_id} ({_format_exception_summary(e)})")
                    generation_failed = True

                if self._should_stop_generation_wait(
                    class_name,
                    method_name,
                    target.get("method_signature"),
                    test_result,
                    generation_failed,
                ):
                    if mutant_future is not None:
                        self._cancel_future_if_possible(mutant_future, target_id, "变异体生成")
                    cleanup_deferred = self._defer_sandbox_cleanup_if_needed(
                        sandbox_id,
                        [future for future in [test_future, mutant_future] if future is not None],
                        target_id,
                    )
                    gen_executor.shutdown(wait=False, cancel_futures=True)
                else:
                    if not mutation_enabled:
                        gen_executor.shutdown(wait=True)
                    else:
                        mutant_generation_timed_out = False
                        if mutant_future is not None:
                            try:
                                mutant_result = mutant_future.result(timeout=180)
                            except FutureTimeoutError:
                                logger.warning(f"变异体生成超时: {target_id} (timeout=180s)")
                                mutant_generation_timed_out = True
                            except Exception as e:
                                logger.warning(
                                    f"变异体生成异常: {target_id} ({_format_exception_summary(e)})"
                                )
                        if mutant_generation_timed_out and mutant_future is not None:
                            self._cancel_future_if_possible(mutant_future, target_id, "变异体生成")
                            cleanup_deferred = self._defer_sandbox_cleanup_if_needed(
                                sandbox_id,
                                [
                                    future
                                    for future in [test_future, mutant_future]
                                    if future is not None
                                ],
                                target_id,
                            )
                            gen_executor.shutdown(wait=False, cancel_futures=True)
                        else:
                            gen_executor.shutdown(wait=True)
            except Exception:
                gen_executor.shutdown(wait=False, cancel_futures=True)
                raise

            # 处理测试生成结果
            if test_result:
                result.tests_generated = test_result.get("generated", 0)
                result.test_files = test_result.get("test_files", {})

            if result.tests_generated == 0:
                coverage = self.db.get_method_coverage(
                    class_name,
                    method_name,
                    target.get("method_signature"),
                )
                if coverage is not None:
                    result.method_coverage = coverage.line_coverage_rate
                result.error = "测试生成失败"
                logger.warning("测试生成失败")
                return result

            if not mutation_enabled:
                coverage = self.db.get_method_coverage(
                    class_name,
                    method_name,
                    target.get("method_signature"),
                )
                if coverage is not None:
                    result.method_coverage = coverage.line_coverage_rate
                result.mutants_generated = None
                result.mutants_evaluated = None
                result.mutants_killed = None
                result.local_mutation_score = None
                result.success = True
                result.processing_time = time.time() - start_time
                logger.info(
                    f"完成(test-only): "
                    f"{result.tests_generated} 测试, "
                    f"覆盖率 {result.method_coverage if result.method_coverage is not None else 'N/A'}, "
                    f"耗时 {result.processing_time:.1f}s"
                )
                return result

            # 处理变异体生成结果
            if mutant_result:
                result.mutants_generated = mutant_result.get("generated", 0)

            if result.mutants_generated == 0:
                coverage = self.db.get_method_coverage(
                    class_name,
                    method_name,
                    target.get("method_signature"),
                )
                if coverage is not None:
                    result.method_coverage = coverage.line_coverage_rate
                # 没有变异体也算成功（可能方法太简单）
                logger.info("没有生成变异体")
                result.success = True
                return result

            # 2. 评估变异体（在独立沙箱中）
            eval_result = self._evaluate_in_sandbox(
                sandbox_path,
                class_name,
                method_name,
                target.get("method_signature"),
            )
            if eval_result:
                result.mutants_evaluated = eval_result.get("evaluated", 0)
                result.mutants_killed = eval_result.get("killed", 0)
                result.local_mutation_score = eval_result.get("mutation_score", 0.0)

            coverage = self.db.get_method_coverage(
                class_name,
                method_name,
                target.get("method_signature"),
            )
            if coverage is not None:
                result.method_coverage = coverage.line_coverage_rate

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
            if sandbox_id and not cleanup_deferred:
                try:
                    self.sandbox_manager.cleanup_sandbox(sandbox_id)
                except Exception as e:
                    logger.warning(f"清理沙箱失败: {e}")

        return result

    def _should_stop_generation_wait(
        self,
        class_name: str,
        method_name: str,
        method_signature: Optional[str],
        test_result: Optional[Dict[str, Any]],
        generation_failed: bool,
    ) -> bool:
        if generation_failed:
            return True
        if not test_result or test_result.get("generated", 0) <= 0:
            return True
        return self._is_target_blacklisted(class_name, method_name, method_signature)

    def _is_target_blacklisted(
        self,
        class_name: str,
        method_name: str,
        method_signature: Optional[str],
    ) -> bool:
        target_id = build_method_key(class_name, method_name, method_signature)
        return any(ft.get("target") == target_id for ft in self.state.failed_targets)

    def _cancel_future_if_possible(self, future: Future[Any], target_id: str, label: str) -> None:
        if future.done():
            return
        if future.cancel():
            logger.info(f"{label}任务已取消: {target_id}")
            return
        logger.info(f"{label}任务已在运行，停止等待: {target_id}")

    def _defer_sandbox_cleanup_if_needed(
        self,
        sandbox_id: Optional[str],
        futures: List[Future[Any]],
        target_id: str,
    ) -> bool:
        if not sandbox_id:
            return False

        pending_futures = [future for future in futures if not future.done()]
        if not pending_futures:
            return False

        remaining = len(pending_futures)
        lock = threading.Lock()
        cleaned = False

        def cleanup_when_safe(_future: Future[Any]) -> None:
            nonlocal remaining, cleaned
            should_cleanup = False
            with lock:
                remaining -= 1
                if remaining == 0 and not cleaned:
                    cleaned = True
                    should_cleanup = True
            if not should_cleanup:
                return
            try:
                self.sandbox_manager.cleanup_sandbox(sandbox_id)
                logger.info(f"后台任务结束后已延迟清理沙箱: {target_id}")
            except Exception as error:
                logger.warning(f"延迟清理沙箱失败 {sandbox_id}: {error}")

        for future in pending_futures:
            future.add_done_callback(cleanup_when_safe)

        logger.info(f"检测到后台生成任务仍在运行，延迟清理沙箱: {target_id}")
        return True

    def _generate_tests_in_sandbox(
        self,
        sandbox_path: str,
        class_name: str,
        method_name: str,
        method_signature: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """在沙箱中生成测试"""
        try:
            # 检查是否已有测试
            existing_tests = self.db.get_tests_by_target_method(
                class_name,
                method_name,
                method_signature,
            )
            if existing_tests:
                # 已有测试，读取测试文件内容
                test_files = {}
                for tc in existing_tests:
                    test_file_path = Path(sandbox_path) / "src" / "test" / "java"
                    if tc.package_name:
                        test_file_path = test_file_path / tc.package_name.replace(".", "/")
                    test_file_path = test_file_path / f"{tc.class_name}.java"
                    if test_file_path.exists():
                        test_files[str(test_file_path)] = test_file_path.read_text()

                return {
                    "generated": sum(len(tc.methods) for tc in existing_tests),
                    "test_files": test_files,
                }

            # 调用工具生成测试
            result = self.tools.call(
                "generate_tests",
                class_name=class_name,
                method_name=method_name,
                method_signature=method_signature,
            )

            if result and result.get("generated", 0) > 0:
                # 收集生成的测试文件（只收集与当前目标相关的）
                test_files = self._collect_test_files(sandbox_path, class_name, method_name)
                result["test_files"] = test_files

            return result

        except Exception as e:
            logger.warning(f"生成测试失败: {e}")
            return None

    def _generate_mutants_in_sandbox(
        self,
        sandbox_path: str,
        class_name: str,
        method_name: str,
        method_signature: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """在沙箱中生成变异体"""
        try:
            # 检查是否已有变异体
            existing_mutants = self.db.get_mutants_by_method(
                class_name,
                method_name,
                status="valid",
                method_signature=method_signature,
            )
            if existing_mutants:
                return {"generated": len(existing_mutants)}

            # 调用工具生成变异体
            result = self.tools.call(
                "generate_mutants",
                class_name=class_name,
                method_name=method_name,
                method_signature=method_signature,
            )
            return result

        except Exception as e:
            logger.warning(f"生成变异体失败: {e}")
            return None

    def _evaluate_in_sandbox(
        self,
        sandbox_path: str,
        class_name: str,
        method_name: str,
        method_signature: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """在沙箱中评估变异体"""
        try:
            from ..utils.project_utils import write_test_file

            # 获取变异体和测试用例
            mutants = self.db.get_mutants_by_method(
                class_name,
                method_name,
                status="valid",
                method_signature=method_signature,
            )
            test_cases = self.db.get_tests_by_target_method(
                class_name,
                method_name,
                method_signature,
            )

            if not mutants or not test_cases:
                return {"evaluated": 0, "killed": 0, "mutation_score": 0.0}

            # 关键修复：将测试文件写入 Worker 沙箱
            # 这样变异体沙箱（从 Worker 沙箱复制）才会包含测试文件
            formatting_enabled, formatting_style = self.tools._get_formatting_config()
            for tc in test_cases:
                if not tc.full_code:
                    logger.warning(f"测试用例缺少完整代码，跳过写入: {tc.id}")
                    continue

                _ = write_test_file(
                    project_path=sandbox_path,
                    package_name=tc.package_name or "",
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
            evaluator.build_kill_matrix(
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
            is_target_file = any(file_name.startswith(pattern) for pattern in target_patterns)

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

            self.sync_workspace_coverage()

        except Exception as e:
            logger.warning(f"收集覆盖率失败: {e}")

    def sync_workspace_coverage(
        self, iteration: Optional[int] = None, wait_for_report: bool = True
    ) -> bool:
        jacoco_path = Path(self.workspace_path) / "target" / "site" / "jacoco" / "jacoco.xml"
        return self._sync_coverage_from_report(
            jacoco_path, iteration=iteration, wait_for_report=wait_for_report
        )

    def _sync_coverage_from_report(
        self,
        jacoco_path: Path,
        iteration: Optional[int] = None,
        wait_for_report: bool = True,
    ) -> bool:
        max_wait_attempts = 5 if wait_for_report else 1
        file_found = False

        for attempt in range(max_wait_attempts):
            if jacoco_path.exists():
                file_found = True
                break

            if attempt < max_wait_attempts - 1:
                logger.debug(f"等待 JaCoCo 报告生成... (尝试 {attempt + 1}/{max_wait_attempts})")
                time.sleep(0.5)

        if not file_found:
            logger.warning(
                f"JaCoCo 报告在等待 {max_wait_attempts * 0.5:.1f}秒 后仍不存在: {jacoco_path}"
            )
            return False

        coverage_iteration = self.state.iteration if iteration is None else iteration

        method_coverages = self.coverage_parser.parse_jacoco_xml_with_lines(str(jacoco_path))
        for cov in method_coverages:
            self.db.save_method_coverage(cov, coverage_iteration)

        logger.info(f"已保存 {len(method_coverages)} 个方法的覆盖率数据")
        if not method_coverages:
            logger.warning("JaCoCo 报告已生成，但未解析到任何方法级覆盖率数据")

        coverage = self.coverage_parser.aggregate_global_coverage_from_xml(str(jacoco_path))
        if coverage:
            self.state.line_coverage = coverage.get("line_coverage", 0.0)
            self.state.branch_coverage = coverage.get("branch_coverage", 0.0)
            logger.info(
                f"全局覆盖率: 行 {self.state.line_coverage:.1%}, "
                f"分支 {self.state.branch_coverage:.1%}"
            )
            return True

        logger.warning(f"无法从 JaCoCo 报告聚合全局覆盖率: {jacoco_path}")
        return bool(method_coverages)

    def _sync_global_state(self) -> None:
        """同步全局状态"""
        try:
            mutation_enabled = self._is_mutation_enabled()
            total = 0
            killed = 0
            if mutation_enabled:
                # 从数据库获取全局变异体统计
                all_mutants = self.db.get_all_evaluated_mutants()
                total = len(all_mutants)
                killed = len([m for m in all_mutants if not m.survived])

            self.state.update_global_stats_from_batch(
                total_mutants=total,
                killed_mutants=killed,
                line_coverage=self.state.line_coverage,
                branch_coverage=self.state.branch_coverage,
                mutation_enabled=mutation_enabled,
            )

            # 同步测试数量
            all_tests = self.db.get_all_test_cases()
            self.state.total_tests = sum(len(tc.methods) for tc in all_tests)

            logger.debug(
                f"全局状态已同步: "
                f"覆盖率(行/分支)=({self.state.line_coverage:.1%}/{self.state.branch_coverage:.1%}), "
                f"变异分析={'启用' if self.state.global_mutation_enabled else '禁用'}"
            )

        except Exception as e:
            logger.warning(f"同步全局状态失败: {e}")

    def _check_improvement(
        self,
        prev_mutation_score: float,
        prev_line_coverage: float,
        threshold: float,
    ) -> bool:
        """检查是否有显著改进（绝对增量阈值）"""
        mutation_enabled = self._is_mutation_enabled()
        current_mutation_score = self.state.global_mutation_score
        mutation_delta = (
            current_mutation_score - prev_mutation_score
            if mutation_enabled and current_mutation_score is not None
            else 0.0
        )
        coverage_delta = self.state.line_coverage - prev_line_coverage

        has_improvement = coverage_delta >= threshold
        if mutation_enabled:
            has_improvement = has_improvement or mutation_delta >= threshold

        if has_improvement:
            logger.info(
                f"检测到改进: 变异分数 Δ{mutation_delta:+.1%}, 覆盖率 Δ{coverage_delta:+.1%}"
            )
        else:
            logger.debug(
                f"未达到显著改进阈值（绝对增量）: "
                f"变异分数Δ{mutation_delta:+.1%}, "
                f"覆盖率Δ{coverage_delta:+.1%}, "
                f"阈值 {threshold:.1%}"
            )

        return has_improvement

    def _check_excellent_quality(self) -> bool:
        """检查是否达到优秀质量水平"""
        mutation_enabled = self._is_mutation_enabled()
        current_mutation_score = self.state.global_mutation_score
        if mutation_enabled:
            is_excellent = (
                current_mutation_score is not None
                and current_mutation_score >= self.excellent_mutation_score
                and self.state.line_coverage >= self.excellent_line_coverage
                and self.state.branch_coverage >= self.excellent_branch_coverage
            )
        else:
            is_excellent = (
                self.state.line_coverage >= self.excellent_line_coverage
                and self.state.branch_coverage >= self.excellent_branch_coverage
            )

        if is_excellent:
            if mutation_enabled and self.state.global_mutation_score is not None:
                logger.info(
                    f"达到优秀质量: "
                    f"变异分数 {self.state.global_mutation_score:.1%}, "
                    f"行覆盖率 {self.state.line_coverage:.1%}, "
                    f"分支覆盖率 {self.state.branch_coverage:.1%}"
                )
            else:
                logger.info(
                    f"达到优秀质量(test-only): "
                    f"行覆盖率 {self.state.line_coverage:.1%}, "
                    f"分支覆盖率 {self.state.branch_coverage:.1%}"
                )

        return is_excellent

    def _should_stop(self) -> bool:
        """检查是否应该停止"""
        current_llm_calls = self.llm.get_total_calls() - self._llm_calls_base
        if current_llm_calls > self.state.llm_calls:
            _ = self.state.increment_llm_calls(current_llm_calls - self.state.llm_calls)

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
        if self.state.global_mutation_enabled:
            logger.info(f"  变异分数: {self.state.global_mutation_score:.1%}")
            logger.info(f"  总变异体数: {self.state.global_total_mutants}")
            logger.info(
                f"  已击杀: {self.state.global_killed_mutants}, "
                f"幸存: {self.state.global_survived_mutants}"
            )
        else:
            logger.info("  变异分析: 未启用")
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

    def _resolve_mutation_enabled(self) -> bool:
        mutation_enabled = True
        tools = getattr(self, "tools", None)
        tools_config = getattr(tools, "config", None)
        if tools_config is not None:
            try:
                mutation_enabled = bool(tools_config.evolution.mutation_enabled)
            except AttributeError:
                mutation_enabled = True
        return mutation_enabled

    def _is_mutation_enabled(self) -> bool:
        if hasattr(self, "mutation_enabled"):
            return bool(self.mutation_enabled)
        self.mutation_enabled = self._resolve_mutation_enabled()
        return bool(self.mutation_enabled)

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
