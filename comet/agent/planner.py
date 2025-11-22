"""Agent 调度器"""

import json
import logging
from typing import Optional, Dict, Any

from ..llm.client import LLMClient
from ..llm.prompts import PromptManager
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

    def run(self, stop_on_no_improvement_rounds: int = 3, min_improvement_threshold: float = 0.01) -> AgentState:
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

            # 更新迭代计数
            self.state.iteration += 1

            # 只有在执行评估后才检查改进
            # 因为只有 run_evaluation 会更新变异分数和覆盖率等指标
            if action == "run_evaluation":
                # 检查评估是否成功执行
                evaluation_succeeded = (
                    result is not None and
                    isinstance(result, dict) and
                    result.get("evaluated", 0) > 0
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
                        min_improvement_threshold
                    )

                    if has_improvement:
                        logger.info(f"检测到改进，重置无改进计数器")
                        no_improvement_count = 0
                        # 记录改进（使用全局指标）
                        self.state.add_improvement({
                            "iteration": self.state.iteration,
                            "mutation_score": self.state.global_mutation_score,
                            "line_coverage": self.state.line_coverage,
                            "mutation_score_delta": self.state.global_mutation_score - prev_mutation_score,
                            "coverage_delta": self.state.line_coverage - prev_line_coverage,
                        })
                    else:
                        no_improvement_count += 1
                        logger.info(f"评估后无显著改进 (连续 {no_improvement_count}/{stop_on_no_improvement_rounds} 轮)")

                    # 更新上一轮指标（只在评估后更新，使用全局指标）
                    prev_mutation_score = self.state.global_mutation_score
                    prev_line_coverage = self.state.line_coverage
            else:
                # 非评估操作，同步状态但不检查改进
                self._sync_state_from_db()

            # 检查停止条件：连续多轮无改进
            if no_improvement_count >= stop_on_no_improvement_rounds:
                logger.info(f"连续 {no_improvement_count} 轮无改进，停止")
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
        threshold: float = 0.01
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

        has_improvement = mutation_score_delta >= threshold or coverage_delta >= threshold

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
            self.state.global_mutation_score >= self.excellent_mutation_score and
            self.state.line_coverage >= self.excellent_line_coverage and
            self.state.branch_coverage >= self.excellent_branch_coverage
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
        logger.info(f"  已击杀: {self.state.global_killed_mutants}, 幸存: {self.state.global_survived_mutants}")
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
            self.state.global_survived_mutants = len([m for m in all_evaluated_mutants if m.survived])
            self.state.global_killed_mutants = self.state.global_total_mutants - self.state.global_survived_mutants

            # 计算全局变异分数
            if self.state.global_total_mutants > 0:
                self.state.global_mutation_score = self.state.global_killed_mutants / self.state.global_total_mutants
            else:
                self.state.global_mutation_score = 0.0

            # ===== 当前目标统计（仅当前目标方法的 valid 变异体）=====
            current_target = self.state.current_target
            if current_target and current_target.get("class_name") and current_target.get("method_name"):
                # 获取当前目标方法的 valid 变异体
                current_mutants = self.tools.db.get_mutants_by_method(
                    class_name=current_target["class_name"],
                    method_name=current_target["method_name"],
                    status="valid"
                )
                self.state.total_mutants = len(current_mutants)
                self.state.survived_mutants = len([m for m in current_mutants if m.survived])
                self.state.killed_mutants = self.state.total_mutants - self.state.survived_mutants

                # 计算当前目标变异分数
                if self.state.total_mutants > 0:
                    self.state.mutation_score = self.state.killed_mutants / self.state.total_mutants
                else:
                    self.state.mutation_score = 0.0
            else:
                # 没有当前目标时，使用所有 valid 变异体
                valid_mutants = self.tools.db.get_valid_mutants()
                self.state.total_mutants = len(valid_mutants)
                self.state.survived_mutants = len([m for m in valid_mutants if m.survived])
                self.state.killed_mutants = self.state.total_mutants - self.state.survived_mutants

                # 计算变异分数
                if self.state.total_mutants > 0:
                    self.state.mutation_score = self.state.killed_mutants / self.state.total_mutants
                else:
                    self.state.mutation_score = 0.0

            # 获取覆盖率统计
            try:
                all_coverages = self.tools.db.get_all_method_coverage()
                if all_coverages:
                    # 计算所有方法的加权平均覆盖率
                    total_lines = sum(c.total_lines for c in all_coverages)
                    covered_lines = sum(len(c.covered_lines) for c in all_coverages)
                    total_branches = sum(c.total_branches for c in all_coverages)
                    covered_branches = sum(c.covered_branches for c in all_coverages)

                    self.state.line_coverage = covered_lines / total_lines if total_lines > 0 else 0.0
                    self.state.branch_coverage = covered_branches / total_branches if total_branches > 0 else 0.0
                else:
                    self.state.line_coverage = 0.0
                    self.state.branch_coverage = 0.0

                # 同步当前目标方法的覆盖率
                if self.state.current_target and self.state.current_target.get("class_name") and self.state.current_target.get("method_name"):
                    current_coverage = self.tools.db.get_method_coverage(
                        self.state.current_target["class_name"],
                        self.state.current_target["method_name"]
                    )
                    if current_coverage:
                        self.state.current_method_coverage = current_coverage.line_coverage_rate
                        logger.debug(f"已更新当前方法覆盖率: {self.state.current_method_coverage:.1%}")
            except Exception as e:
                logger.warning(f"同步覆盖率数据失败: {e}")

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
            # 更新测试用例信息（让 Agent 知道当前有哪些测试）
            if self.tools.db:
                try:
                    all_test_cases = self.tools.db.get_all_test_cases()
                    self.state.set_test_cases(all_test_cases)
                except Exception as e:
                    logger.warning(f"获取测试用例列表失败: {e}")

            # 动态获取工具描述
            tools_description = self.tools.get_tools_description()

            # 渲染提示词
            system, user = self.prompt_manager.render_agent_planner(
                state=self.state.to_dict(),
                tools_description=tools_description
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
            decision = json.loads(response)
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

        try:
            result = self.tools.call(action, **params)
            logger.info(f"工具执行成功: {action}")

            # 如果是refine_tests，更新当前方法的覆盖率
            if action == "refine_tests" and isinstance(result, dict) and "method_coverage" in result:
                self.state.current_method_coverage = result["method_coverage"]
                logger.debug(f"更新当前方法覆盖率: {self.state.current_method_coverage:.1%}")

            # 记录操作历史
            self.state.add_action(
                action=action,
                params=params,
                success=True,
                result=self._simplify_result(result)
            )

            return result
        except Exception as e:
            logger.error(f"工具执行失败: {action} - {e}")

            # 记录失败的操作
            self.state.add_action(
                action=action,
                params=params,
                success=False,
                result=str(e)
            )

            return None

    def _simplify_result(self, result: Any) -> Any:
        """简化结果用于记录（避免过大的对象）"""
        if result is None:
            return None
        if isinstance(result, dict):
            # 只保留关键字段
            return {k: v for k, v in result.items() if k in ['generated', 'evaluated', 'killed', 'mutation_score', 'class_name', 'method_name']}
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
