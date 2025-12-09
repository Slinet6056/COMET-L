"""Agent 调度器"""

import json
import logging
from typing import Optional, Dict, Any

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
from ..utils.json_utils import extract_json_from_response
from .state import AgentState
from .tools import AgentTools

logger = logging.getLogger(__name__)


class PlannerAgent:
    """调度器 Agent - 协调测试和变异协同进化"""

    def __init__(
        self,
        llm_client: LLMClient,
        tools: AgentTools,
        max_iterations: int = 10,
        budget: int = 1000,
        excellent_mutation_score: float = 0.95,
        excellent_line_coverage: float = 0.90,
        excellent_branch_coverage: float = 0.85,
    ):
        """
        初始化调度器

        Args:
            llm_client: LLM 客户端
            tools: 工具集
            max_iterations: 最大迭代次数
            budget: LLM 调用预算
            excellent_mutation_score: 优秀变异分数阈值（默认 0.95）
            excellent_line_coverage: 优秀行覆盖率阈值（默认 0.90）
            excellent_branch_coverage: 优秀分支覆盖率阈值（默认 0.85）
        """
        self.llm = llm_client
        self.tools = tools
        self.prompt_manager = PromptManager()
        self.max_iterations = max_iterations
        self.budget = budget

        # 优秀水平阈值（可配置）
        self.excellent_mutation_score = excellent_mutation_score
        self.excellent_line_coverage = excellent_line_coverage
        self.excellent_branch_coverage = excellent_branch_coverage

        self.state = AgentState()
        self.state.budget = budget

    def run(
        self,
        stop_on_no_improvement_rounds: int = 3,
        min_improvement_threshold: float = 0.01,
    ) -> AgentState:
        """
        运行调度循环

        Args:
            stop_on_no_improvement_rounds: 无改进时停止的轮数
            min_improvement_threshold: 最小改进阈值

        Returns:
            最终状态
        """
        logger.info("开始协同进化循环")
        no_improvement_count = 0

        # 记录上一轮的关键指标
        prev_mutation_score = 0.0
        prev_line_coverage = 0.0

        while not self._should_stop():
            logger.info(f"{'='*60}")
            logger.info(f"迭代 {self.state.iteration + 1}/{self.max_iterations}")
            logger.info(f"{'='*60}")

            # 从数据库同步状态
            self._sync_state_from_db()

            # 检查是否达到优秀水平（可以提前停止）
            if self._check_excellent_quality():
                logger.info("已达到优秀质量水平，提前结束")
                break

            # LLM 决策
            decision = self._make_decision()

            if not decision:
                logger.warning("决策失败，跳过本轮")
                self.state.iteration += 1
                continue

            # 检查 agent 是否建议停止
            if decision.get("action") == "stop" or decision.get("should_stop", False):
                logger.info(f"Agent 决策停止: {decision.get('reasoning', '无理由')}")
                break

            # 执行工具
            action = decision.get("action")
            result = self._execute_tool(decision)

            # 检查 select_target 的结果，如果没有可选目标则停止
            if action == "select_target":
                if not result or not result.get("class_name"):
                    logger.info("select_target 返回空结果，没有更多可选目标，停止")
                    break

            # 更新迭代计数
            self.state.iteration += 1

            # 只有在执行评估后才检查改进
            # 因为只有 run_evaluation 会更新变异分数和覆盖率等指标
            if action == "run_evaluation":
                # 检查评估是否成功执行
                evaluation_succeeded = (
                    result is not None
                    and isinstance(result, dict)
                    and result.get("evaluated", 0) > 0
                )

                if not evaluation_succeeded:
                    logger.warning("评估执行失败或没有评估任何变异体，跳过改进检查")
                    # 同步状态但不检查改进
                    self._sync_state_from_db()
                else:
                    # 评估成功，再次同步状态以获取最新指标
                    self._sync_state_from_db()

                    # 检查改进（使用全局指标）
                    has_improvement = self._check_improvement(
                        prev_mutation_score,
                        prev_line_coverage,
                        min_improvement_threshold,
                    )

                    if has_improvement:
                        logger.info(f"检测到改进，重置无改进计数器")
                        no_improvement_count = 0
                        # 记录改进（使用全局指标）
                        self.state.add_improvement(
                            {
                                "iteration": self.state.iteration,
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
                            f"评估后无显著改进 (连续 {no_improvement_count}/{stop_on_no_improvement_rounds} 轮)"
                        )

                    # 更新上一轮指标（只在评估后更新，使用全局指标）
                    prev_mutation_score = self.state.global_mutation_score
                    prev_line_coverage = self.state.line_coverage
            else:
                # 非评估操作，同步状态但不检查改进
                self._sync_state_from_db()

            # 检查停止条件：连续多轮无改进
            if no_improvement_count >= stop_on_no_improvement_rounds:
                logger.info(f"连续 {no_improvement_count} 轮无改进")

                # 将当前目标加入黑名单并重新选择
                if self.state.current_target and self.state.current_target.get(
                    "class_name"
                ):
                    current_class = self.state.current_target["class_name"]
                    current_method = self.state.current_target.get("method_name", "")

                    logger.info(
                        f"将当前目标 {current_class}.{current_method} 加入黑名单"
                    )
                    self.state.add_failed_target(
                        current_class,
                        current_method,
                        f"连续 {no_improvement_count} 轮无改进",
                    )

                    # 重置无改进计数器
                    no_improvement_count = 0

                    # 尝试重新选择目标
                    logger.info("尝试重新选择新目标...")
                    new_target = self.tools.select_target(criteria="coverage")

                    # 检查是否还有可选目标
                    if not new_target or not new_target.get("class_name"):
                        logger.info("没有更多可选目标，停止")
                        break

                    logger.info(
                        f"已切换到新目标: {new_target.get('class_name')}.{new_target.get('method_name')}"
                    )

                    # 确保状态已更新（虽然 select_target 内部也会更新，但显式调用确保一致性）
                    self.state.update_target(new_target)

                    # 更新当前方法覆盖率
                    if "method_coverage" in new_target:
                        self.state.current_method_coverage = new_target[
                            "method_coverage"
                        ]

                    # 记录切换动作
                    self.state.add_action(
                        action="select_target",
                        params={},
                        success=True,
                        result=new_target,
                    )
                else:
                    # 没有当前目标，直接停止
                    logger.info("没有当前目标，停止")
                    break

        logger.info("协同进化循环结束")
        self._log_final_summary()
        return self.state

    def _should_stop(self) -> bool:
        """检查是否应该停止"""
        if self.state.iteration >= self.max_iterations:
            logger.info("达到最大迭代次数")
            return True

        if self.state.llm_calls >= self.budget:
            logger.info("达到 LLM 调用预算")
            return True

        return False

    def _check_improvement(
        self,
        prev_mutation_score: float,
        prev_line_coverage: float,
        threshold: float = 0.01,
    ) -> bool:
        """
        检查是否有显著改进（使用全局指标）

        Args:
            prev_mutation_score: 上一轮的全局变异分数
            prev_line_coverage: 上一轮的全局行覆盖率
            threshold: 改进阈值

        Returns:
            是否有显著改进
        """
        mutation_score_delta = self.state.global_mutation_score - prev_mutation_score
        coverage_delta = self.state.line_coverage - prev_line_coverage

        has_improvement = (
            mutation_score_delta >= threshold or coverage_delta >= threshold
        )

        if has_improvement:
            logger.info(
                f"检测到改进（全局指标）: "
                f"变异分数 {prev_mutation_score:.1%} -> {self.state.global_mutation_score:.1%} (Δ{mutation_score_delta:+.1%}), "
                f"行覆盖率 {prev_line_coverage:.1%} -> {self.state.line_coverage:.1%} (Δ{coverage_delta:+.1%})"
            )

        return has_improvement

    def _check_excellent_quality(self) -> bool:
        """
        检查是否达到优秀质量水平（可提前结束）
        使用全局指标评估整个项目的质量

        Returns:
            是否达到优秀水平
        """
        is_excellent = (
            self.state.global_mutation_score >= self.excellent_mutation_score
            and self.state.line_coverage >= self.excellent_line_coverage
            and self.state.branch_coverage >= self.excellent_branch_coverage
        )

        if is_excellent:
            logger.info(
                f"达到优秀质量水平（全局指标）: "
                f"全局变异分数={self.state.global_mutation_score:.1%} (阈值≥{self.excellent_mutation_score:.1%}), "
                f"全局行覆盖率={self.state.line_coverage:.1%} (阈值≥{self.excellent_line_coverage:.1%}), "
                f"全局分支覆盖率={self.state.branch_coverage:.1%} (阈值≥{self.excellent_branch_coverage:.1%})"
            )

        return is_excellent

    def _log_final_summary(self) -> None:
        """记录最终总结"""
        logger.info(f"{'='*60}")
        logger.info("协同进化最终总结")
        logger.info(f"{'='*60}")
        logger.info(f"总迭代次数: {self.state.iteration}")
        logger.info(f"LLM 调用次数: {self.state.llm_calls}/{self.budget}")
        logger.info("")
        logger.info("全局统计（所有目标的累积）:")
        logger.info(f"  变异分数: {self.state.global_mutation_score:.1%}")
        logger.info(f"  总变异体数: {self.state.global_total_mutants}")
        logger.info(
            f"  已击杀: {self.state.global_killed_mutants}, 幸存: {self.state.global_survived_mutants}"
        )
        logger.info("")
        logger.info("覆盖率:")
        logger.info(f"  行覆盖率: {self.state.line_coverage:.1%}")
        logger.info(f"  分支覆盖率: {self.state.branch_coverage:.1%}")
        logger.info(f"  总测试数: {self.state.total_tests}")

        if self.state.recent_improvements:
            logger.info("")
            logger.info(f"改进历史 (最近 {len(self.state.recent_improvements)} 次):")
            for imp in self.state.recent_improvements:
                logger.info(
                    f"  迭代 {imp['iteration']}: "
                    f"变异分数 Δ{imp.get('mutation_score_delta', 0):+.1%}, "
                    f"覆盖率 Δ{imp.get('coverage_delta', 0):+.1%}"
                )
        logger.info(f"{'='*60}")

    def _sync_state_from_db(self) -> None:
        """从数据库同步状态信息"""
        if not self.tools.db:
            return

        try:
            # ===== 全局统计（包括所有已评估的变异体：valid + outdated）=====
            all_evaluated_mutants = self.tools.db.get_all_evaluated_mutants()
            self.state.global_total_mutants = len(all_evaluated_mutants)
            self.state.global_survived_mutants = len(
                [m for m in all_evaluated_mutants if m.survived]
            )
            self.state.global_killed_mutants = (
                self.state.global_total_mutants - self.state.global_survived_mutants
            )

            # 计算全局变异分数
            if self.state.global_total_mutants > 0:
                self.state.global_mutation_score = (
                    self.state.global_killed_mutants / self.state.global_total_mutants
                )
            else:
                self.state.global_mutation_score = 0.0

            # ===== 当前目标统计（仅当前目标方法的 valid 变异体）=====
            current_target = self.state.current_target
            if (
                current_target
                and current_target.get("class_name")
                and current_target.get("method_name")
            ):
                # 获取当前目标方法的 valid 变异体
                current_mutants = self.tools.db.get_mutants_by_method(
                    class_name=current_target["class_name"],
                    method_name=current_target["method_name"],
                    status="valid",
                )
                self.state.total_mutants = len(current_mutants)
                self.state.survived_mutants = len(
                    [m for m in current_mutants if m.survived]
                )
                self.state.killed_mutants = (
                    self.state.total_mutants - self.state.survived_mutants
                )

                # 计算当前目标变异分数
                if self.state.total_mutants > 0:
                    self.state.mutation_score = (
                        self.state.killed_mutants / self.state.total_mutants
                    )
                else:
                    self.state.mutation_score = 0.0
            else:
                # 没有当前目标时，使用所有 valid 变异体
                valid_mutants = self.tools.db.get_valid_mutants()
                self.state.total_mutants = len(valid_mutants)
                self.state.survived_mutants = len(
                    [m for m in valid_mutants if m.survived]
                )
                self.state.killed_mutants = (
                    self.state.total_mutants - self.state.survived_mutants
                )

                # 计算变异分数
                if self.state.total_mutants > 0:
                    self.state.mutation_score = (
                        self.state.killed_mutants / self.state.total_mutants
                    )
                else:
                    self.state.mutation_score = 0.0

            # 获取覆盖率统计（直接从 JaCoCo XML 文件读取，最准确）
            try:
                if not self.tools.project_path:
                    logger.debug("project_path 未设置，跳过覆盖率同步")
                else:
                    from pathlib import Path
                    from ..executor.coverage_parser import CoverageParser

                    parser = CoverageParser()

                    jacoco_path = (
                        Path(self.tools.project_path)
                        / "target"
                        / "site"
                        / "jacoco"
                        / "jacoco.xml"
                    )
                    if jacoco_path.exists():
                        # 直接从 XML 文件读取全局覆盖率（最准确的方式）
                        global_coverage = parser.aggregate_global_coverage_from_xml(
                            str(jacoco_path)
                        )

                        if (
                            global_coverage
                            and "line_coverage" in global_coverage
                            and "branch_coverage" in global_coverage
                        ):
                            self.state.line_coverage = global_coverage["line_coverage"]
                            self.state.branch_coverage = global_coverage[
                                "branch_coverage"
                            ]

                            logger.debug(
                                f"同步全局覆盖率（从 XML）: 行 {self.state.line_coverage:.1%}, "
                                f"分支 {self.state.branch_coverage:.1%}"
                            )
                        else:
                            logger.warning(
                                f"从 XML 解析的覆盖率数据格式不正确: {global_coverage}"
                            )
                            # 如果解析失败，保持当前值不变（不重置为0）
                    else:
                        logger.debug(
                            f"JaCoCo XML 报告不存在: {jacoco_path}，保持当前覆盖率值"
                        )
                        # 如果文件不存在，保持当前值不变（不重置为0），因为可能是还没有运行过评估

                    # 同步当前目标方法的覆盖率
                    if (
                        self.state.current_target
                        and self.state.current_target.get("class_name")
                        and self.state.current_target.get("method_name")
                    ):
                        current_coverage = self.tools.db.get_method_coverage(
                            self.state.current_target["class_name"],
                            self.state.current_target["method_name"],
                        )
                        if current_coverage:
                            self.state.current_method_coverage = (
                                current_coverage.line_coverage_rate
                            )
                            logger.debug(
                                f"已更新当前方法覆盖率: {self.state.current_method_coverage:.1%}"
                            )
            except Exception as e:
                logger.warning(f"同步覆盖率数据失败: {e}", exc_info=True)
                # 异常时保持当前值不变，不重置为0

            # 同步测试数量
            try:
                all_tests = self.tools.db.get_all_test_cases()
                self.state.total_tests = sum(len(tc.methods) for tc in all_tests)
                logger.debug(f"已更新测试总数: {self.state.total_tests}")
            except Exception as e:
                logger.warning(f"同步测试数量失败: {e}")

            # 显示全局统计和当前目标统计
            if self.state.current_target:
                logger.debug(
                    f"状态已同步: 全局 {self.state.global_total_mutants} 个变异体 "
                    f"(击杀 {self.state.global_killed_mutants}, 幸存 {self.state.global_survived_mutants}, "
                    f"分数 {self.state.global_mutation_score:.1%}), "
                    f"当前目标 {self.state.total_mutants} 个变异体 "
                    f"(击杀 {self.state.killed_mutants}, 幸存 {self.state.survived_mutants}, "
                    f"分数 {self.state.mutation_score:.1%}), "
                    f"全局行覆盖率={self.state.line_coverage:.1%}, 全局分支覆盖率={self.state.branch_coverage:.1%}"
                )
            else:
                logger.debug(
                    f"状态已同步: 全局 {self.state.global_total_mutants} 个变异体 "
                    f"(击杀 {self.state.global_killed_mutants}, 幸存 {self.state.global_survived_mutants}, "
                    f"分数 {self.state.global_mutation_score:.1%}), "
                    f"全局行覆盖率={self.state.line_coverage:.1%}, 全局分支覆盖率={self.state.branch_coverage:.1%}"
                )
        except Exception as e:
            logger.warning(f"同步状态失败: {e}")

    def _make_decision(self) -> Optional[Dict[str, Any]]:
        """
        使用 LLM 做决策

        Returns:
            决策字典 {action, params, reasoning}
        """
        try:
            # 动态获取工具描述
            tools_description = self.tools.get_tools_description()

            # 渲染提示词
            system, user = self.prompt_manager.render_agent_planner(
                state=self.state.to_dict(), tools_description=tools_description
            )

            # 调用 LLM
            response = self.llm.chat_with_system(
                system_prompt=system,
                user_prompt=user,
                temperature=0.5,
                response_format={"type": "json_object"},
            )

            # 更新 LLM 调用计数
            self.state.llm_calls += 1

            # 解析决策
            cleaned_response = extract_json_from_response(response)
            decision = json.loads(cleaned_response)
            logger.info(f"决策: {decision.get('action')} - {decision.get('reasoning')}")

            return decision

        except Exception as e:
            logger.error(f"决策失败: {e}")
            return None

    def _execute_tool(self, decision: Dict[str, Any]) -> Any:
        """
        执行工具

        Args:
            decision: 决策字典

        Returns:
            工具执行结果
        """
        action = decision.get("action")
        params = decision.get("params", {})

        # 确保 action 为有效的字符串，避免类型错误
        if not isinstance(action, str) or not action:
            safe_action = "unknown" if action is None or action == "" else str(action)
            logger.error(f"决策缺少有效的 action: {action}")
            self.state.add_action(
                action=safe_action,
                params=params,
                success=False,
                result="invalid action",
            )
            return None

        try:
            result = self.tools.call(action, **params)
            logger.info(f"工具执行成功: {action}")

            # 如果是refine_tests，更新当前方法的覆盖率
            if (
                action == "refine_tests"
                and isinstance(result, dict)
                and "method_coverage" in result
            ):
                self.state.current_method_coverage = result["method_coverage"]
                logger.debug(
                    f"更新当前方法覆盖率: {self.state.current_method_coverage:.1%}"
                )

            # 记录操作历史
            self.state.add_action(
                action=action,
                params=params,
                success=True,
                result=self._simplify_result(result),
            )

            # 执行自动化工作流
            self._auto_execute_workflow(action, result)

            return result
        except Exception as e:
            logger.error(f"工具执行失败: {action} - {e}")

            # 记录失败的操作
            self.state.add_action(
                action=action, params=params, success=False, result=str(e)
            )

            return None

    def _auto_execute_workflow(self, action: str, result: Any) -> None:
        """
        执行自动化工作流

        在特定工具执行后，自动执行后续操作：
        - select_target: 如果新目标没有测试/变异体，自动生成并评估
        - refine_tests/refine_mutants: 自动执行评估

        Args:
            action: 已执行的工具名称
            result: 工具执行结果
        """
        if not result:
            return

        # 自动化流程1: select_target 后的自动生成和评估
        if action == "select_target":
            self._auto_workflow_for_new_target(result)

        # 自动化流程2: refine_tests 或 refine_mutants 后自动评估
        elif action in ["refine_tests", "refine_mutants"]:
            self._auto_workflow_for_refine(action)

    def _auto_workflow_for_new_target(self, target_result: Dict[str, Any]) -> None:
        """
        新目标的自动化工作流

        流程：
        1. 如果目标没有测试，自动生成测试
        2. 如果目标没有变异体，自动生成变异体
        3. 无论是否生成，只要有测试和变异体，就自动评估以获取最新指标

        顺序：generate_tests（如需要）→ generate_mutants（如需要）→ run_evaluation

        Args:
            target_result: select_target 的返回结果
        """
        if not isinstance(target_result, dict):
            return

        class_name = target_result.get("class_name")
        method_name = target_result.get("method_name")

        if not class_name or not method_name:
            logger.debug("目标信息不完整，跳过自动化流程")
            return

        logger.info(f"{'='*60}")
        logger.info(f"开始新目标自动化流程: {class_name}.{method_name}")
        logger.info(f"{'='*60}")

        # 标记是否需要评估
        need_evaluation = False

        # 检查是否需要生成测试（检查目标方法是否有测试，而不是整个类）
        existing_tests = (
            self.tools.db.get_tests_by_target_method(class_name, method_name)
            if self.tools.db
            else []
        )
        if not existing_tests:
            logger.info("→ 自动执行: generate_tests（目标方法没有测试）")
            try:
                test_result = self.tools.call(
                    "generate_tests", class_name=class_name, method_name=method_name
                )
                if test_result and test_result.get("generated", 0) > 0:
                    logger.info(f"  ✓ 成功生成 {test_result.get('generated')} 个测试")
                    need_evaluation = True
                    # 记录自动执行的操作
                    self.state.add_action(
                        action="generate_tests",
                        params={"class_name": class_name, "method_name": method_name},
                        success=True,
                        result=self._simplify_result(test_result),
                    )
                else:
                    logger.warning("  ✗ 测试生成失败或未生成任何测试，停止自动流程")
                    return
            except Exception as e:
                logger.error(f"  ✗ 自动生成测试失败: {e}")
                return
        else:
            logger.info("→ 跳过 generate_tests（目标方法已有测试）")

        # 检查是否需要生成变异体
        existing_mutants = (
            self.tools.db.get_mutants_by_method(class_name, method_name, status="valid")
            if self.tools.db
            else []
        )
        if not existing_mutants:
            logger.info("→ 自动执行: generate_mutants（目标没有变异体）")
            try:
                mutant_result = self.tools.call(
                    "generate_mutants", class_name=class_name, method_name=method_name
                )
                if mutant_result and mutant_result.get("generated", 0) > 0:
                    logger.info(
                        f"  ✓ 成功生成 {mutant_result.get('generated')} 个变异体"
                    )
                    need_evaluation = True
                    # 记录自动执行的操作
                    self.state.add_action(
                        action="generate_mutants",
                        params={"class_name": class_name, "method_name": method_name},
                        success=True,
                        result=self._simplify_result(mutant_result),
                    )
                else:
                    logger.warning("  ✗ 变异体生成失败或未生成任何变异体，停止自动流程")
                    return
            except Exception as e:
                logger.error(f"  ✗ 自动生成变异体失败: {e}")
                return
        else:
            logger.info("→ 跳过 generate_mutants（已有变异体）")

        # 检查是否需要评估：如果既有测试又有变异体（即使没有生成新的），也应该评估以获取最新指标
        if not need_evaluation and existing_tests and existing_mutants:
            logger.info("→ 目标已有测试和变异体，执行评估以获取最新指标")
            need_evaluation = True

        # 如果需要评估（生成了新内容或已有测试和变异体），自动评估
        if need_evaluation:
            logger.info("→ 自动执行: run_evaluation（完成生成操作后）")
            try:
                eval_result = self.tools.call("run_evaluation")
                if eval_result:
                    logger.info(
                        f"  ✓ 评估完成: 评估了 {eval_result.get('evaluated', 0)} 个变异体"
                    )
                    # 记录自动执行的操作
                    self.state.add_action(
                        action="run_evaluation",
                        params={},
                        success=True,
                        result=self._simplify_result(eval_result),
                    )
                else:
                    logger.warning("  ✗ 评估失败")
            except Exception as e:
                logger.error(f"  ✗ 自动评估失败: {e}")

        logger.info(f"{'='*60}")
        logger.info("新目标自动化流程完成")
        logger.info(f"{'='*60}")

    def _auto_workflow_for_refine(self, refine_action: str) -> None:
        """
        完善操作后的自动化工作流

        流程：refine_tests/refine_mutants → run_evaluation

        Args:
            refine_action: 完善操作名称（refine_tests 或 refine_mutants）
        """
        logger.info(f"{'='*60}")
        logger.info(f"{refine_action} 完成，自动执行评估")
        logger.info(f"{'='*60}")

        logger.info("→ 自动执行: run_evaluation")
        try:
            eval_result = self.tools.call("run_evaluation")
            if eval_result:
                logger.info(
                    f"  ✓ 评估完成: 评估了 {eval_result.get('evaluated', 0)} 个变异体"
                )
                # 记录自动执行的操作
                self.state.add_action(
                    action="run_evaluation",
                    params={},
                    success=True,
                    result=self._simplify_result(eval_result),
                )
            else:
                logger.warning("  ✗ 评估失败")
        except Exception as e:
            logger.error(f"  ✗ 自动评估失败: {e}")

        logger.info(f"{'='*60}")

    def _simplify_result(self, result: Any) -> Any:
        """简化结果用于记录（避免过大的对象）"""
        if result is None:
            return None
        if isinstance(result, dict):
            # 只保留关键字段
            return {
                k: v
                for k, v in result.items()
                if k
                in [
                    "generated",
                    "evaluated",
                    "killed",
                    "mutation_score",
                    "class_name",
                    "method_name",
                ]
            }
        return str(result)[:100]  # 截断过长的字符串

    def save_state(self, file_path: str) -> None:
        """保存状态"""
        self.state.save(file_path)

    def load_state(self, file_path: str) -> bool:
        """加载状态"""
        state = AgentState.load(file_path)
        if state:
            self.state = state
            return True
        return False
