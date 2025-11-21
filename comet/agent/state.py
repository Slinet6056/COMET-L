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
        self.total_mutants = 0
        self.killed_mutants = 0
        self.survived_mutants = 0
        self.total_tests = 0
        self.mutation_score = 0.0
        self.line_coverage = 0.0
        self.branch_coverage = 0.0
        self.current_method_coverage: Optional[float] = None  # 当前方法的覆盖率
        self.llm_calls = 0
        self.budget = 1000

        # 当前目标和上一个目标
        self.current_target: Optional[Dict[str, Any]] = None
        self.previous_target: Optional[Dict[str, Any]] = None  # 追踪目标切换

        # 测试用例信息（新增）
        self.test_cases: List[Dict[str, Any]] = []  # 测试用例列表

        # 历史记录
        self.action_history: List[Dict[str, Any]] = []  # 操作历史
        self.recent_improvements: List[Dict[str, Any]] = []
        self.processed_targets: List[str] = []
        self.available_targets: List[Dict[str, Any]] = []

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

    def add_action(self, action: str, params: Dict[str, Any], success: bool, result: Any = None) -> None:
        """
        添加操作记录

        Args:
            action: 操作名称
            params: 操作参数
            success: 是否成功
            result: 操作结果
        """
        self.action_history.append({
            "iteration": self.iteration,
            "action": action,
            "params": params,
            "success": success,
            "result": result,
        })
        # 只保留最近 10 次操作
        self.action_history = self.action_history[-10:]

    def set_test_cases(self, test_cases: List[Any]) -> None:
        """
        设置测试用例列表（供 Agent 查看）

        Args:
            test_cases: TestCase 对象列表
        """
        self.test_cases = []
        for tc in test_cases:
            self.test_cases.append({
                "class_name": tc.class_name,
                "target_class": tc.target_class,
                "version": tc.version,
                "num_methods": len(tc.methods),
                "method_names": [m.method_name for m in tc.methods],
                "compile_success": tc.compile_success,
                "kills_count": len(tc.kills),
            })
        self.total_tests = sum(tc["num_methods"] for tc in self.test_cases)

    def update_target(self, new_target: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
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

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "iteration": self.iteration,
            "total_mutants": self.total_mutants,
            "killed_mutants": self.killed_mutants,
            "survived_mutants": self.survived_mutants,
            "total_tests": self.total_tests,
            "mutation_score": self.mutation_score,
            "line_coverage": self.line_coverage,
            "branch_coverage": self.branch_coverage,
            "current_method_coverage": self.current_method_coverage,
            "llm_calls": self.llm_calls,
            "budget": self.budget,
            "current_target": self.current_target,
            "previous_target": self.previous_target,
            "test_cases": self.test_cases,
            "action_history": self.action_history,
            "recent_improvements": self.recent_improvements,
            "processed_targets": self.processed_targets,
            "available_targets": self.available_targets,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "last_update": self.last_update.isoformat() if self.last_update else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentState":
        """从字典创建"""
        state = cls()
        state.iteration = data.get("iteration", 0)
        state.total_mutants = data.get("total_mutants", 0)
        state.killed_mutants = data.get("killed_mutants", 0)
        state.survived_mutants = data.get("survived_mutants", 0)
        state.total_tests = data.get("total_tests", 0)
        state.mutation_score = data.get("mutation_score", 0.0)
        state.line_coverage = data.get("line_coverage", 0.0)
        state.branch_coverage = data.get("branch_coverage", 0.0)
        state.llm_calls = data.get("llm_calls", 0)
        state.budget = data.get("budget", 1000)
        state.current_target = data.get("current_target")
        state.previous_target = data.get("previous_target")
        state.test_cases = data.get("test_cases", [])
        state.action_history = data.get("action_history", [])
        state.recent_improvements = data.get("recent_improvements", [])
        state.processed_targets = data.get("processed_targets", [])
        state.available_targets = data.get("available_targets", [])

        if data.get("start_time"):
            state.start_time = datetime.fromisoformat(data["start_time"])
        if data.get("last_update"):
            state.last_update = datetime.fromisoformat(data["last_update"])

        return state

    def save(self, file_path: str) -> None:
        """保存状态到文件"""
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"状态已保存: {file_path}")

    @classmethod
    def load(cls, file_path: str) -> Optional["AgentState"]:
        """从文件加载状态"""
        if not Path(file_path).exists():
            return None

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        logger.info(f"状态已加载: {file_path}")
        return cls.from_dict(data)
