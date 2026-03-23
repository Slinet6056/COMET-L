from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from comet.agent.state import AgentState, ParallelAgentState

from .log_router import RunLogRouter

MUTATION_METRIC_KEYS = (
    "mutationScore",
    "globalMutationScore",
    "totalMutants",
    "globalTotalMutants",
    "killedMutants",
    "globalKilledMutants",
    "survivedMutants",
    "globalSurvivedMutants",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_mutation_metrics(
    metrics: dict[str, Any], mutation_enabled: bool | None
) -> dict[str, Any]:
    normalized = dict(metrics)
    if mutation_enabled is False:
        for key in MUTATION_METRIC_KEYS:
            normalized[key] = None
    return normalized


def resolve_state_mutation_enabled(state: AgentState) -> bool | None:
    mutation_enabled = getattr(state, "global_mutation_enabled", None)
    if isinstance(mutation_enabled, bool):
        return mutation_enabled
    return None


class RuntimeEventBus:
    def __init__(self, max_events: int = 200) -> None:
        self.max_events = max_events
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.RLock()
        self._sequence = 0

    def __call__(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        if not event_type:
            raise ValueError("event type is required")
        payload = {key: value for key, value in event.items() if key != "type"}
        self.publish(event_type, **payload)

    def publish(self, event_type: str, **payload: object) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            event = {
                "sequence": self._sequence,
                "timestamp": _utc_now_iso(),
                "type": event_type,
                **payload,
            }
            self._events.append(event)
            return dict(event)

    def publish_snapshot(
        self,
        run_id: str,
        status: str,
        state: AgentState,
        *,
        log_router: Optional[RunLogRouter] = None,
    ) -> dict[str, Any]:
        snapshot = build_run_snapshot(run_id, status, state, log_router=log_router)
        return self.publish(
            "run.snapshot",
            runId=run_id,
            status=status,
            mode=snapshot["mode"],
            snapshot=snapshot,
        )

    def list_events(self, after_sequence: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [event for event in self._events if event["sequence"] > after_sequence]


def build_run_snapshot(
    run_id: str,
    status: str,
    state: AgentState,
    *,
    log_router: Optional[RunLogRouter] = None,
) -> dict[str, Any]:
    improvement_summary = state.improvement_summary or {"count": 0, "latest": None}
    mutation_enabled = resolve_state_mutation_enabled(state)
    if log_router is not None and isinstance(state, ParallelAgentState):
        log_router.sync_parallel_state(state)

    metrics = normalize_mutation_metrics(
        {
            "mutationScore": state.mutation_score,
            "globalMutationScore": state.global_mutation_score,
            "lineCoverage": state.line_coverage,
            "branchCoverage": state.branch_coverage,
            "totalTests": state.total_tests,
            "totalMutants": state.total_mutants,
            "globalTotalMutants": state.global_total_mutants,
            "killedMutants": state.killed_mutants,
            "globalKilledMutants": state.global_killed_mutants,
            "survivedMutants": state.survived_mutants,
            "globalSurvivedMutants": state.global_survived_mutants,
            "currentMethodCoverage": state.current_method_coverage,
        },
        mutation_enabled,
    )

    snapshot: dict[str, Any] = {
        "runId": run_id,
        "status": status,
        "mode": "parallel" if isinstance(state, ParallelAgentState) else "standard",
        "mutationEnabled": mutation_enabled,
        "iteration": state.iteration,
        "llmCalls": state.llm_calls,
        "budget": state.budget,
        "decisionReasoning": state.decision_reasoning,
        "currentTarget": state.current_target,
        "previousTarget": state.previous_target,
        "recentImprovements": list(state.recent_improvements),
        "improvementSummary": improvement_summary,
        "metrics": metrics,
    }

    if log_router is not None:
        snapshot["logStreams"] = log_router.snapshot()

    if isinstance(state, ParallelAgentState):
        batch_results = [[result.to_dict() for result in batch] for batch in state.batch_results]
        parallel_payload = {
            "currentBatch": state.current_batch,
            "parallelStats": dict(state.parallel_stats),
            "activeTargets": state.get_active_target_details(),
            "targetLifecycle": state.get_task_lifecycle_details(),
            "workerCards": state.get_worker_cards(),
            "batchResults": batch_results,
        }
        snapshot["parallel"] = parallel_payload
        snapshot.update(parallel_payload)

    return snapshot
