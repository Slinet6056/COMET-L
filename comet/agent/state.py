"""Agent 状态管理"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class AgentState:
    """Agent 状态 - 记录当前迭代的状态"""

    def __init__(self):
        """初始化状态"""
        self.iteration = 0

        # 全局统计（所有目标的累积，包括 outdated 的变异体）
        self.global_total_mutants = 0
        self.global_killed_mutants = 0
        self.global_survived_mutants = 0
        self.global_mutation_score = 0.0

        # 当前目标统计（只统计当前目标方法的 valid 变异体）
        self.total_mutants = 0
        self.killed_mutants = 0
        self.survived_mutants = 0
        self.mutation_score = 0.0

        # 通用统计
        self.total_tests = 0
        self.line_coverage = 0.0
        self.branch_coverage = 0.0
        self.current_method_coverage: Optional[float] = None  # 当前方法的覆盖率
        self.llm_calls = 0
        self.budget = 1000

        # 当前目标和上一个目标
        self.current_target: Optional[Dict[str, Any]] = None
        self.previous_target: Optional[Dict[str, Any]] = None  # 追踪目标切换

        # 历史记录
        self.action_history: List[Dict[str, Any]] = []  # 操作历史
        self.recent_improvements: List[Dict[str, Any]] = []
        self.processed_targets: List[str] = []
        self.available_targets: List[Dict[str, Any]] = []
        self.failed_targets: List[Dict[str, Any]] = (
            []
        )  # 失败的目标（黑名单），包含类名、方法名和失败原因

        # 时间戳
        self.start_time: Optional[datetime] = None
        self.last_update: Optional[datetime] = None

    def update(self, metrics: Dict[str, Any]) -> None:
        """
        更新状态

        Args:
            metrics: 度量数据
        """
        self.iteration = metrics.get("iteration", self.iteration)
        self.total_mutants = metrics.get("total_mutants", self.total_mutants)
        self.killed_mutants = metrics.get("killed_mutants", self.killed_mutants)
        self.survived_mutants = metrics.get("survived_mutants", self.survived_mutants)
        self.total_tests = metrics.get("total_tests", self.total_tests)
        self.mutation_score = metrics.get("mutation_score", self.mutation_score)
        self.line_coverage = metrics.get("line_coverage", self.line_coverage)
        self.branch_coverage = metrics.get("branch_coverage", self.branch_coverage)
        self.llm_calls = metrics.get("llm_calls", self.llm_calls)

        self.last_update = datetime.now()

    def add_improvement(self, improvement: Dict[str, Any]) -> None:
        """添加改进记录"""
        self.recent_improvements.append(improvement)
        # 只保留最近 5 次
        self.recent_improvements = self.recent_improvements[-5:]

    def mark_target_processed(self, target: str) -> None:
        """标记目标已处理"""
        if target not in self.processed_targets:
            self.processed_targets.append(target)

    def set_available_targets(self, targets: List[Dict[str, Any]]) -> None:
        """设置可用目标"""
        self.available_targets = targets

    def add_action(
        self, action: str, params: Dict[str, Any], success: bool, result: Any = None
    ) -> None:
        """
        添加操作记录

        Args:
            action: 操作名称
            params: 操作参数
            success: 是否成功
            result: 操作结果
        """
        self.action_history.append(
            {
                "iteration": self.iteration,
                "action": action,
                "params": params,
                "success": success,
                "result": result,
            }
        )
        # 只保留最近 10 次操作
        self.action_history = self.action_history[-10:]

    def update_target(
        self, new_target: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        更新当前目标，并记录上一个目标

        Args:
            new_target: 新目标

        Returns:
            上一个目标（如果有切换）
        """
        if new_target != self.current_target:
            self.previous_target = self.current_target
            self.current_target = new_target

            if self.previous_target:
                logger.info(
                    f"目标已切换: "
                    f"{self.previous_target.get('class_name')}.{self.previous_target.get('method_name')} "
                    f"-> {new_target.get('class_name') if new_target else 'None'}."
                    f"{new_target.get('method_name') if new_target else 'None'}"
                )
            return self.previous_target
        return None

    def add_failed_target(self, class_name: str, method_name: str, reason: str) -> None:
        """
        将目标添加到失败黑名单

        Args:
            class_name: 类名
            method_name: 方法名
            reason: 失败原因
        """
        target_key = f"{class_name}.{method_name}" if method_name else class_name

        # 检查是否已经在黑名单中
        if any(ft.get("target") == target_key for ft in self.failed_targets):
            logger.debug(f"目标 {target_key} 已在黑名单中")
            return

        self.failed_targets.append(
            {
                "target": target_key,
                "class_name": class_name,
                "method_name": method_name,
                "reason": reason,
                "iteration": self.iteration,
            }
        )
        logger.warning(f"已将 {target_key} 添加到黑名单，原因: {reason}")
        logger.info(f"当前黑名单大小: {len(self.failed_targets)}")

        # 如果当前目标是被加入黑名单的目标，清除当前目标选中
        if self.current_target:
            current_class = self.current_target.get("class_name")
            current_method = self.current_target.get("method_name", "")
            current_target_key = (
                f"{current_class}.{current_method}"
                if current_method and current_class
                else (current_class if current_class else None)
            )
            if current_target_key == target_key:
                logger.info(f"当前目标 {target_key} 已被加入黑名单，清除目标选中")
                self.update_target(None)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "iteration": self.iteration,
            # 全局统计
            "global_total_mutants": self.global_total_mutants,
            "global_killed_mutants": self.global_killed_mutants,
            "global_survived_mutants": self.global_survived_mutants,
            "global_mutation_score": self.global_mutation_score,
            # 当前目标统计
            "total_mutants": self.total_mutants,
            "killed_mutants": self.killed_mutants,
            "survived_mutants": self.survived_mutants,
            "mutation_score": self.mutation_score,
            # 通用统计
            "total_tests": self.total_tests,
            "line_coverage": self.line_coverage,
            "branch_coverage": self.branch_coverage,
            "current_method_coverage": self.current_method_coverage,
            "llm_calls": self.llm_calls,
            "budget": self.budget,
            "current_target": self.current_target,
            "previous_target": self.previous_target,
            "action_history": self.action_history,
            "recent_improvements": self.recent_improvements,
            "processed_targets": self.processed_targets,
            "available_targets": self.available_targets,
            "failed_targets": self.failed_targets,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "last_update": self.last_update.isoformat() if self.last_update else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentState":
        """从字典创建"""
        state = cls()
        state.iteration = data.get("iteration", 0)
        # 全局统计
        state.global_total_mutants = data.get("global_total_mutants", 0)
        state.global_killed_mutants = data.get("global_killed_mutants", 0)
        state.global_survived_mutants = data.get("global_survived_mutants", 0)
        state.global_mutation_score = data.get("global_mutation_score", 0.0)
        # 当前目标统计
        state.total_mutants = data.get("total_mutants", 0)
        state.killed_mutants = data.get("killed_mutants", 0)
        state.survived_mutants = data.get("survived_mutants", 0)
        state.mutation_score = data.get("mutation_score", 0.0)
        # 通用统计
        state.total_tests = data.get("total_tests", 0)
        state.line_coverage = data.get("line_coverage", 0.0)
        state.branch_coverage = data.get("branch_coverage", 0.0)
        state.current_method_coverage = data.get("current_method_coverage")
        state.llm_calls = data.get("llm_calls", 0)
        state.budget = data.get("budget", 1000)
        state.current_target = data.get("current_target")
        state.previous_target = data.get("previous_target")
        state.action_history = data.get("action_history", [])
        state.recent_improvements = data.get("recent_improvements", [])
        state.processed_targets = data.get("processed_targets", [])
        state.available_targets = data.get("available_targets", [])
        state.failed_targets = data.get("failed_targets", [])

        if data.get("start_time"):
            state.start_time = datetime.fromisoformat(data["start_time"])
        if data.get("last_update"):
            state.last_update = datetime.fromisoformat(data["last_update"])

        return state

    def save(self, file_path: str) -> None:
        """保存状态到文件"""
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"状态已保存: {file_path}")

    @classmethod
    def load(cls, file_path: str) -> Optional["AgentState"]:
        """从文件加载状态"""
        if not Path(file_path).exists():
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        logger.info(f"状态已加载: {file_path}")
        return cls.from_dict(data)
