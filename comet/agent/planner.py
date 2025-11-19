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
    ):
        """
        初始化调度器

        Args:
            llm_client: LLM 客户端
            tools: 工具集
            max_iterations: 最大迭代次数
            budget: LLM 调用预算
        """
        self.llm = llm_client
        self.tools = tools
        self.prompt_manager = PromptManager()
        self.max_iterations = max_iterations
        self.budget = budget

        self.state = AgentState()
        self.state.budget = budget

    def run(self, stop_on_no_improvement_rounds: int = 3) -> AgentState:
        """
        运行调度循环

        Args:
            stop_on_no_improvement_rounds: 无改进时停止的轮数

        Returns:
            最终状态
        """
        logger.info("开始协同进化循环")
        no_improvement_count = 0

        while not self._should_stop():
            logger.info(f"{'='*60}")
            logger.info(f"迭代 {self.state.iteration + 1}/{self.max_iterations}")
            logger.info(f"{'='*60}")

            # LLM 决策
            decision = self._make_decision()

            if not decision:
                logger.warning("决策失败，跳过本轮")
                self.state.iteration += 1
                continue

            # 执行工具
            result = self._execute_tool(decision)

            # 更新状态（由具体工具更新）
            # 检查改进
            # 这里简化处理

            self.state.iteration += 1

            # 检查停止条件
            if no_improvement_count >= stop_on_no_improvement_rounds:
                logger.info(f"连续 {no_improvement_count} 轮无改进，停止")
                break

        logger.info("协同进化循环结束")
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
