"""Agent 状态管理"""

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.method_keys import build_method_key

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
        self.decision_reasoning: Optional[str] = None

        # 历史记录
        self.action_history: List[Dict[str, Any]] = []  # 操作历史
        self.recent_improvements: List[Dict[str, Any]] = []
        self.improvement_summary: Dict[str, Any] = {"count": 0, "latest": None}
        self.processed_targets: List[str] = []
        self.available_targets: List[Dict[str, Any]] = []
        self.failed_targets: List[
            Dict[str, Any]
        ] = []  # 失败的目标（黑名单），包含类名、方法名和失败原因

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
        self.improvement_summary = self._build_improvement_summary()

    def set_decision_reasoning(self, reasoning: Optional[str]) -> None:
        self.decision_reasoning = reasoning

    def _build_improvement_summary(self) -> Dict[str, Any]:
        if not self.recent_improvements:
            return {"count": 0, "latest": None}

        return {
            "count": len(self.recent_improvements),
            "latest": dict(self.recent_improvements[-1]),
        }

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

    def add_failed_target(self, class_name: str, method_name: str, reason: str) -> None:
        """
        将目标添加到失败黑名单

        Args:
            class_name: 类名
            method_name: 方法名
            reason: 失败原因
        """
        method_signature = None
        if self.current_target and self.current_target.get("class_name") == class_name:
            if self.current_target.get("method_name") == method_name:
                method_signature = self.current_target.get("method_signature")
        target_key = build_method_key(class_name, method_name, method_signature)

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
                build_method_key(
                    current_class,
                    current_method,
                    self.current_target.get("method_signature"),
                )
                if current_class
                else None
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
            "decision_reasoning": self.decision_reasoning,
            "action_history": self.action_history,
            "recent_improvements": self.recent_improvements,
            "improvement_summary": self.improvement_summary,
            "processed_targets": self.processed_targets,
            "available_targets": self.available_targets,
            "failed_targets": self.failed_targets,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "currentTarget": self.current_target,
            "previousTarget": self.previous_target,
            "decisionReasoning": self.decision_reasoning,
            "recentImprovements": self.recent_improvements,
            "improvementSummary": self.improvement_summary,
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
        state.current_target = data.get("current_target", data.get("currentTarget"))
        state.previous_target = data.get("previous_target", data.get("previousTarget"))
        state.decision_reasoning = data.get("decision_reasoning", data.get("decisionReasoning"))
        state.action_history = data.get("action_history", [])
        state.recent_improvements = data.get(
            "recent_improvements", data.get("recentImprovements", [])
        )
        state.improvement_summary = (
            data.get("improvement_summary", data.get("improvementSummary"))
            or state._build_improvement_summary()
        )
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


@dataclass
class WorkerResult:
    """Worker 处理结果"""

    target_id: str  # 目标标识: "{class_name}.{method_name}"
    class_name: str
    method_name: str
    method_signature: Optional[str] = None
    success: bool = False
    error: Optional[str] = None

    # 生成结果
    tests_generated: int = 0
    mutants_generated: int = 0

    # 评估结果
    mutants_evaluated: int = 0
    mutants_killed: int = 0
    local_mutation_score: float = 0.0

    # 测试文件（路径 -> 内容）
    test_files: Dict[str, str] = field(default_factory=dict)

    # 处理时间
    processing_time: float = 0.0

    method_coverage: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "class_name": self.class_name,
            "method_name": self.method_name,
            "method_signature": self.method_signature,
            "success": self.success,
            "error": self.error,
            "tests_generated": self.tests_generated,
            "mutants_generated": self.mutants_generated,
            "mutants_evaluated": self.mutants_evaluated,
            "mutants_killed": self.mutants_killed,
            "local_mutation_score": self.local_mutation_score,
            "test_files": self.test_files,
            "processing_time": self.processing_time,
            "method_coverage": self.method_coverage,
            "targetId": self.target_id,
            "className": self.class_name,
            "methodName": self.method_name,
            "methodSignature": self.method_signature,
            "testsGenerated": self.tests_generated,
            "mutantsGenerated": self.mutants_generated,
            "mutantsEvaluated": self.mutants_evaluated,
            "mutantsKilled": self.mutants_killed,
            "localMutationScore": self.local_mutation_score,
            "processingTime": self.processing_time,
            "methodCoverage": self.method_coverage,
        }

    def to_worker_card(self) -> Dict[str, Any]:
        return {
            "targetId": self.target_id,
            "className": self.class_name,
            "methodName": self.method_name,
            "methodSignature": self.method_signature,
            "success": self.success,
            "error": self.error,
            "testsGenerated": self.tests_generated,
            "mutantsGenerated": self.mutants_generated,
            "mutantsEvaluated": self.mutants_evaluated,
            "mutantsKilled": self.mutants_killed,
            "localMutationScore": self.local_mutation_score,
            "processingTime": self.processing_time,
            "methodCoverage": self.method_coverage,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkerResult":
        return cls(
            target_id=data.get("target_id", data.get("targetId", "")),
            class_name=data.get("class_name", data.get("className", "")),
            method_name=data.get("method_name", data.get("methodName", "")),
            method_signature=data.get("method_signature", data.get("methodSignature")),
            success=data.get("success", False),
            error=data.get("error"),
            tests_generated=data.get("tests_generated", data.get("testsGenerated", 0)),
            mutants_generated=data.get("mutants_generated", data.get("mutantsGenerated", 0)),
            mutants_evaluated=data.get("mutants_evaluated", data.get("mutantsEvaluated", 0)),
            mutants_killed=data.get("mutants_killed", data.get("mutantsKilled", 0)),
            local_mutation_score=data.get(
                "local_mutation_score", data.get("localMutationScore", 0.0)
            ),
            test_files=data.get("test_files", {}),
            processing_time=data.get("processing_time", data.get("processingTime", 0.0)),
            method_coverage=data.get("method_coverage", data.get("methodCoverage")),
        )


class ParallelAgentState(AgentState):
    """并行 Agent 状态 - 支持多目标追踪和线程安全"""

    def __init__(self):
        """初始化并行状态"""
        super().__init__()

        # 线程安全锁
        self._lock = threading.RLock()  # 可重入锁，支持嵌套调用

        # 多目标追踪
        self._active_targets: Dict[str, Dict[str, Any]] = {}  # {target_id: target_info}
        self._active_targets_lock = threading.Lock()
        self._target_lifecycle: Dict[str, Dict[str, Any]] = {}
        self._target_order_counter: int = 1

        # 批次管理
        self.current_batch: int = 0
        self.batch_results: List[List[WorkerResult]] = []  # 每批次的结果

        # 并行统计
        self.parallel_stats: Dict[str, Any] = {
            "total_batches": 0,
            "total_workers_spawned": 0,
            "total_targets_processed": 0,
            "failed_targets_in_parallel": 0,
            "merge_conflicts": 0,
        }

    def acquire_target(
        self,
        class_name: str,
        method_name: str,
        method_signature: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        原子地获取目标（避免多个 Worker 选择同一目标）

        Args:
            class_name: 类名
            method_name: 方法名

        Returns:
            是否成功获取（False 表示目标已被其他 Worker 占用）
        """
        target_id = build_method_key(class_name, method_name, method_signature)
        with self._active_targets_lock:
            if target_id in self._active_targets:
                return False
            if target_id in self.processed_targets:
                return False
            # 检查黑名单
            if any(ft.get("target") == target_id for ft in self.failed_targets):
                return False

            target_metadata = dict(metadata or {})
            order = self._target_order_counter
            self._target_order_counter += 1
            target_metadata.update(
                {
                    "target_id": target_id,
                    "class_name": class_name,
                    "method_name": method_name,
                    "method_signature": method_signature,
                    "started_at": datetime.now(),
                    "order": order,
                    "status": "running",
                }
            )
            self._active_targets[target_id] = target_metadata
            self._target_lifecycle[target_id] = dict(target_metadata)
            return True

    def release_target(
        self,
        class_name: str,
        method_name: str,
        method_signature: Optional[str],
        success: bool,
        result: Optional[WorkerResult] = None,
    ) -> None:
        """
        释放目标（Worker 完成处理后调用）

        Args:
            class_name: 类名
            method_name: 方法名
            success: 是否处理成功
        """
        target_id = build_method_key(class_name, method_name, method_signature)
        with self._active_targets_lock:
            ended_at = datetime.now()
            lifecycle: Dict[str, Any] | None = self._target_lifecycle.get(target_id)
            if lifecycle is None:
                lifecycle = {
                    "target_id": target_id,
                    "class_name": class_name,
                    "method_name": method_name,
                    "method_signature": method_signature,
                    "order": self._target_order_counter,
                    "started_at": ended_at,
                }
                self._target_order_counter += 1
                self._target_lifecycle[target_id] = lifecycle

            lifecycle["status"] = "completed" if success else "failed"
            lifecycle["ended_at"] = ended_at
            lifecycle["completed_at"] = ended_at if success else None
            if result is not None:
                lifecycle["duration_seconds"] = result.processing_time
                if result.error:
                    lifecycle["error"] = result.error

            if target_id in self._active_targets:
                del self._active_targets[target_id]
            if success:
                self.mark_target_processed(target_id)

    def get_active_targets(self) -> List[str]:
        """获取当前活跃的目标列表"""
        with self._active_targets_lock:
            return list(self._active_targets.keys())

    def get_active_target_details(self) -> List[Dict[str, Any]]:
        with self._active_targets_lock:
            return [
                self._serialize_active_target(target) for target in self._active_targets.values()
            ]

    def get_task_lifecycle_details(self) -> List[Dict[str, Any]]:
        with self._active_targets_lock:
            ordered_targets = sorted(
                self._target_lifecycle.values(),
                key=lambda target: (
                    int(target.get("order", 0)),
                    str(target.get("target_id", target.get("targetId", ""))),
                ),
            )
            return [self._serialize_active_target(target) for target in ordered_targets]

    def get_active_target_count(self) -> int:
        """获取当前活跃目标数量"""
        with self._active_targets_lock:
            return len(self._active_targets)

    def update_threadsafe(self, metrics: Dict[str, Any]) -> None:
        """
        线程安全地更新状态

        Args:
            metrics: 度量数据
        """
        with self._lock:
            self.update(metrics)

    def add_action_threadsafe(
        self, action: str, params: Dict[str, Any], success: bool, result: Any = None
    ) -> None:
        """线程安全地添加操作记录"""
        with self._lock:
            self.add_action(action, params, success, result)

    def increment_llm_calls(self, count: int = 1) -> int:
        """
        线程安全地增加 LLM 调用计数

        Returns:
            更新后的总调用次数
        """
        with self._lock:
            self.llm_calls += count
            return self.llm_calls

    def add_batch_result(self, batch_results: List[WorkerResult]) -> None:
        """
        添加一个批次的结果

        Args:
            batch_results: 该批次所有 Worker 的结果
        """
        with self._lock:
            self.batch_results.append(batch_results)
            self.current_batch += 1
            self.parallel_stats["total_batches"] += 1
            self.parallel_stats["total_workers_spawned"] += len(batch_results)
            self.parallel_stats["total_targets_processed"] += sum(
                1 for r in batch_results if r.success
            )
            self.parallel_stats["failed_targets_in_parallel"] += sum(
                1 for r in batch_results if not r.success
            )

    def _serialize_active_target(self, target: Dict[str, Any]) -> Dict[str, Any]:
        serialized = dict(target)
        started_at = serialized.get("started_at")
        if isinstance(started_at, datetime):
            serialized["started_at"] = started_at.isoformat()

        serialized.setdefault("targetId", serialized.get("target_id"))
        serialized.setdefault("className", serialized.get("class_name"))
        serialized.setdefault("methodName", serialized.get("method_name"))
        serialized.setdefault("startedAt", serialized.get("started_at"))

        ended_at = serialized.get("ended_at")
        if isinstance(ended_at, datetime):
            serialized["ended_at"] = ended_at.isoformat()
        serialized.setdefault("endedAt", serialized.get("ended_at"))

        completed_at = serialized.get("completed_at")
        if isinstance(completed_at, datetime):
            serialized["completed_at"] = completed_at.isoformat()
        serialized.setdefault("completedAt", serialized.get("completed_at"))

        serialized.setdefault("durationSeconds", serialized.get("duration_seconds"))
        return serialized

    def get_worker_cards(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.batch_results:
                return []
            return [result.to_worker_card() for result in self.batch_results[-1]]

    def update_global_stats_from_batch(
        self,
        total_mutants: int,
        killed_mutants: int,
        line_coverage: float,
        branch_coverage: float,
    ) -> None:
        """
        从批次结果更新全局统计（在同步阶段调用）

        Args:
            total_mutants: 全局变异体总数
            killed_mutants: 全局被击杀变异体数
            line_coverage: 全局行覆盖率
            branch_coverage: 全局分支覆盖率
        """
        with self._lock:
            self.global_total_mutants = total_mutants
            self.global_killed_mutants = killed_mutants
            self.global_survived_mutants = total_mutants - killed_mutants
            self.global_mutation_score = (
                killed_mutants / total_mutants if total_mutants > 0 else 0.0
            )
            self.line_coverage = line_coverage
            self.branch_coverage = branch_coverage
            self.last_update = datetime.now()

    def record_merge_conflict(self) -> None:
        """记录合并冲突"""
        with self._lock:
            self.parallel_stats["merge_conflicts"] += 1

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（线程安全）"""
        with self._lock:
            data = super().to_dict()
            batch_results = [[result.to_dict() for result in batch] for batch in self.batch_results]
            active_targets = self.get_active_target_details()
            target_lifecycle = self.get_task_lifecycle_details()
            data["current_batch"] = self.current_batch
            data["parallel_stats"] = self.parallel_stats
            data["batch_results"] = batch_results
            data["active_targets"] = active_targets
            data["target_lifecycle"] = target_lifecycle
            data["worker_cards"] = self.get_worker_cards()
            data["currentBatch"] = self.current_batch
            data["parallelStats"] = self.parallel_stats
            data["batchResults"] = batch_results
            data["activeTargets"] = active_targets
            data["targetLifecycle"] = target_lifecycle
            data["workerCards"] = data["worker_cards"]
            return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParallelAgentState":
        """从字典创建"""
        state = cls()
        # 调用父类的 from_dict 逻辑
        state.iteration = data.get("iteration", 0)
        state.global_total_mutants = data.get("global_total_mutants", 0)
        state.global_killed_mutants = data.get("global_killed_mutants", 0)
        state.global_survived_mutants = data.get("global_survived_mutants", 0)
        state.global_mutation_score = data.get("global_mutation_score", 0.0)
        state.total_mutants = data.get("total_mutants", 0)
        state.killed_mutants = data.get("killed_mutants", 0)
        state.survived_mutants = data.get("survived_mutants", 0)
        state.mutation_score = data.get("mutation_score", 0.0)
        state.total_tests = data.get("total_tests", 0)
        state.line_coverage = data.get("line_coverage", 0.0)
        state.branch_coverage = data.get("branch_coverage", 0.0)
        state.current_method_coverage = data.get("current_method_coverage")
        state.llm_calls = data.get("llm_calls", 0)
        state.budget = data.get("budget", 1000)
        state.current_target = data.get("current_target", data.get("currentTarget"))
        state.previous_target = data.get("previous_target", data.get("previousTarget"))
        state.decision_reasoning = data.get("decision_reasoning", data.get("decisionReasoning"))
        state.action_history = data.get("action_history", [])
        state.recent_improvements = data.get(
            "recent_improvements", data.get("recentImprovements", [])
        )
        state.improvement_summary = (
            data.get("improvement_summary", data.get("improvementSummary"))
            or state._build_improvement_summary()
        )
        state.processed_targets = data.get("processed_targets", [])
        state.available_targets = data.get("available_targets", [])
        state.failed_targets = data.get("failed_targets", [])

        if data.get("start_time"):
            state.start_time = datetime.fromisoformat(data["start_time"])
        if data.get("last_update"):
            state.last_update = datetime.fromisoformat(data["last_update"])

        # 并行特有字段
        state.current_batch = data.get("current_batch", data.get("currentBatch", 0))
        state.parallel_stats = data.get("parallel_stats", data.get("parallelStats")) or {
            "total_batches": 0,
            "total_workers_spawned": 0,
            "total_targets_processed": 0,
            "failed_targets_in_parallel": 0,
            "merge_conflicts": 0,
        }

        serialized_batches = data.get("batch_results", data.get("batchResults", []))
        state.batch_results = [
            [WorkerResult.from_dict(result) for result in batch] for batch in serialized_batches
        ]

        active_targets = data.get("active_targets", data.get("activeTargets", []))
        for target in active_targets:
            target_id = target.get("target_id", target.get("targetId"))
            if not target_id:
                continue
            restored_target = dict(target)
            started_at = restored_target.get("started_at")
            if isinstance(started_at, str):
                restored_target["started_at"] = datetime.fromisoformat(started_at)
            state._active_targets[target_id] = restored_target

        target_lifecycle = data.get("target_lifecycle", data.get("targetLifecycle", []))
        for target in target_lifecycle:
            target_id = target.get("target_id", target.get("targetId"))
            if not target_id:
                continue
            restored_target = dict(target)
            started_at = restored_target.get("started_at", restored_target.get("startedAt"))
            if isinstance(started_at, str):
                restored_target["started_at"] = datetime.fromisoformat(started_at)
            ended_at = restored_target.get("ended_at", restored_target.get("endedAt"))
            if isinstance(ended_at, str):
                restored_target["ended_at"] = datetime.fromisoformat(ended_at)
            completed_at = restored_target.get("completed_at", restored_target.get("completedAt"))
            if isinstance(completed_at, str):
                restored_target["completed_at"] = datetime.fromisoformat(completed_at)
            state._target_lifecycle[target_id] = restored_target
            state._target_order_counter = max(
                state._target_order_counter,
                int(restored_target.get("order", 0)) + 1,
            )

        return state

    @classmethod
    def load(cls, file_path: str) -> Optional["ParallelAgentState"]:
        """从文件加载状态"""
        if not Path(file_path).exists():
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        logger.info(f"并行状态已加载: {file_path}")
        return cls.from_dict(data)
