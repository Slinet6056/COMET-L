import copy
import json
import logging
import sqlite3
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from comet.agent.state import AgentState, ParallelAgentState
from comet.config import Settings
from comet.utils.log_context import ContextFilter
from comet.web.log_router import RunLogRouter
from comet.web.runtime_protocol import RuntimeEventBus, build_run_snapshot

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
logger = logging.getLogger(__name__)


SystemInitializer = Callable[..., dict[str, Any]]
EvolutionRunner = Callable[..., None]
SettingsLoader = Callable[[str | None], Settings]


class ColoredFormatter(logging.Formatter):
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    BRIGHT_BLACK = "\033[90m"

    LEVEL_COLORS = {
        logging.DEBUG: CYAN,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: f"{BOLD}{RED}",
    }

    def __init__(self, fmt: str, datefmt: str | None = None) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:
        original_levelname = record.levelname
        level_color = self.LEVEL_COLORS.get(record.levelno, "")
        timestamp = self.formatTime(record, self.datefmt)

        if level_color:
            record.levelname = f"{level_color}{record.levelname}{self.RESET}"

        try:
            formatted = super().format(record)
            if timestamp and formatted.startswith(timestamp):
                rest = formatted[len(timestamp) :]
                formatted = f"{self.BRIGHT_BLACK}{timestamp}{self.RESET}{rest}"
            return formatted
        finally:
            record.levelname = original_levelname


@dataclass(slots=True)
class RunRequest:
    project_path: str
    config_path: str = "config.yaml"
    max_iterations: Optional[int] = None
    budget: Optional[int] = None
    resume_state: Optional[str] = None
    debug: bool = False
    bug_reports_dir: Optional[str] = None
    parallel: bool = False
    parallel_targets: Optional[int] = None
    log_file: Optional[str] = None
    runtime_roots: dict[str, str] = field(default_factory=dict)
    observer: Optional[Callable[[dict[str, object]], None]] = None


@dataclass(slots=True)
class RunSession:
    run_id: str
    status: str
    created_at: str
    project_path: str
    config_path: str
    paths: dict[str, str]
    path_snapshot: dict[str, str]
    config_snapshot: dict[str, Any]
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    failed_at: Optional[str] = None
    error: Optional[str] = None
    is_historical: bool = False


class ActiveRunConflictError(RuntimeError):
    pass


class RunLifecycleService:
    def __init__(self, workspace_root: Path | str = ".") -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self._lock = threading.RLock()
        self._sessions: dict[str, RunSession] = {}
        self._requests: dict[str, RunRequest] = {}
        self._event_buses: dict[str, RuntimeEventBus] = {}
        self._log_routers: dict[str, RunLogRouter] = {}
        self._runtime_snapshots: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._active_run_id: Optional[str] = None
        self._load_persisted_sessions()

    def create_run(
        self,
        request: RunRequest,
        *,
        settings_loader: Callable[[Optional[str]], Settings] = Settings.from_yaml_or_default,
    ) -> RunSession:
        with self._lock:
            if self._active_run_id is not None:
                active = self._sessions[self._active_run_id]
                if active.status in {"created", "running"}:
                    raise ActiveRunConflictError(
                        f"active run already exists: {self._active_run_id}"
                    )

            run_id = self._new_run_id()
            scoped_paths = self._build_scoped_paths(run_id)

            config = settings_loader(request.config_path)
            scoped_request = self._build_scoped_request(request, scoped_paths)
            apply_scoped_runtime_paths(config, scoped_paths)
            apply_run_overrides(config, scoped_request)
            config.ensure_directories()

            self._ensure_scoped_directories(scoped_paths)
            self._write_config_snapshot(config, scoped_paths["resolved_config"])

            session = RunSession(
                run_id=run_id,
                status="created",
                created_at=_utc_now_iso(),
                project_path=request.project_path,
                config_path=request.config_path,
                paths=scoped_paths,
                path_snapshot={
                    "state": scoped_paths["state"],
                    "output": scoped_paths["output"],
                    "sandbox": scoped_paths["sandbox"],
                    "log": scoped_paths["log"],
                    "database": scoped_paths["database"],
                },
                config_snapshot=config.to_dict(),
            )

            self._sessions[run_id] = session
            self._requests[run_id] = scoped_request
            self._event_buses[run_id] = RuntimeEventBus()
            self._log_routers[run_id] = RunLogRouter()
            self._log_routers[run_id].ensure_stream(
                "main", status="pending", started_at=session.created_at
            )
            self._runtime_snapshots[run_id] = self._build_runtime_snapshot(run_id)
            self._active_run_id = run_id
            self._persist_session_manifest(session)
            return session

    def get_run_request(self, run_id: str) -> RunRequest:
        return self._requests[run_id]

    def get_session(self, run_id: str) -> RunSession:
        return self._sessions[run_id]

    def get_event_bus(self, run_id: str) -> RuntimeEventBus:
        return self._event_buses[run_id]

    def get_log_router(self, run_id: str) -> RunLogRouter:
        return self._log_routers[run_id]

    def active_run_id(self) -> Optional[str]:
        with self._lock:
            if self._active_run_id is None:
                return None
            session = self._sessions.get(self._active_run_id)
            if session is None:
                self._active_run_id = None
                return None
            if session.status in {"created", "running"}:
                return self._active_run_id
            return None

    def run_mode(self, run_id: str) -> str:
        request = self._requests[run_id]
        session = self._sessions[run_id]
        parallel_enabled = bool(
            request.parallel
            or request.parallel_targets is not None
            or session.config_snapshot.get("agent", {}).get("parallel", {}).get("enabled", False)
        )
        return "parallel" if parallel_enabled else "standard"

    def build_snapshot(self, run_id: str) -> dict[str, Any]:
        session = self._sessions[run_id]
        snapshot = copy.deepcopy(self._runtime_snapshots.get(run_id, {}))
        if not snapshot:
            snapshot = self._build_runtime_snapshot(run_id)

        base_phase = self._build_phase(session)
        runtime_phase: dict[str, Any] = {}
        phase_value = snapshot.get("phase")
        if isinstance(phase_value, dict):
            for key, value in phase_value.items():
                if isinstance(key, str):
                    runtime_phase[key] = value
        merged_phase: dict[str, Any] = {}
        if session.status in {"completed", "failed"}:
            merged_phase.update(runtime_phase)
            merged_phase.update(base_phase)
        else:
            merged_phase.update(base_phase)
            merged_phase.update(runtime_phase)
        snapshot["phase"] = merged_phase
        snapshot["status"] = session.status
        snapshot["artifacts"] = self._build_artifacts(session)
        snapshot["logStreams"] = self.get_log_streams_snapshot(run_id)
        snapshot["isHistorical"] = session.is_historical
        return snapshot

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            run_ids = sorted(
                self._sessions,
                key=lambda current_run_id: self._sessions[current_run_id].created_at,
                reverse=True,
            )

        summaries: list[dict[str, Any]] = []
        for run_id in run_ids:
            session = self._sessions[run_id]
            snapshot = self.build_snapshot(run_id)
            summaries.append(
                {
                    "runId": run_id,
                    "status": session.status,
                    "mode": snapshot["mode"],
                    "projectPath": session.project_path,
                    "configPath": session.config_path,
                    "createdAt": session.created_at,
                    "startedAt": session.started_at,
                    "completedAt": session.completed_at,
                    "failedAt": session.failed_at,
                    "error": session.error,
                    "iteration": snapshot["iteration"],
                    "llmCalls": snapshot["llmCalls"],
                    "budget": snapshot["budget"],
                    "phase": snapshot["phase"],
                    "metrics": snapshot["metrics"],
                    "artifacts": snapshot["artifacts"],
                    "isHistorical": session.is_historical,
                }
            )
        return summaries

    def get_log_streams_snapshot(self, run_id: str) -> dict[str, Any]:
        session = self._sessions[run_id]
        log_router = self._log_routers[run_id]
        state = self._load_state_snapshot(run_id)
        if isinstance(state, ParallelAgentState):
            log_router.sync_parallel_state(state)

        main_duration: float | None = None
        if session.started_at and (session.completed_at or session.failed_at):
            started_at = datetime.fromisoformat(session.started_at)
            ended_at = datetime.fromisoformat(session.completed_at or session.failed_at or "")
            main_duration = max((ended_at - started_at).total_seconds(), 0.0)

        main_status = "running"
        if session.status == "created":
            main_status = "pending"
        elif session.status == "completed":
            main_status = "completed"
        elif session.status == "failed":
            main_status = "failed"

        log_router.ensure_stream(
            "main",
            status=main_status,
            started_at=session.started_at or session.created_at,
            ended_at=session.completed_at or session.failed_at,
            completed_at=session.completed_at,
            duration_seconds=main_duration,
        )
        return log_router.snapshot()

    def get_task_log_payload(self, run_id: str, task_id: str) -> dict[str, Any]:
        log_router = self._log_routers[run_id]
        streams = self.get_log_streams_snapshot(run_id)
        stream = streams["byTaskId"].get(task_id)
        if stream is None:
            raise KeyError(task_id)

        return {
            "runId": run_id,
            "taskId": task_id,
            "availableTaskIds": streams["taskIds"],
            "maxEntriesPerStream": log_router.max_entries_per_stream,
            "stream": stream,
            "entries": log_router.get_logs(task_id),
        }

    def publish_runtime_snapshot(
        self,
        run_id: str,
        *,
        state: AgentState | None = None,
        phase: Optional[dict[str, object]] = None,
        **snapshot_updates: object,
    ) -> None:
        with self._lock:
            if run_id not in self._sessions:
                raise KeyError(run_id)

            session = self._sessions[run_id]
            event_bus = self._event_buses[run_id]
            log_router = self._log_routers[run_id]
            snapshot = (
                build_run_snapshot(run_id, session.status, state, log_router=log_router)
                if state is not None
                else copy.deepcopy(self._runtime_snapshots.get(run_id, {}))
            )

            if not snapshot:
                snapshot = self._build_runtime_snapshot(run_id)

            base_phase = self._build_phase(session)
            runtime_phase: dict[str, Any] = {}
            phase_value = snapshot.get("phase")
            if isinstance(phase_value, dict):
                for key, value in phase_value.items():
                    if isinstance(key, str):
                        runtime_phase[key] = value

            phase_payload: dict[str, Any] = {}
            if isinstance(phase, dict):
                for key, value in phase.items():
                    if isinstance(key, str):
                        phase_payload[key] = value
            merged_phase: dict[str, Any] = {}
            if session.status in {"completed", "failed"}:
                merged_phase.update(runtime_phase)
                merged_phase.update(phase_payload)
                merged_phase.update(base_phase)
            else:
                merged_phase.update(base_phase)
                merged_phase.update(runtime_phase)
                merged_phase.update(phase_payload)
            snapshot["phase"] = merged_phase

            snapshot["status"] = session.status
            snapshot["artifacts"] = self._build_artifacts(session)
            snapshot["logStreams"] = self.get_log_streams_snapshot(run_id)

            for key, value in snapshot_updates.items():
                snapshot[key] = value

            self._runtime_snapshots[run_id] = copy.deepcopy(snapshot)

        event_bus.publish(
            "run.snapshot",
            runId=run_id,
            status=str(snapshot["status"]),
            mode=str(snapshot["mode"]),
            snapshot=copy.deepcopy(snapshot),
        )

    def build_results(self, run_id: str) -> dict[str, Any]:
        session = self._sessions[run_id]
        snapshot = self.build_snapshot(run_id)
        database_summary = self._build_database_summary(Path(session.paths["database"]))
        artifact_summary = {
            "finalState": self._build_download_artifact(
                run_id,
                session.paths["final_state"],
                content_type="application/json",
                download_name="final_state.json",
                artifact_slug="final-state",
            ),
            "runLog": self._build_download_artifact(
                run_id,
                session.paths["log"],
                content_type="text/plain; charset=utf-8",
                download_name="run.log",
                artifact_slug="run-log",
            ),
        }
        return {
            "runId": snapshot["runId"],
            "status": snapshot["status"],
            "mode": snapshot["mode"],
            "iteration": snapshot["iteration"],
            "llmCalls": snapshot["llmCalls"],
            "budget": snapshot["budget"],
            "phase": snapshot["phase"],
            "summary": {
                "metrics": snapshot["metrics"],
                "tests": database_summary["tests"],
                "mutants": database_summary["mutants"],
                "coverage": database_summary["coverage"],
                "sources": {
                    "finalState": artifact_summary["finalState"]["exists"],
                    "database": database_summary["databaseAvailable"],
                    "runLog": artifact_summary["runLog"]["exists"],
                },
            },
            "artifacts": artifact_summary,
        }

    def get_download_artifact(self, run_id: str, artifact_slug: str) -> dict[str, Any]:
        session = self._sessions[run_id]
        artifact_map = {
            "final-state": self._build_download_artifact(
                run_id,
                session.paths["final_state"],
                content_type="application/json",
                download_name="final_state.json",
                artifact_slug="final-state",
                include_file_path=True,
            ),
            "run-log": self._build_download_artifact(
                run_id,
                session.paths["log"],
                content_type="text/plain; charset=utf-8",
                download_name="run.log",
                artifact_slug="run-log",
                include_file_path=True,
            ),
        }
        if artifact_slug not in artifact_map:
            raise KeyError(artifact_slug)
        return artifact_map[artifact_slug]

    def start_run(
        self,
        run_id: str,
        *,
        settings_loader: SettingsLoader = Settings.from_yaml_or_default,
        system_initializer: SystemInitializer,
        evolution_runner: EvolutionRunner,
    ) -> None:
        thread = threading.Thread(
            target=self._run_in_background,
            args=(run_id,),
            kwargs={
                "settings_loader": settings_loader,
                "system_initializer": system_initializer,
                "evolution_runner": evolution_runner,
            },
            daemon=True,
            name=f"comet-web-{run_id}",
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()

    def mark_running(self, run_id: str) -> None:
        with self._lock:
            session = self._sessions[run_id]
            session.status = "running"
            session.started_at = _utc_now_iso()
            self._log_routers[run_id].ensure_stream(
                "main", status="running", started_at=session.started_at
            )
            self._persist_session_manifest(session)
        self.publish_runtime_snapshot(run_id)

    def mark_completed(self, run_id: str) -> None:
        with self._lock:
            session = self._sessions[run_id]
            session.status = "completed"
            session.completed_at = _utc_now_iso()
            duration_seconds: float | None = None
            if session.started_at is not None:
                duration_seconds = max(
                    (
                        datetime.fromisoformat(session.completed_at)
                        - datetime.fromisoformat(session.started_at)
                    ).total_seconds(),
                    0.0,
                )
            self._log_routers[run_id].ensure_stream(
                "main",
                status="completed",
                started_at=session.started_at or session.created_at,
                ended_at=session.completed_at,
                completed_at=session.completed_at,
                duration_seconds=duration_seconds,
            )
            if self._active_run_id == run_id:
                self._active_run_id = None
            self._persist_session_manifest(session)
        self.publish_runtime_snapshot(run_id, state=self._load_state_snapshot(run_id))

    def mark_failed(self, run_id: str, error: str) -> None:
        with self._lock:
            session = self._sessions[run_id]
            session.status = "failed"
            session.failed_at = _utc_now_iso()
            duration_seconds: float | None = None
            if session.started_at is not None:
                duration_seconds = max(
                    (
                        datetime.fromisoformat(session.failed_at)
                        - datetime.fromisoformat(session.started_at)
                    ).total_seconds(),
                    0.0,
                )
            self._log_routers[run_id].ensure_stream(
                "main",
                status="failed",
                started_at=session.started_at or session.created_at,
                ended_at=session.failed_at,
                duration_seconds=duration_seconds,
            )
            session.error = error
            if self._active_run_id == run_id:
                self._active_run_id = None
            self._persist_session_manifest(session)
        self.publish_runtime_snapshot(run_id, state=self._load_state_snapshot(run_id), error=error)

    def _run_in_background(
        self,
        run_id: str,
        *,
        settings_loader: SettingsLoader,
        system_initializer: SystemInitializer,
        evolution_runner: EvolutionRunner,
    ) -> None:
        self.mark_running(run_id)
        request = self._requests[run_id]
        event_bus = self._event_buses[run_id]
        log_router = self._log_routers[run_id]

        try:
            run_request(
                request,
                settings_loader=settings_loader,
                system_initializer=system_initializer,
                evolution_runner=evolution_runner,
                observer=event_bus,
                log_router=log_router,
                runtime_snapshot_publisher=(
                    lambda **payload: self.publish_runtime_snapshot(run_id, **payload)
                ),
            )
        except Exception as exc:
            self.mark_failed(run_id, str(exc))
            return

        self.mark_completed(run_id)

    def _new_state_for_run(self, run_id: str) -> AgentState:
        if self.run_mode(run_id) == "parallel":
            return ParallelAgentState()
        return AgentState()

    def _load_state_snapshot(self, run_id: str) -> AgentState | None:
        session = self._sessions[run_id]
        for artifact_key in ("final_state", "interrupted_state"):
            artifact_path = Path(session.paths[artifact_key])
            if not artifact_path.exists():
                continue

            try:
                data = json.loads(artifact_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("state snapshot must be an object")
                if self.run_mode(run_id) == "parallel" or self._looks_parallel(data):
                    return ParallelAgentState.from_dict(data)
                return AgentState.from_dict(data)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning("加载运行状态文件失败 %s: %s", artifact_path, exc)
                continue

        return None

    def _looks_parallel(self, payload: dict[str, Any]) -> bool:
        return any(
            key in payload
            for key in (
                "parallel_stats",
                "parallelStats",
                "current_batch",
                "currentBatch",
            )
        )

    def _build_phase(self, session: RunSession) -> dict[str, str | None]:
        phase_map = {
            "created": ("queued", "Queued"),
            "running": ("running", "Running"),
            "completed": ("completed", "Completed"),
            "failed": ("failed", "Failed"),
        }
        key, label = phase_map.get(session.status, (session.status, session.status.title()))
        return {
            "key": key,
            "label": label,
            "createdAt": session.created_at,
            "startedAt": session.started_at,
            "completedAt": session.completed_at,
            "failedAt": session.failed_at,
        }

    def _build_runtime_snapshot(self, run_id: str) -> dict[str, Any]:
        session = self._sessions[run_id]
        state = self._load_state_snapshot(run_id)
        if state is None:
            state = self._new_state_for_run(run_id)

        snapshot = build_run_snapshot(
            run_id,
            session.status,
            state,
            log_router=self._log_routers.get(run_id),
        )
        snapshot["phase"] = self._build_phase(session)
        snapshot["artifacts"] = self._build_artifacts(session)
        return snapshot

    def _build_artifacts(self, session: RunSession) -> dict[str, dict[str, object]]:
        paths = {
            "state": session.paths["state"],
            "output": session.paths["output"],
            "sandbox": session.paths["sandbox"],
            "log": session.paths["log"],
            "database": session.paths["database"],
            "resolvedConfig": session.paths["resolved_config"],
            "finalState": session.paths["final_state"],
            "interruptedState": session.paths["interrupted_state"],
        }
        artifacts: dict[str, dict[str, object]] = {}
        for name, path in paths.items():
            artifacts[name] = {"exists": Path(path).exists()}
        artifacts["finalState"]["downloadUrl"] = f"/api/runs/{session.run_id}/artifacts/final-state"
        artifacts["log"]["downloadUrl"] = f"/api/runs/{session.run_id}/artifacts/run-log"
        return artifacts

    def _build_download_artifact(
        self,
        run_id: str,
        artifact_path: str,
        *,
        content_type: str,
        download_name: str,
        artifact_slug: str,
        include_file_path: bool = False,
    ) -> dict[str, Any]:
        resolved_path = Path(artifact_path)
        exists = resolved_path.exists()
        stat = resolved_path.stat() if exists else None
        artifact = {
            "exists": exists,
            "filename": download_name,
            "contentType": content_type,
            "sizeBytes": stat.st_size if stat is not None else None,
            "updatedAt": (
                datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                if stat is not None
                else None
            ),
            "downloadUrl": f"/api/runs/{run_id}/artifacts/{artifact_slug}",
        }
        if include_file_path:
            artifact["filePath"] = str(resolved_path)
        return artifact

    def _build_database_summary(self, database_path: Path) -> dict[str, Any]:
        empty_summary = {
            "databaseAvailable": False,
            "tests": {
                "totalCases": 0,
                "compiledCases": 0,
                "totalMethods": 0,
                "targetMethods": 0,
            },
            "mutants": {
                "total": 0,
                "evaluated": 0,
                "killed": 0,
                "survived": 0,
                "pending": 0,
                "valid": 0,
                "invalid": 0,
                "outdated": 0,
            },
            "coverage": {
                "latestIteration": None,
                "methodsTracked": 0,
                "averageLineCoverage": None,
                "averageBranchCoverage": None,
            },
        }
        if not database_path.exists() or database_path.stat().st_size == 0:
            return empty_summary

        try:
            connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            return empty_summary

        try:
            cursor = connection.cursor()
            if not self._has_table(cursor, "test_cases"):
                return empty_summary

            tests_row = cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_cases,
                    COALESCE(SUM(CASE WHEN compile_success = 1 THEN 1 ELSE 0 END), 0) AS compiled_cases
                FROM test_cases
                """
            ).fetchone()
            methods_total = self._count_rows(cursor, "test_methods")
            target_methods = self._count_distinct_target_methods(cursor)

            mutants = self._summarize_mutants(cursor)
            coverage = self._summarize_coverage(cursor)
            return {
                "databaseAvailable": True,
                "tests": {
                    "totalCases": int(tests_row["total_cases"] or 0),
                    "compiledCases": int(tests_row["compiled_cases"] or 0),
                    "totalMethods": methods_total,
                    "targetMethods": target_methods,
                },
                "mutants": mutants,
                "coverage": coverage,
            }
        except sqlite3.Error:
            return empty_summary
        finally:
            connection.close()

    def _has_table(self, cursor: sqlite3.Cursor, table_name: str) -> bool:
        row = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _count_rows(self, cursor: sqlite3.Cursor, table_name: str) -> int:
        if not self._has_table(cursor, table_name):
            return 0
        row = cursor.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        return int(row["count"] or 0)

    def _count_distinct_target_methods(self, cursor: sqlite3.Cursor) -> int:
        if not self._has_table(cursor, "test_methods"):
            return 0
        row = cursor.execute(
            "SELECT COUNT(DISTINCT target_method) AS count FROM test_methods"
        ).fetchone()
        return int(row["count"] or 0)

    def _summarize_mutants(self, cursor: sqlite3.Cursor) -> dict[str, int]:
        if not self._has_table(cursor, "mutants"):
            return {
                "total": 0,
                "evaluated": 0,
                "killed": 0,
                "survived": 0,
                "pending": 0,
                "valid": 0,
                "invalid": 0,
                "outdated": 0,
            }
        row = cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN evaluated_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS evaluated,
                COALESCE(SUM(CASE WHEN status = 'killed' THEN 1 ELSE 0 END), 0) AS killed,
                COALESCE(SUM(CASE WHEN survived = 1 THEN 1 ELSE 0 END), 0) AS survived,
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
                COALESCE(SUM(CASE WHEN status = 'valid' THEN 1 ELSE 0 END), 0) AS valid,
                COALESCE(SUM(CASE WHEN status = 'invalid' THEN 1 ELSE 0 END), 0) AS invalid,
                COALESCE(SUM(CASE WHEN status = 'outdated' THEN 1 ELSE 0 END), 0) AS outdated
            FROM mutants
            """
        ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "evaluated": int(row["evaluated"] or 0),
            "killed": int(row["killed"] or 0),
            "survived": int(row["survived"] or 0),
            "pending": int(row["pending"] or 0),
            "valid": int(row["valid"] or 0),
            "invalid": int(row["invalid"] or 0),
            "outdated": int(row["outdated"] or 0),
        }

    def _summarize_coverage(self, cursor: sqlite3.Cursor) -> dict[str, int | float | None]:
        if not self._has_table(cursor, "method_coverage"):
            return {
                "latestIteration": None,
                "methodsTracked": 0,
                "averageLineCoverage": None,
                "averageBranchCoverage": None,
            }

        latest_iteration_row = cursor.execute(
            "SELECT MAX(iteration) AS latest_iteration FROM method_coverage"
        ).fetchone()
        latest_iteration = latest_iteration_row["latest_iteration"]
        if latest_iteration is None:
            return {
                "latestIteration": None,
                "methodsTracked": 0,
                "averageLineCoverage": None,
                "averageBranchCoverage": None,
            }

        coverage_row = cursor.execute(
            """
            SELECT
                COUNT(*) AS methods_tracked,
                AVG(line_coverage) AS average_line_coverage,
                AVG(branch_coverage) AS average_branch_coverage
            FROM method_coverage
            WHERE iteration = ?
            """,
            (latest_iteration,),
        ).fetchone()
        return {
            "latestIteration": int(latest_iteration),
            "methodsTracked": int(coverage_row["methods_tracked"] or 0),
            "averageLineCoverage": (
                float(coverage_row["average_line_coverage"])
                if coverage_row["average_line_coverage"] is not None
                else None
            ),
            "averageBranchCoverage": (
                float(coverage_row["average_branch_coverage"])
                if coverage_row["average_branch_coverage"] is not None
                else None
            ),
        }

    def _new_run_id(self) -> str:
        return f"run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"

    def _session_manifest_path(self, run_id: str) -> Path:
        return self.workspace_root / "state" / "runs" / run_id / "session.json"

    def _persist_session_manifest(self, session: RunSession) -> None:
        manifest_path = self._session_manifest_path(session.run_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(asdict(session), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_persisted_sessions(self) -> None:
        manifests_root = self.workspace_root / "state" / "runs"
        if not manifests_root.is_dir():
            return

        for manifest_path in sorted(manifests_root.glob("*/session.json")):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                session = self._deserialize_session(payload, manifest_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning("跳过损坏的运行记录清单 %s: %s", manifest_path, exc)
                continue

            session = self._normalize_restored_session(session)
            session.is_historical = True
            run_id = session.run_id
            self._sessions[run_id] = session
            self._requests[run_id] = self._build_restored_request(session)
            self._event_buses[run_id] = RuntimeEventBus()
            self._log_routers[run_id] = RunLogRouter()
            self._log_routers[run_id].ensure_stream(
                "main",
                status=self._session_to_stream_status(session.status),
                started_at=session.started_at or session.created_at,
                ended_at=session.completed_at or session.failed_at,
                completed_at=session.completed_at,
            )

            try:
                self._runtime_snapshots[run_id] = self._build_runtime_snapshot(run_id)
            except Exception as exc:
                logger.warning("恢复运行 %s 的快照失败: %s", run_id, exc)
                session.status = "failed"
                session.failed_at = session.failed_at or _utc_now_iso()
                session.error = session.error or "历史运行恢复失败，请检查状态文件是否损坏。"
                self._runtime_snapshots[run_id] = self._build_runtime_snapshot(run_id)

            self._persist_session_manifest(session)

    def _deserialize_session(self, payload: object, manifest_path: Path) -> RunSession:
        if not isinstance(payload, dict):
            raise ValueError("session manifest must be an object")

        session = RunSession(**payload)
        self._validate_restored_session_paths(session, manifest_path)
        return session

    def _validate_restored_session_paths(self, session: RunSession, manifest_path: Path) -> None:
        required_paths = {
            "state",
            "output",
            "sandbox",
            "log",
            "database",
            "resolved_config",
            "final_state",
            "interrupted_state",
        }
        missing_paths = required_paths - set(session.paths)
        if missing_paths:
            raise ValueError(f"missing paths: {sorted(missing_paths)}")

        expected_run_dir = manifest_path.parent.resolve()
        if expected_run_dir.name != session.run_id:
            raise ValueError("run id does not match manifest directory")

        allowed_roots = (
            self.workspace_root / "state",
            self.workspace_root / "output",
            self.workspace_root / "sandbox",
            self.workspace_root / "logs",
        )
        for path_value in session.paths.values():
            path = Path(path_value).expanduser().resolve()
            if not any(path.is_relative_to(root.resolve()) for root in allowed_roots):
                raise ValueError(f"path escapes workspace roots: {path}")

    def _normalize_restored_session(self, session: RunSession) -> RunSession:
        if session.status not in {"created", "running"}:
            return session

        session.status = "failed"
        session.failed_at = session.failed_at or _utc_now_iso()
        session.error = session.error or "运行在 Web 服务重启后无法恢复，已标记为失败。"
        return session

    def _build_restored_request(self, session: RunSession) -> RunRequest:
        parallel_config = session.config_snapshot.get("agent", {}).get("parallel", {})
        parallel_enabled = bool(parallel_config.get("enabled", False))
        parallel_targets = parallel_config.get("max_parallel_targets")
        return RunRequest(
            project_path=session.project_path,
            config_path=session.config_path,
            parallel=parallel_enabled,
            parallel_targets=parallel_targets if isinstance(parallel_targets, int) else None,
            log_file=session.paths.get("log"),
            runtime_roots={
                "state": session.paths["state"],
                "output": session.paths["output"],
                "sandbox": session.paths["sandbox"],
            },
        )

    def _session_to_stream_status(self, status: str) -> str:
        if status == "created":
            return "pending"
        if status in {"completed", "failed", "running"}:
            return status
        return "pending"

    def _build_scoped_paths(self, run_id: str) -> dict[str, str]:
        state_root = self.workspace_root / "state" / "runs" / run_id
        output_root = self.workspace_root / "output" / "runs" / run_id
        sandbox_root = self.workspace_root / "sandbox" / "runs" / run_id
        log_file = self.workspace_root / "logs" / "runs" / run_id / "run.log"

        return {
            "state": str(state_root),
            "output": str(output_root),
            "sandbox": str(sandbox_root),
            "log": str(log_file),
            "database": str(state_root / "comet.db"),
            "knowledge_database": str(state_root / "knowledge.db"),
            "final_state": str(output_root / "final_state.json"),
            "interrupted_state": str(output_root / "interrupted_state.json"),
            "resolved_config": str(output_root / "resolved_config.json"),
        }

    def _build_scoped_request(
        self, request: RunRequest, scoped_paths: dict[str, str]
    ) -> RunRequest:
        return RunRequest(
            project_path=request.project_path,
            config_path=request.config_path,
            max_iterations=request.max_iterations,
            budget=request.budget,
            resume_state=request.resume_state,
            debug=request.debug,
            bug_reports_dir=request.bug_reports_dir,
            parallel=request.parallel,
            parallel_targets=request.parallel_targets,
            log_file=scoped_paths["log"],
            runtime_roots={
                "state": scoped_paths["state"],
                "output": scoped_paths["output"],
                "sandbox": scoped_paths["sandbox"],
            },
            observer=request.observer,
        )

    def _ensure_scoped_directories(self, scoped_paths: dict[str, str]) -> None:
        for key in ["state", "output", "sandbox"]:
            Path(scoped_paths[key]).mkdir(parents=True, exist_ok=True)
        Path(scoped_paths["log"]).parent.mkdir(parents=True, exist_ok=True)

    def _write_config_snapshot(self, config: Settings, config_snapshot_path: str) -> None:
        snapshot_path = Path(config_snapshot_path)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_event(
    observer: Optional[Callable[[dict[str, object]], None]],
    event_type: str,
    **payload: object,
) -> None:
    if observer is None:
        return

    event: dict[str, object] = {"type": event_type}
    for key, value in payload.items():
        event[key] = value
    observer(event)


def reset_managed_logging() -> None:
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_comet_managed", False):
            root_logger.removeHandler(handler)
            handler.close()


def configure_logging(
    log_file: str,
    *,
    level: str = "INFO",
    console_stream: Any = None,
    log_router: Optional[logging.Handler] = None,
) -> Path:
    reset_managed_logging()

    resolved_log_file = Path(log_file).expanduser().resolve()
    resolved_log_file.parent.mkdir(parents=True, exist_ok=True)

    context_filter = ContextFilter()

    file_handler = logging.FileHandler(resolved_log_file, encoding="utf-8")
    file_handler.__dict__["_comet_managed"] = True
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    file_handler.addFilter(context_filter)

    console_handler = logging.StreamHandler(console_stream or sys.stdout)
    console_handler.__dict__["_comet_managed"] = True
    console_handler.setFormatter(ColoredFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    console_handler.addFilter(context_filter)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    if log_router is not None:
        log_router.__dict__["_comet_managed"] = True
        log_router.addFilter(context_filter)
        root_logger.addHandler(log_router)

    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    return resolved_log_file


def build_run_request(args: Any) -> RunRequest:
    return RunRequest(
        project_path=args.project_path,
        config_path=args.config,
        max_iterations=args.max_iterations,
        budget=args.budget,
        resume_state=args.resume,
        debug=args.debug,
        bug_reports_dir=args.bug_reports_dir,
        parallel=args.parallel,
        parallel_targets=args.parallel_targets,
    )


def apply_run_overrides(config: Settings, request: RunRequest) -> None:
    if request.max_iterations is not None:
        config.evolution.max_iterations = request.max_iterations
    if request.budget is not None:
        config.evolution.budget_llm_calls = request.budget
    if request.parallel or request.parallel_targets is not None:
        config.agent.parallel.enabled = True
    if request.parallel_targets is not None:
        config.agent.parallel.max_parallel_targets = request.parallel_targets

    if request.log_file:
        config.logging.file = request.log_file


def apply_scoped_runtime_paths(config: Settings, scoped_paths: dict[str, str]) -> None:
    config.set_runtime_roots(
        state=Path(scoped_paths["state"]),
        output=Path(scoped_paths["output"]),
        sandbox=Path(scoped_paths["sandbox"]),
    )


def apply_runtime_roots(config: Settings, runtime_roots: dict[str, str]) -> None:
    state = runtime_roots.get("state")
    output = runtime_roots.get("output")
    sandbox = runtime_roots.get("sandbox")
    if not (state and output and sandbox):
        return

    config.set_runtime_roots(
        state=Path(state),
        output=Path(output),
        sandbox=Path(sandbox),
    )


def _resolve_bug_reports_dir(
    bug_reports_dir: Optional[str], logger: logging.Logger
) -> Optional[str]:
    if not bug_reports_dir:
        return None

    bug_dir = Path(bug_reports_dir)
    if bug_dir.exists() and bug_dir.is_dir():
        return str(bug_dir.resolve())

    logger.warning(f"Bug 报告目录不存在: {bug_reports_dir}")
    return None


def _validate_project_path(project_path: str, logger: logging.Logger) -> str:
    resolved_project_path = Path(project_path)
    if not resolved_project_path.exists():
        logger.error(f"项目路径不存在: {project_path}")
        raise FileNotFoundError(project_path)

    if not (resolved_project_path / "pom.xml").exists():
        logger.error(f"不是有效的 Maven 项目: {project_path}")
        raise ValueError(project_path)

    return str(resolved_project_path)


def run_request(
    request: RunRequest,
    *,
    settings_loader: SettingsLoader = Settings.from_yaml_or_default,
    system_initializer: SystemInitializer,
    evolution_runner: EvolutionRunner,
    logger: Optional[logging.Logger] = None,
    observer: Optional[Callable[[dict[str, object]], None]] = None,
    log_router: Optional[RunLogRouter] = None,
    runtime_snapshot_publisher: Optional[Callable[..., None]] = None,
) -> int:
    runtime_logger = logger or logging.getLogger(__name__)
    event_sink = observer or request.observer

    config = settings_loader(request.config_path)
    apply_runtime_roots(config, request.runtime_roots)
    apply_run_overrides(config, request)

    log_level = "DEBUG" if request.debug else config.logging.level
    resolved_log_path = configure_logging(
        config.logging.file,
        level=log_level,
        log_router=log_router,
    )

    runtime_logger.info(f"加载配置: {request.config_path}")
    runtime_logger.info(f"当前日志文件: {resolved_log_path}")

    if request.debug:
        runtime_logger.info("已启用调试模式 (DEBUG 日志)")

    project_path = _validate_project_path(request.project_path, runtime_logger)
    bug_reports_dir = _resolve_bug_reports_dir(request.bug_reports_dir, runtime_logger)

    parallel_mode = request.parallel or config.agent.parallel.enabled
    if request.parallel_targets is not None:
        config.agent.parallel.max_parallel_targets = request.parallel_targets
        parallel_mode = True

    emit_event(
        event_sink,
        "run.started",
        project_path=project_path,
        log_file=str(resolved_log_path),
        parallel_mode=parallel_mode,
        resume_state=request.resume_state,
    )

    try:
        components = system_initializer(config, bug_reports_dir, parallel_mode)
        if isinstance(components, dict) and runtime_snapshot_publisher is not None:
            components["runtime_snapshot_publisher"] = runtime_snapshot_publisher
        if isinstance(components, dict) and log_router is not None:
            components["log_router"] = log_router
        evolution_runner(project_path, components, request.resume_state)
    except Exception as exc:
        emit_event(
            event_sink,
            "run.failed",
            project_path=project_path,
            log_file=str(resolved_log_path),
            error=str(exc),
        )
        raise

    runtime_logger.info("COMET-L 运行完成")
    emit_event(
        event_sink,
        "run.completed",
        project_path=project_path,
        log_file=str(resolved_log_path),
        parallel_mode=parallel_mode,
    )
    return 0


def run_cli(
    args: Any,
    *,
    system_initializer: SystemInitializer,
    evolution_runner: EvolutionRunner,
    settings_loader: SettingsLoader = Settings.from_yaml_or_default,
) -> int:
    return run_request(
        build_run_request(args),
        settings_loader=settings_loader,
        system_initializer=system_initializer,
        evolution_runner=evolution_runner,
    )
