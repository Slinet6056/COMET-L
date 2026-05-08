import copy
import json
import logging
import shutil
import sqlite3
import sys
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional
from uuid import uuid4

from comet.agent.state import AgentState, ParallelAgentState
from comet.config import Settings
from comet.config.policy import enforce_deployment_policy, redacted_settings_dict
from comet.config.settings import DeploymentPolicyConfig, GitHubConfig
from comet.utils.log_context import ContextFilter
from comet.web.git_pr_service import GitHubPullRequestService, GitPullRequestError
from comet.web.log_router import RunLogRouter
from comet.web.repo_import_service import (
    GitHubRepoImportService,
    RepoImportBranchResolutionError,
    RepoImportCloneError,
    RepoImportNonMavenError,
    RepoImportPermissionError,
    RepoImportUrlError,
)
from comet.web.reporting import build_run_report, collect_generated_test_files, resolve_git_metadata
from comet.web.runtime_protocol import (
    RuntimeEventBus,
    build_run_snapshot,
    normalize_mutation_metrics,
)
from comet.web.storage import RunRecord, WebDatabase, WebDatabaseError

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
PENDING_STATUSES = {"pending"}
RUNNING_STATUSES = {"starting", "running", "cancelling"}
BOOT_STALE_STATUSES = {"starting", "running", "cancelling"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "stale"}
logger = logging.getLogger(__name__)
SAFE_ARTIFACTS = {
    "final-state": {
        "filename": "final_state.json",
        "content_type": "application/json",
        "root": "output",
    },
    "run-log": {
        "filename": "run.log",
        "content_type": "text/plain; charset=utf-8",
        "root": "log",
    },
    "report": {
        "filename": "report.md",
        "content_type": "text/markdown; charset=utf-8",
        "root": "output",
    },
    "resolved-config": {
        "filename": "resolved_config.json",
        "content_type": "application/json",
        "root": "output",
    },
    "final-tests.zip": {
        "filename": "final_tests.zip",
        "content_type": "application/zip",
        "root": "output",
    },
}


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
    config_path: str | None = "config.yaml"
    max_iterations: Optional[int] = None
    budget: Optional[int] = None
    mutation_enabled: Optional[bool] = None
    resume_state: Optional[str] = None
    debug: bool = False
    bug_reports_dir: Optional[str] = None
    parallel: bool = False
    parallel_targets: Optional[int] = None
    github_repo_url: Optional[str] = None
    github_base_branch: Optional[str] = None
    selected_java_version: Optional[str] = None
    log_file: Optional[str] = None
    runtime_roots: dict[str, str] = field(default_factory=dict)
    observer: Optional[Callable[[dict[str, object]], None]] = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


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
    project_source_type: str = "local"
    bug_reports_path: Optional[str] = None
    error: Optional[str] = None
    queue_position: Optional[int] = None
    cancel_requested: bool = False
    cancellation_reason: Optional[str] = None
    is_historical: bool = False
    user_id: Optional[int] = None
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def finished_at(self) -> Optional[str]:
        return self.completed_at or self.failed_at


class ActiveRunConflictError(RuntimeError):
    pass


class QueueLimitExceededError(RuntimeError):
    pass


class GitHubUnauthorizedError(RuntimeError):
    pass


class InvalidGitHubRepoUrlError(ValueError):
    pass


class InvalidJavaVersionError(ValueError):
    pass


class GitNoWritePermissionError(PermissionError):
    pass


class GitBranchConflictError(RuntimeError):
    pass


class ReportGenerationError(RuntimeError):
    pass


class NonMavenRepositoryError(ValueError):
    pass


class GitCloneError(RuntimeError):
    pass


class GitDefaultBranchResolutionError(RuntimeError):
    pass


class RunLifecycleService:
    def __init__(
        self,
        workspace_root: Path | str = ".",
        *,
        repo_import_service: GitHubRepoImportService | None = None,
        pull_request_service: GitHubPullRequestService | None = None,
        web_database: WebDatabase | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self._lock = threading.RLock()
        self._sessions: dict[str, RunSession] = {}
        self._requests: dict[str, RunRequest] = {}
        self._event_buses: dict[str, RuntimeEventBus] = {}
        self._log_routers: dict[str, RunLogRouter] = {}
        self._runtime_snapshots: dict[str, dict[str, Any]] = {}
        self._github_runtime_configs: dict[str, GitHubConfig] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._run_controls: dict[str, threading.Event] = {}
        self._scheduler_specs: dict[
            str, tuple[SettingsLoader, SystemInitializer, EvolutionRunner]
        ] = {}
        self._active_run_id: Optional[str] = None
        self._repo_import_service = repo_import_service
        self._pull_request_service = pull_request_service
        self._web_database = web_database or WebDatabase.for_workspace(self.workspace_root)
        try:
            self._web_database.bootstrap()
        except WebDatabaseError:
            if web_database is not None:
                raise
            logger.warning("跳过不兼容的默认 Web 数据库: %s", self._web_database.db_path)
            self._web_database = None
        if self._web_database is not None:
            self._load_persisted_sessions()

    def set_repo_import_service(self, repo_import_service: GitHubRepoImportService) -> None:
        self._repo_import_service = repo_import_service

    def set_pull_request_service(self, pull_request_service: GitHubPullRequestService) -> None:
        self._pull_request_service = pull_request_service

    def set_web_database(self, web_database: WebDatabase) -> None:
        self._web_database = web_database
        self._web_database.bootstrap()
        self._load_persisted_sessions()

    def create_run(
        self,
        request: RunRequest,
        *,
        user_id: int | None = None,
        settings_loader: Callable[[Optional[str]], Settings] = Settings.from_yaml_or_default,
    ) -> RunSession:
        with self._lock:
            run_id = self._new_run_id()
            scoped_paths = self._build_scoped_paths(run_id, user_id=user_id)

            config = settings_loader(request.config_path)
            self._assert_queue_capacity(config, user_id=user_id)
            scoped_request = self._build_scoped_request(request, scoped_paths)
            apply_scoped_runtime_paths(config, scoped_paths)
            apply_run_overrides(config, scoped_request)
            enforce_deployment_policy(config)
            config.ensure_directories()

            if scoped_request.github_repo_url is not None:
                if self._repo_import_service is None:
                    raise GitCloneError("仓库导入服务未初始化，无法处理 GitHub 仓库请求。")
                try:
                    imported = self._repo_import_service.import_repository(
                        run_id=run_id,
                        github_repo_url=scoped_request.github_repo_url,
                        github_config=config.github,
                        requested_base_branch=scoped_request.github_base_branch,
                        user_key=_github_user_key(user_id),
                    )
                except RepoImportUrlError as exc:
                    raise InvalidGitHubRepoUrlError(str(exc)) from exc
                except RepoImportPermissionError as exc:
                    raise GitHubUnauthorizedError(str(exc)) from exc
                except RepoImportBranchResolutionError as exc:
                    raise GitDefaultBranchResolutionError(str(exc)) from exc
                except RepoImportCloneError as exc:
                    raise GitCloneError(str(exc)) from exc
                except RepoImportNonMavenError as exc:
                    raise NonMavenRepositoryError(str(exc)) from exc

                scoped_request.project_path = imported.project_path
                scoped_request.github_base_branch = imported.base_branch
                config.github.base_branch = imported.base_branch
                config.github.repo_url = scoped_request.github_repo_url

            self._ensure_scoped_directories(scoped_paths)
            safe_config_snapshot, _ = redacted_settings_dict(config)
            self._write_config_snapshot(safe_config_snapshot, scoped_paths["resolved_config"])
            scoped_request.config_path = scoped_paths["resolved_config"]

            session = RunSession(
                run_id=run_id,
                status="pending",
                user_id=user_id,
                created_at=_utc_now_iso(),
                project_path=scoped_request.project_path,
                config_path=scoped_request.config_path,
                paths=scoped_paths,
                path_snapshot={
                    "state": scoped_paths["state"],
                    "output": scoped_paths["output"],
                    "sandbox": scoped_paths["sandbox"],
                    "log": scoped_paths["log"],
                    "database": scoped_paths["database"],
                },
                config_snapshot=safe_config_snapshot,
                project_source_type="github"
                if scoped_request.github_repo_url is not None
                else _normalize_project_source_type(scoped_request.source_metadata),
                bug_reports_path=scoped_request.bug_reports_dir,
                source_metadata=copy.deepcopy(scoped_request.source_metadata),
            )

            self._sessions[run_id] = session
            self._requests[run_id] = scoped_request
            self._event_buses[run_id] = RuntimeEventBus()
            self._log_routers[run_id] = RunLogRouter()
            if config.github.repo_url:
                self._github_runtime_configs[run_id] = config.github.model_copy(deep=True)
            self._log_routers[run_id].ensure_stream(
                "main", status="pending", started_at=session.created_at
            )
            self._runtime_snapshots[run_id] = self._build_runtime_snapshot(run_id)
            self._active_run_id = run_id
            self._persist_session(session)
            self._refresh_queue_positions_locked()
            return session

    def get_run_request(self, run_id: str) -> RunRequest:
        return self._requests[run_id]

    def get_session(self, run_id: str) -> RunSession:
        return self._sessions[run_id]

    def get_visible_session(
        self,
        run_id: str,
        *,
        user_id: int,
        include_all: bool = False,
    ) -> RunSession:
        if self._web_database is None:
            session = self._sessions[run_id]
            if include_all or session.user_id == user_id:
                return session
            raise KeyError(run_id)

        record = self._web_database.get_run_record(run_id)
        if record is None:
            raise KeyError(run_id)
        if not include_all and record.user_id != user_id:
            raise KeyError(run_id)

        try:
            session = self._session_from_record(record)
            self._validate_session_paths(session)
        except (TypeError, ValueError) as exc:
            logger.warning("跳过损坏的 SQLite 运行记录 %s: %s", record.id, exc)
            raise KeyError(run_id) from exc

        with self._lock:
            existing = self._sessions.get(run_id)
            if existing is not None:
                session.is_historical = existing.is_historical
            self._sessions[run_id] = session
            self._requests[run_id] = self._build_restored_request(session)
            event_bus = self._event_buses.setdefault(run_id, RuntimeEventBus())
            del event_bus
            log_router = self._log_routers.setdefault(run_id, RunLogRouter())
            log_router.ensure_stream(
                "main",
                status=self._session_to_stream_status(session.status),
                started_at=session.started_at or session.created_at,
                ended_at=session.completed_at or session.failed_at,
                completed_at=session.completed_at,
            )
            if run_id not in self._runtime_snapshots:
                try:
                    self._runtime_snapshots[run_id] = self._build_runtime_snapshot(run_id)
                except Exception as exc:
                    logger.warning("恢复运行 %s 的快照失败: %s", run_id, exc)
                    self._runtime_snapshots[run_id] = build_run_snapshot(
                        run_id,
                        session.status,
                        self._new_state_for_run(run_id),
                        log_router=log_router,
                    )
        return session

    def get_event_bus(self, run_id: str) -> RuntimeEventBus:
        return self._event_buses.setdefault(run_id, RuntimeEventBus())

    def get_log_router(self, run_id: str) -> RunLogRouter:
        return self._log_routers.setdefault(run_id, RunLogRouter())

    def active_run_id(self) -> Optional[str]:
        with self._lock:
            candidates = [
                session for session in self._sessions.values() if session.status in RUNNING_STATUSES
            ]
            if not candidates:
                self._active_run_id = None
                return None
            candidates.sort(
                key=lambda session: (session.started_at or session.created_at, session.run_id)
            )
            self._active_run_id = candidates[-1].run_id
            return self._active_run_id

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
        snapshot["selectedJavaVersion"] = self._resolve_selected_java_version(run_id)
        snapshot["artifacts"] = self._build_artifacts(session)
        snapshot["logStreams"] = self.get_log_streams_snapshot(run_id)
        snapshot["isHistorical"] = session.is_historical
        snapshot["queuePosition"] = session.queue_position
        snapshot["cancelRequested"] = session.cancel_requested
        snapshot["cancellationReason"] = session.cancellation_reason
        self._apply_mutation_semantics(run_id, snapshot)
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
                    "mutationEnabled": snapshot.get("mutationEnabled"),
                    "projectPath": session.project_path,
                    "configPath": session.config_path,
                    "createdAt": session.created_at,
                    "startedAt": session.started_at,
                    "completedAt": session.completed_at,
                    "finishedAt": session.finished_at,
                    "failedAt": session.failed_at,
                    "error": session.error,
                    "userId": session.user_id,
                    "projectSourceType": session.project_source_type,
                    "bugReportsPath": session.bug_reports_path,
                    "pathSnapshot": session.path_snapshot,
                    "queuePosition": session.queue_position,
                    "cancelRequested": session.cancel_requested,
                    "cancellationReason": session.cancellation_reason,
                    "iteration": snapshot["iteration"],
                    "llmCalls": snapshot["llmCalls"],
                    "budget": snapshot["budget"],
                    "selectedJavaVersion": snapshot.get("selectedJavaVersion"),
                    "phase": snapshot["phase"],
                    "metrics": snapshot["metrics"],
                    "artifacts": snapshot["artifacts"],
                    "isHistorical": session.is_historical,
                }
            )
        return summaries

    def list_runs_for_user(
        self, *, user_id: int, include_all: bool = False
    ) -> list[dict[str, Any]]:
        if self._web_database is None:
            return []
        records = self._web_database.list_run_records(user_id=user_id, include_all=include_all)
        summaries: list[dict[str, Any]] = []
        for record in records:
            if record.id not in self._sessions:
                try:
                    session = self._session_from_record(record)
                    session = self._normalize_restored_session(session)
                    session.is_historical = True
                    self._sessions[record.id] = session
                    self._requests[record.id] = self._build_restored_request(session)
                    self._event_buses[record.id] = RuntimeEventBus()
                    self._log_routers[record.id] = RunLogRouter()
                    self._runtime_snapshots[record.id] = self._build_runtime_snapshot(record.id)
                except (TypeError, ValueError) as exc:
                    logger.warning("跳过损坏的 SQLite 运行记录 %s: %s", record.id, exc)
                    continue
            snapshot = self.build_snapshot(record.id)
            session = self._sessions[record.id]
            summaries.append(
                {
                    "runId": record.id,
                    "status": session.status,
                    "mode": snapshot["mode"],
                    "mutationEnabled": snapshot.get("mutationEnabled"),
                    "projectPath": session.project_path,
                    "configPath": session.config_path,
                    "createdAt": session.created_at,
                    "startedAt": session.started_at,
                    "completedAt": session.completed_at,
                    "finishedAt": session.finished_at,
                    "failedAt": session.failed_at,
                    "error": session.error,
                    "iteration": snapshot["iteration"],
                    "llmCalls": snapshot["llmCalls"],
                    "budget": snapshot["budget"],
                    "selectedJavaVersion": snapshot.get("selectedJavaVersion"),
                    "phase": snapshot["phase"],
                    "metrics": snapshot["metrics"],
                    "artifacts": snapshot["artifacts"],
                    "isHistorical": session.is_historical,
                    "userId": session.user_id,
                    "projectSourceType": session.project_source_type,
                    "bugReportsPath": session.bug_reports_path,
                    "pathSnapshot": session.path_snapshot,
                    "queuePosition": session.queue_position,
                    "cancelRequested": session.cancel_requested,
                    "cancellationReason": session.cancellation_reason,
                }
            )
        return summaries

    def get_log_streams_snapshot(self, run_id: str) -> dict[str, Any]:
        session = self._sessions[run_id]
        log_router = self.get_log_router(run_id)
        state = self._load_state_snapshot(run_id)
        if isinstance(state, ParallelAgentState):
            log_router.sync_parallel_state(state)

        main_duration: float | None = None
        if session.started_at and (session.completed_at or session.failed_at):
            started_at = datetime.fromisoformat(session.started_at)
            ended_at = datetime.fromisoformat(session.completed_at or session.failed_at or "")
            main_duration = max((ended_at - started_at).total_seconds(), 0.0)

        main_status = "running"
        if session.status in {"created", "pending", "starting"}:
            main_status = "pending"
        elif session.status == "completed":
            main_status = "completed"
        elif session.status in {"failed", "stale"}:
            main_status = "failed"
        elif session.status == "cancelled":
            main_status = "cancelled"

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
        log_router = self.get_log_router(run_id)
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
            snapshot["selectedJavaVersion"] = self._resolve_selected_java_version(run_id)
            snapshot["artifacts"] = self._build_artifacts(session)
            snapshot["logStreams"] = self.get_log_streams_snapshot(run_id)
            snapshot["queuePosition"] = session.queue_position
            snapshot["cancelRequested"] = session.cancel_requested
            snapshot["cancellationReason"] = session.cancellation_reason

            for key, value in snapshot_updates.items():
                snapshot[key] = value

            self._apply_mutation_semantics(run_id, snapshot)

            self._runtime_snapshots[run_id] = copy.deepcopy(snapshot)

        event_bus.publish(
            "run.snapshot",
            runId=run_id,
            status=str(snapshot["status"]),
            mode=str(snapshot["mode"]),
            snapshot=copy.deepcopy(snapshot),
        )

    def build_results(self, run_id: str) -> dict[str, Any]:
        snapshot = self.build_snapshot(run_id)
        return self._build_results_payload(run_id, snapshot)

    def _build_timeout_overrun_summary(self, session: RunSession) -> dict[str, Any]:
        timeout_seconds = self._resolve_timeout_seconds_from_config(session.config_snapshot)
        finished_at = session.finished_at
        if timeout_seconds is None or session.started_at is None or finished_at is None:
            return {
                "timeoutExceeded": False,
                "timeoutOverrunSeconds": None,
            }

        started = datetime.fromisoformat(session.started_at)
        finished = datetime.fromisoformat(finished_at)
        elapsed_seconds = max((finished - started).total_seconds(), 0.0)
        overrun_seconds = elapsed_seconds - timeout_seconds
        if overrun_seconds <= 0:
            return {
                "timeoutExceeded": False,
                "timeoutOverrunSeconds": None,
            }

        return {
            "timeoutExceeded": True,
            "timeoutOverrunSeconds": overrun_seconds,
        }

    def _resolve_timeout_seconds_from_config(self, config_snapshot: dict[str, Any]) -> int | None:
        timeout = config_snapshot.get("execution", {}).get("timeout")
        if isinstance(timeout, int) and timeout > 0:
            return timeout
        return None

    def _build_results_payload(self, run_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        session = self._sessions[run_id]
        database_summary = self._build_database_summary(Path(session.paths["database"]))
        timeout_overrun_summary = self._build_timeout_overrun_summary(session)
        artifact_summary = {
            "finalState": self._build_download_artifact(
                run_id,
                artifact_slug="final-state",
            ),
            "runLog": self._build_download_artifact(
                run_id,
                artifact_slug="run-log",
            ),
            "resolvedConfig": self._build_download_artifact(
                run_id,
                artifact_slug="resolved-config",
            ),
        }
        report_artifact = self._build_download_artifact(
            run_id,
            artifact_slug="report",
        )
        final_tests_archive = self._build_final_tests_archive_artifact(run_id)
        pull_request_url = snapshot.get("pullRequestUrl")
        if not isinstance(pull_request_url, str):
            pull_request_url = None
        pull_request_error = snapshot.get("pullRequestError")
        if not isinstance(pull_request_error, str) or not pull_request_error.strip():
            pull_request_error = None
        if (
            pull_request_error is None
            and pull_request_url is None
            and report_artifact["exists"]
            and isinstance(session.error, str)
            and session.error.strip()
        ):
            pull_request_error = session.error.strip()
        payload = {
            "runId": snapshot["runId"],
            "status": snapshot["status"],
            "mode": snapshot["mode"],
            "projectSourceType": session.project_source_type,
            "mutationEnabled": snapshot.get("mutationEnabled"),
            **timeout_overrun_summary,
            "iteration": snapshot["iteration"],
            "llmCalls": snapshot["llmCalls"],
            "budget": snapshot["budget"],
            "selectedJavaVersion": snapshot.get("selectedJavaVersion"),
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
            "pullRequestUrl": pull_request_url,
            "pullRequestError": pull_request_error,
            "reportArtifact": report_artifact,
        }
        if final_tests_archive is not None:
            payload["finalTestsArchive"] = final_tests_archive
        return payload

    def get_download_artifact(self, run_id: str, artifact_slug: str) -> dict[str, Any]:
        if artifact_slug not in SAFE_ARTIFACTS:
            raise KeyError(artifact_slug)
        if artifact_slug == "final-tests.zip":
            self._ensure_final_tests_archive(run_id)
        return self._build_download_artifact(
            run_id,
            artifact_slug=artifact_slug,
            include_file_path=True,
        )

    def start_run(
        self,
        run_id: str,
        *,
        settings_loader: SettingsLoader = Settings.from_yaml_or_default,
        system_initializer: SystemInitializer,
        evolution_runner: EvolutionRunner,
    ) -> None:
        with self._lock:
            self._scheduler_specs[run_id] = (
                settings_loader,
                system_initializer,
                evolution_runner,
            )
        self.dispatch_pending_runs()

    def dispatch_pending_runs(self) -> None:
        # 单 FastAPI 进程假设：本调度器只在当前进程内领取 SQLite pending 运行。
        while True:
            dispatch: tuple[str, SettingsLoader, SystemInitializer, EvolutionRunner] | None = None
            with self._lock:
                self._refresh_queue_positions_locked()
                run_id = self._claim_next_pending_run_locked()
                if run_id is None:
                    return
                spec = self._scheduler_specs.get(run_id)
                if spec is None:
                    return
                dispatch = (run_id, *spec)
            self._start_claimed_run(*dispatch)

    def _start_claimed_run(
        self,
        run_id: str,
        settings_loader: SettingsLoader,
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
            self._run_controls[run_id] = threading.Event()
            self._threads[run_id] = thread
        thread.start()

    def cancel_run(self, run_id: str, *, reason: str = "用户取消运行。") -> RunSession:
        with self._lock:
            session = self._sessions[run_id]
            session.cancel_requested = True
            session.cancellation_reason = reason
            if session.status == "pending":
                session.status = "cancelled"
                session.failed_at = _utc_now_iso()
                session.queue_position = None
                self._scheduler_specs.pop(run_id, None)
                self._github_runtime_configs.pop(run_id, None)
            elif session.status in {"starting", "running"}:
                session.status = "cancelling"
            control = self._run_controls.get(run_id)
            if control is not None:
                control.set()
            self._persist_session(session)
            self._refresh_queue_positions_locked()
        self.publish_runtime_snapshot(run_id)
        self.dispatch_pending_runs()
        return session

    def mark_running(self, run_id: str) -> None:
        with self._lock:
            session = self._sessions[run_id]
            if session.cancel_requested:
                session.status = "cancelled"
                session.failed_at = _utc_now_iso()
                session.queue_position = None
                self._github_runtime_configs.pop(run_id, None)
                self._persist_session(session)
                return
            session.status = "running"
            session.started_at = _utc_now_iso()
            session.queue_position = None
            self._log_routers[run_id].ensure_stream(
                "main", status="running", started_at=session.started_at
            )
            self._persist_session(session)
        self.publish_runtime_snapshot(run_id)

    def mark_completed(self, run_id: str, *, completed_at: str | None = None) -> None:
        completion_time = completed_at or _utc_now_iso()
        with self._lock:
            session = self._sessions[run_id]
            if session.cancel_requested or session.status == "cancelling":
                self._finish_cancelled_run_locked(session, completed_at=completion_time)
                self.publish_runtime_snapshot(run_id)
                self.dispatch_pending_runs()
                return
        self._generate_report_artifact(run_id, completed_at=completion_time)
        pull_request_url: str | None = None
        pull_request_error: str | None = None
        try:
            pull_request_url = self._publish_generated_tests_pull_request(run_id)
        except GitPullRequestError as exc:
            pull_request_error = str(exc)
        preserved_snapshot_fields: dict[str, Any] = {}
        with self._lock:
            session = self._sessions[run_id]
            current_snapshot = self._runtime_snapshots.get(run_id, {})
            if isinstance(current_snapshot, dict):
                for key in ("pullRequestUrl", "pullRequestError"):
                    if key in current_snapshot:
                        preserved_snapshot_fields[key] = current_snapshot[key]
            if pull_request_url is not None:
                preserved_snapshot_fields["pullRequestUrl"] = pull_request_url
                preserved_snapshot_fields.pop("pullRequestError", None)
            if pull_request_error is not None:
                preserved_snapshot_fields["pullRequestError"] = pull_request_error
            session.status = "completed"
            session.completed_at = completion_time
            session.error = pull_request_error
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
            self._github_runtime_configs.pop(run_id, None)
            self._persist_session(session)
            self._scheduler_specs.pop(run_id, None)
            self._run_controls.pop(run_id, None)
        self.publish_runtime_snapshot(
            run_id,
            state=self._load_state_snapshot(run_id),
            **preserved_snapshot_fields,
        )
        self.dispatch_pending_runs()

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
            self._github_runtime_configs.pop(run_id, None)
            self._persist_session(session)
            self._scheduler_specs.pop(run_id, None)
            self._run_controls.pop(run_id, None)
        self.publish_runtime_snapshot(run_id, state=self._load_state_snapshot(run_id), error=error)
        self.dispatch_pending_runs()

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
        run_control = self._run_controls[run_id]
        timeout_seconds = self._resolve_run_timeout_seconds(run_id)
        timeout_deadline = (
            datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
            if timeout_seconds is not None
            else None
        )

        try:
            run_request(
                request,
                settings_loader=settings_loader,
                system_initializer=system_initializer,
                evolution_runner=evolution_runner,
                observer=event_bus,
                log_router=log_router,
                repo_import_service=self._repo_import_service,
                source_run_id=run_id,
                user_id=self._sessions[run_id].user_id,
                runtime_snapshot_publisher=(
                    lambda **payload: self.publish_runtime_snapshot(run_id, **payload)
                ),
                run_control=run_control,
                timeout_deadline=timeout_deadline,
            )
            self.mark_completed(run_id)
            if timeout_deadline is not None and datetime.now(timezone.utc) >= timeout_deadline:
                overrun = self._build_timeout_overrun_summary(self._sessions[run_id])
                logger.warning(
                    "运行 %s 已完成，但超出执行时间预算 %.3f 秒",
                    run_id,
                    overrun["timeoutOverrunSeconds"] or 0.0,
                )
        except Exception as exc:
            self.mark_failed(run_id, str(exc))
            return

    def _claim_next_pending_run_locked(self) -> str | None:
        for session in sorted(
            self._sessions.values(),
            key=lambda current: (current.created_at, current.run_id),
        ):
            if session.status != "pending":
                continue
            if session.run_id not in self._scheduler_specs:
                continue
            if session.cancel_requested:
                session.status = "cancelled"
                session.failed_at = _utc_now_iso()
                session.queue_position = None
                self._github_runtime_configs.pop(session.run_id, None)
                self._persist_session(session)
                continue
            if not self._can_start_session_locked(session):
                continue
            session.status = "starting"
            session.started_at = _utc_now_iso()
            session.queue_position = None
            self._active_run_id = session.run_id
            self._persist_session(session)
            self.publish_runtime_snapshot(session.run_id)
            return session.run_id
        return None

    def _can_start_session_locked(self, session: RunSession) -> bool:
        policy = self._queue_policy_for_session(session)
        running_sessions = [
            current for current in self._sessions.values() if current.status in RUNNING_STATUSES
        ]
        if len(running_sessions) >= policy.global_max_running_tasks:
            return False
        user_running = [
            current
            for current in running_sessions
            if current.user_id is not None and current.user_id == session.user_id
        ]
        return len(user_running) < policy.per_user_max_running_tasks

    def _queue_policy_for_session(self, session: RunSession):
        deployment_snapshot = session.config_snapshot.get("deployment", {})
        if not isinstance(deployment_snapshot, dict):
            return DeploymentPolicyConfig()
        return DeploymentPolicyConfig.model_validate(deployment_snapshot)

    def assert_queue_capacity(self, settings: Settings, *, user_id: int | None) -> None:
        with self._lock:
            self._assert_queue_capacity(settings, user_id=user_id)

    def _assert_queue_capacity(self, settings: Settings, *, user_id: int | None) -> None:
        if self._web_database is None:
            return
        policy = settings.deployment
        global_pending = self._web_database.count_run_records_by_statuses(PENDING_STATUSES)
        if global_pending >= policy.global_max_pending_tasks:
            raise QueueLimitExceededError("全局排队任务数量已达上限。")
        if user_id is not None:
            user_pending = self._web_database.count_run_records_by_statuses(
                PENDING_STATUSES,
                user_id=user_id,
            )
            if user_pending >= policy.per_user_max_pending_tasks:
                raise QueueLimitExceededError("当前用户排队任务数量已达上限。")

    def _refresh_queue_positions_locked(self) -> None:
        pending_sessions = sorted(
            (
                session
                for session in self._sessions.values()
                if session.status == "pending" and not session.cancel_requested
            ),
            key=lambda current: (current.created_at, current.run_id),
        )
        pending_ids = {session.run_id for session in pending_sessions}
        for position, session in enumerate(pending_sessions, start=1):
            if session.queue_position == position:
                continue
            session.queue_position = position
            if self._web_database is not None:
                self._web_database.update_run_queue_position(session.run_id, position)
        for session in self._sessions.values():
            if session.run_id in pending_ids or session.queue_position is None:
                continue
            session.queue_position = None
            if self._web_database is not None:
                self._web_database.update_run_queue_position(session.run_id, None)

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
            "pending": ("queued", "Queued"),
            "starting": ("starting", "Starting"),
            "running": ("running", "Running"),
            "cancelling": ("cancelling", "Cancelling"),
            "cancelled": ("cancelled", "Cancelled"),
            "stale": ("stale", "Stale"),
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
        self._apply_mutation_semantics(run_id, snapshot, state=state)
        snapshot["phase"] = self._build_phase(session)
        snapshot["artifacts"] = self._build_artifacts(session)
        snapshot["queuePosition"] = session.queue_position
        snapshot["cancelRequested"] = session.cancel_requested
        snapshot["cancellationReason"] = session.cancellation_reason
        return snapshot

    def _resolve_mutation_enabled(
        self, run_id: str, *, state: AgentState | None = None
    ) -> bool | None:
        if state is not None:
            state_value = getattr(state, "global_mutation_enabled", None)
            if isinstance(state_value, bool):
                return state_value

        request = self._requests.get(run_id)
        if request is not None and isinstance(request.mutation_enabled, bool):
            return request.mutation_enabled

        session = self._sessions.get(run_id)
        if session is None:
            return None

        config_value = session.config_snapshot.get("evolution", {}).get("mutation_enabled")
        if isinstance(config_value, bool):
            return config_value
        return None

    def _apply_mutation_semantics(
        self, run_id: str, payload: dict[str, Any], *, state: AgentState | None = None
    ) -> None:
        mutation_enabled = self._resolve_mutation_enabled(run_id, state=state)
        if mutation_enabled is None:
            payload_value = payload.get("mutationEnabled")
            mutation_enabled = payload_value if isinstance(payload_value, bool) else None
        payload["mutationEnabled"] = mutation_enabled

        metrics = payload.get("metrics")
        if isinstance(metrics, dict):
            payload["metrics"] = normalize_mutation_metrics(metrics, mutation_enabled)

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
            "reportArtifact": session.paths["report_artifact"],
        }
        artifacts: dict[str, dict[str, object]] = {}
        for name, path in paths.items():
            artifacts[name] = {"exists": Path(path).exists()}
        artifacts["finalState"] = {"exists": self._safe_artifact_exists(session, "final-state")}
        artifacts["log"] = {"exists": self._safe_artifact_exists(session, "run-log")}
        artifacts["reportArtifact"] = {"exists": self._safe_artifact_exists(session, "report")}
        artifacts["resolvedConfig"] = {
            "exists": self._safe_artifact_exists(session, "resolved-config")
        }
        artifacts["finalState"]["downloadUrl"] = f"/api/runs/{session.run_id}/artifacts/final-state"
        artifacts["log"]["downloadUrl"] = f"/api/runs/{session.run_id}/artifacts/run-log"
        artifacts["reportArtifact"]["downloadUrl"] = f"/api/runs/{session.run_id}/artifacts/report"
        artifacts["resolvedConfig"]["downloadUrl"] = (
            f"/api/runs/{session.run_id}/artifacts/resolved-config"
        )
        return artifacts

    def _build_download_artifact(
        self,
        run_id: str,
        *,
        artifact_slug: str,
        include_file_path: bool = False,
    ) -> dict[str, Any]:
        spec = SAFE_ARTIFACTS[artifact_slug]
        download_name = self._download_filename_for_artifact(run_id, artifact_slug)
        try:
            resolved_path = self._resolve_safe_artifact_path(run_id, artifact_slug)
        except KeyError:
            resolved_path = None
        exists = resolved_path is not None
        stat = resolved_path.stat() if resolved_path is not None else None
        artifact = {
            "exists": exists,
            "filename": download_name,
            "contentType": str(spec["content_type"]),
            "sizeBytes": stat.st_size if stat is not None else None,
            "updatedAt": (
                datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                if stat is not None
                else None
            ),
            "downloadUrl": f"/api/runs/{run_id}/artifacts/{artifact_slug}",
        }
        if include_file_path and resolved_path is not None:
            artifact["filePath"] = str(resolved_path)
        return artifact

    def _download_filename_for_artifact(self, run_id: str, artifact_slug: str) -> str:
        if artifact_slug == "final-tests.zip":
            return f"comet-run-{run_id}-generated-tests.zip"
        return str(SAFE_ARTIFACTS[artifact_slug]["filename"])

    def _build_final_tests_archive_artifact(self, run_id: str) -> dict[str, Any] | None:
        if not self._ensure_final_tests_archive(run_id):
            return None
        artifact = self._build_download_artifact(run_id, artifact_slug="final-tests.zip")
        return artifact if artifact["exists"] else None

    def _ensure_final_tests_archive(self, run_id: str) -> bool:
        session = self._sessions[run_id]
        archive_entries = self._collect_final_tests_archive_entries(Path(session.paths["database"]))
        archive_path = Path(session.paths["output"]) / str(
            SAFE_ARTIFACTS["final-tests.zip"]["filename"]
        )
        if not archive_entries:
            try:
                if archive_path.exists() or archive_path.is_symlink():
                    archive_path.unlink()
            except OSError:
                logger.warning("删除空最终测试包失败 %s", archive_path)
            return False

        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, full_code in archive_entries.items():
                archive.writestr(name, full_code)
        return True

    def _collect_final_tests_archive_entries(self, database_path: Path) -> dict[str, str]:
        if not database_path.exists() or database_path.stat().st_size == 0:
            return {}

        try:
            connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            logger.warning("打开最终测试包数据库失败 %s: %s", database_path, exc)
            return {}

        try:
            cursor = connection.cursor()
            if not self._has_table(cursor, "test_cases"):
                return {}
            rows = cursor.execute(
                """
                SELECT package_name, class_name, full_code
                FROM test_cases
                WHERE compile_success = 1
                  AND full_code IS NOT NULL
                  AND TRIM(full_code) != ''
                ORDER BY class_name ASC, package_name ASC, id ASC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("读取最终测试包数据库失败 %s: %s", database_path, exc)
            return {}
        finally:
            connection.close()

        entries: dict[str, str] = {}
        for row in rows:
            class_name = str(row["class_name"] or "").strip()
            full_code = str(row["full_code"] or "")
            archive_name = self._final_test_archive_name(
                package_name=str(row["package_name"] or "").strip(),
                class_name=class_name,
            )
            if archive_name is None or archive_name in entries:
                continue
            entries[archive_name] = full_code
        return entries

    def _final_test_archive_name(self, *, package_name: str, class_name: str) -> str | None:
        if not class_name:
            return None
        package_path = package_name.replace(".", "/")
        relative_path = f"src/test/java/{class_name}.java"
        if package_path:
            relative_path = f"src/test/java/{package_path}/{class_name}.java"
        posix_path = PurePosixPath(relative_path)
        if posix_path.is_absolute():
            return None
        if any(part in {"", ".", ".."} for part in posix_path.parts):
            return None
        normalized = posix_path.as_posix()
        if not normalized.startswith("src/test/java/") or not normalized.endswith(".java"):
            return None
        return normalized

    def _resolve_safe_artifact_path(self, run_id: str, artifact_slug: str) -> Path:
        session = self._sessions[run_id]
        spec = SAFE_ARTIFACTS[artifact_slug]
        filename = str(spec["filename"])
        root_name = str(spec["root"])
        if root_name == "log":
            artifact_path = Path(session.path_snapshot.get("log") or session.paths["log"])
            if artifact_path.name != filename:
                raise KeyError(artifact_slug)
            root = Path(session.paths["log"]).parent
            return self._resolve_existing_safe_artifact_file(
                artifact_path,
                root=root,
                artifact_slug=artifact_slug,
            )

        root_value = session.path_snapshot.get(root_name) or session.paths[root_name]
        root = Path(root_value)
        artifact_path = root / filename
        canonical_root = Path(session.paths[root_name])
        return self._resolve_existing_safe_artifact_file(
            artifact_path,
            root=canonical_root,
            artifact_slug=artifact_slug,
        )

    def _resolve_existing_safe_artifact_file(
        self,
        artifact_path: Path,
        *,
        root: Path,
        artifact_slug: str,
    ) -> Path:
        try:
            resolved_root = root.expanduser().resolve(strict=True)
            if artifact_path.is_symlink():
                raise KeyError(artifact_slug)
            resolved_path = artifact_path.expanduser().resolve(strict=True)
            if not resolved_path.is_relative_to(resolved_root):
                raise KeyError(artifact_slug)
            if not resolved_path.is_file():
                raise KeyError(artifact_slug)
        except OSError as exc:
            raise KeyError(artifact_slug) from exc
        return resolved_path

    def _safe_artifact_exists(self, session: RunSession, artifact_slug: str) -> bool:
        try:
            return self._resolve_safe_artifact_path(session.run_id, artifact_slug).exists()
        except KeyError:
            return False

    def _generate_report_artifact(self, run_id: str, *, completed_at: str) -> None:
        session = self._sessions[run_id]
        request = self._requests[run_id]
        results = self._build_results_payload(run_id, self._build_runtime_snapshot(run_id))
        summary = results["summary"]
        project_path = Path(request.project_path).expanduser()
        git_branch, git_commit = resolve_git_metadata(project_path)

        try:
            report_content = build_run_report(
                run_id=run_id,
                mode=str(results["mode"]),
                project_path=str(project_path),
                repo_url=_normalize_optional_text(
                    session.config_snapshot.get("github", {}).get("repo_url")
                ),
                base_branch=_normalize_optional_text(
                    session.config_snapshot.get("github", {}).get("base_branch")
                ),
                java_version=self._resolve_selected_java_version(run_id),
                started_at=session.started_at,
                completed_at=completed_at,
                metrics=summary["metrics"],
                mutation_enabled=results.get("mutationEnabled"),
                sources=summary["sources"],
                tests_summary=summary["tests"],
                mutants_summary=summary["mutants"],
                test_files=collect_generated_test_files(Path(session.paths["database"])),
                git_branch=git_branch,
                git_commit=git_commit,
            )
            report_path = Path(session.paths["report_artifact"])
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_content, encoding="utf-8")
        except Exception as exc:
            logger.exception("生成运行报告失败 %s", run_id)
            raise ReportGenerationError("生成运行报告失败。") from exc

    def _publish_generated_tests_pull_request(self, run_id: str) -> str | None:
        session = self._sessions[run_id]
        request = self._requests[run_id]

        if session.project_source_type == "upload":
            return None

        github_snapshot = session.config_snapshot.get("github", {})
        if not isinstance(github_snapshot, dict):
            raise GitPullRequestError("运行配置中的 GitHub 信息无效，无法自动创建 PR。")

        repo_url = _normalize_optional_text(github_snapshot.get("repo_url"))
        if repo_url is None:
            return None

        base_branch = _normalize_optional_text(github_snapshot.get("base_branch"))
        if base_branch is None:
            raise GitPullRequestError("缺少基线分支信息，无法自动创建 PR。")

        if self._pull_request_service is None:
            raise GitPullRequestError("GitHub PR 服务未初始化，无法自动提交并创建 PR。")

        github_config = self._github_runtime_configs.get(run_id)
        if github_config is None:
            try:
                github_config = GitHubConfig.model_validate(github_snapshot)
            except Exception as exc:  # pragma: no cover - 防御分支
                raise GitPullRequestError("GitHub 配置无效，无法自动创建 PR。") from exc

        result = self._pull_request_service.commit_generated_tests_and_create_pr(
            run_id=run_id,
            project_path=Path(request.project_path),
            report_path=Path(session.paths["report_artifact"]),
            repo_url=repo_url,
            base_branch=base_branch,
            github_config=github_config,
            user_key=_github_user_key(session.user_id),
        )
        return result.pull_request_url

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

    def _persist_session(self, session: RunSession) -> None:
        if self._web_database is None:
            return
        if session.status == "cancelled":
            finished_at = session.failed_at or session.completed_at or session.finished_at
        else:
            finished_at = session.finished_at
        path_metadata = {
            "paths": session.paths,
            "pathSnapshot": session.path_snapshot,
            "sourceMetadata": session.source_metadata,
        }
        upload_source = session.source_metadata.get("uploadSource")
        if isinstance(upload_source, dict):
            path_metadata["uploadSource"] = copy.deepcopy(upload_source)
        existing = self._web_database.get_run_record(session.run_id)
        if existing is None:
            self._web_database.create_run_record(
                run_id=session.run_id,
                user_id=session.user_id,
                status=session.status,
                created_at=session.created_at,
                started_at=session.started_at,
                finished_at=finished_at,
                project_source_type=session.project_source_type,
                project_path=session.project_path,
                bug_reports_path=session.bug_reports_path,
                path_metadata=path_metadata,
                paths=session.paths,
                path_snapshot=session.path_snapshot,
                config_snapshot=session.config_snapshot,
                config_path=_normalize_config_path(session.config_path),
                error=session.error,
                queue_position=session.queue_position,
                cancel_requested=session.cancel_requested,
                cancellation_reason=session.cancellation_reason,
            )
            return

        self._web_database.update_run_record(
            session.run_id,
            status=session.status,
            started_at=session.started_at,
            finished_at=finished_at,
            path_metadata=path_metadata,
            paths=session.paths,
            path_snapshot=session.path_snapshot,
            config_snapshot=session.config_snapshot,
            config_path=_normalize_config_path(session.config_path),
            error=session.error,
            queue_position=session.queue_position,
            cancel_requested=session.cancel_requested,
            cancellation_reason=session.cancellation_reason,
        )

    def _load_persisted_sessions(self) -> None:
        if self._web_database is None:
            return
        self._web_database.mark_stale_run_records(
            BOOT_STALE_STATUSES,
            error="运行在 Web 服务重启后无法恢复，已标记为 stale。",
        )
        self._web_database.mark_stale_run_records(
            PENDING_STATUSES,
            error="排队运行在 Web 服务重启后缺少调度上下文，已标记为 stale。",
        )
        self._sessions.clear()
        self._requests.clear()
        self._event_buses.clear()
        self._log_routers.clear()
        self._runtime_snapshots.clear()
        self._github_runtime_configs.clear()
        self._active_run_id = None

        for record in self._web_database.list_run_records(include_all=True):
            try:
                session = self._session_from_record(record)
                self._validate_session_paths(session)
            except (TypeError, ValueError) as exc:
                logger.warning("跳过损坏的 SQLite 运行记录 %s: %s", record.id, exc)
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

            self._persist_session(session)
        with self._lock:
            self._refresh_queue_positions_locked()

    def _session_from_record(self, record: RunRecord) -> RunSession:
        failed_at = record.finished_at if record.status == "failed" else None
        completed_at = record.finished_at if record.status == "completed" else None
        if record.status in {"cancelled", "stale"}:
            failed_at = record.finished_at
        return RunSession(
            run_id=record.id,
            user_id=record.user_id,
            status=record.status,
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=completed_at,
            failed_at=failed_at,
            project_path=record.project_path,
            config_path=record.config_path,
            paths=record.paths,
            path_snapshot=record.path_snapshot,
            config_snapshot=record.config_snapshot,
            project_source_type=record.project_source_type,
            bug_reports_path=record.bug_reports_path,
            error=record.error,
            queue_position=record.queue_position,
            cancel_requested=record.cancel_requested,
            cancellation_reason=record.cancellation_reason,
            source_metadata=_extract_source_metadata(record.path_metadata),
        )

    def _validate_session_paths(self, session: RunSession) -> None:
        required_paths = {
            "state",
            "output",
            "sandbox",
            "log",
            "database",
            "resolved_config",
            "final_state",
            "interrupted_state",
            "report_artifact",
        }
        missing_paths = required_paths - set(session.paths)
        if missing_paths:
            raise ValueError(f"missing paths: {sorted(missing_paths)}")

        allowed_roots = (
            self.workspace_root / "state" / "users",
            self.workspace_root / "output" / "users",
            self.workspace_root / "sandbox" / "users",
            self.workspace_root / "logs" / "users",
        )
        for path_value in session.paths.values():
            path = Path(path_value).expanduser()
            if not self._path_string_is_relative_to_allowed_root(path, allowed_roots):
                raise ValueError(f"path escapes user-scoped roots: {path}")

    def _path_string_is_relative_to_allowed_root(
        self,
        path: Path,
        allowed_roots: tuple[Path, ...],
    ) -> bool:
        absolute_path = path if path.is_absolute() else (self.workspace_root / path)
        normalized_parts: list[str] = []
        for part in absolute_path.parts:
            if part in {"", "."}:
                continue
            if part == "..":
                if normalized_parts:
                    normalized_parts.pop()
                continue
            normalized_parts.append(part)
        normalized_path = Path(*normalized_parts)
        if absolute_path.is_absolute():
            normalized_path = Path(absolute_path.anchor, *normalized_parts[1:])
        return any(normalized_path.is_relative_to(root.resolve()) for root in allowed_roots)

    def _normalize_restored_session(self, session: RunSession) -> RunSession:
        if session.status == "created":
            session.status = "pending"
            return session
        if session.status not in BOOT_STALE_STATUSES:
            return session

        session.status = "stale"
        session.failed_at = session.failed_at or _utc_now_iso()
        session.error = session.error or "运行在 Web 服务重启后无法恢复，已标记为 stale。"
        return session

    def _finish_cancelled_run_locked(
        self,
        session: RunSession,
        *,
        completed_at: str | None = None,
    ) -> None:
        session.status = "cancelled"
        session.completed_at = None
        session.failed_at = completed_at or _utc_now_iso()
        session.queue_position = None
        session.error = None
        self._log_routers[session.run_id].ensure_stream(
            "main",
            status="cancelled",
            started_at=session.started_at or session.created_at,
            ended_at=session.failed_at,
            completed_at=None,
        )
        if self._active_run_id == session.run_id:
            self._active_run_id = None
        self._github_runtime_configs.pop(session.run_id, None)
        self._persist_session(session)
        self._scheduler_specs.pop(session.run_id, None)
        self._run_controls.pop(session.run_id, None)

    def _resolve_run_timeout_seconds(self, run_id: str) -> int | None:
        session = self._sessions.get(run_id)
        if session is None:
            return None
        timeout = session.config_snapshot.get("execution", {}).get("timeout")
        if isinstance(timeout, int) and timeout > 0:
            return timeout
        return None

    def cleanup_workspace(
        self,
        *,
        now: datetime | None = None,
        upload_retention_hours: int = 24,
        run_artifact_retention_days: int = 30,
    ) -> dict[str, list[str]]:
        current_time = now or datetime.now(timezone.utc)
        report: dict[str, list[str]] = {"uploads": [], "artifacts": []}
        if self._web_database is None:
            return report

        upload_cutoff = current_time - timedelta(hours=upload_retention_hours)
        for upload in self._web_database.list_upload_records(include_all=True):
            if upload.used_at is not None:
                continue
            if datetime.fromisoformat(upload.created_at) > upload_cutoff:
                continue
            upload_root = Path(upload.storage_path).expanduser().resolve(strict=False).parent.parent
            self._safe_remove_path(upload_root, self.workspace_root / "sandbox" / "users")
            _ = self._web_database.delete_upload_record(upload.id)
            report["uploads"].append(upload.id)

        artifact_cutoff = current_time - timedelta(days=run_artifact_retention_days)
        for session in self._web_database.list_run_records(include_all=True):
            if session.status not in TERMINAL_STATUSES:
                continue
            finished_at = session.finished_at or session.updated_at or session.created_at
            if datetime.fromisoformat(finished_at) > artifact_cutoff:
                continue
            self._safe_remove_path(
                Path(session.paths["state"]), self.workspace_root / "state" / "users"
            )
            self._safe_remove_path(
                Path(session.paths["output"]), self.workspace_root / "output" / "users"
            )
            self._safe_remove_path(
                Path(session.paths["sandbox"]), self.workspace_root / "sandbox" / "users"
            )
            self._safe_remove_path(
                Path(session.paths["log"]).parent, self.workspace_root / "logs" / "users"
            )
            report["artifacts"].append(session.id)

        return report

    def _safe_remove_path(self, target: Path, allowed_root: Path) -> bool:
        try:
            resolved_root = allowed_root.expanduser().resolve(strict=True)
            resolved_target = target.expanduser().resolve(strict=False)
            if not resolved_target.is_relative_to(resolved_root):
                return False
            if any(
                Path(session.paths[root_key])
                .expanduser()
                .resolve(strict=False)
                .is_relative_to(resolved_target)
                for session in self._sessions.values()
                if session.status in {"pending", "starting", "running", "cancelling"}
                for root_key in ("state", "output", "sandbox")
            ):
                return False
            if resolved_target.is_dir():
                shutil.rmtree(resolved_target)
            elif resolved_target.exists():
                resolved_target.unlink()
            return True
        except OSError:
            return False

    def cleanup_user_root(self, *, now: datetime | None = None) -> dict[str, list[str]]:
        return self.cleanup_workspace(now=now)

    def _build_restored_request(self, session: RunSession) -> RunRequest:
        parallel_config = session.config_snapshot.get("agent", {}).get("parallel", {})
        parallel_enabled = bool(parallel_config.get("enabled", False))
        parallel_targets = parallel_config.get("max_parallel_targets")
        mutation_enabled = session.config_snapshot.get("evolution", {}).get("mutation_enabled")
        return RunRequest(
            project_path=session.project_path,
            config_path=session.config_path,
            mutation_enabled=mutation_enabled if isinstance(mutation_enabled, bool) else None,
            parallel=parallel_enabled,
            parallel_targets=parallel_targets if isinstance(parallel_targets, int) else None,
            github_repo_url=session.config_snapshot.get("github", {}).get("repo_url"),
            github_base_branch=session.config_snapshot.get("github", {}).get("base_branch"),
            selected_java_version=session.config_snapshot.get("execution", {}).get(
                "selected_java_version"
            ),
            log_file=session.paths.get("log"),
            runtime_roots={
                "state": session.paths["state"],
                "output": session.paths["output"],
                "sandbox": session.paths["sandbox"],
            },
            source_metadata=copy.deepcopy(session.source_metadata),
        )

    def _session_to_stream_status(self, status: str) -> str:
        if status in {"created", "pending", "starting"}:
            return "pending"
        if status in {"completed", "failed", "running", "cancelled", "stale"}:
            return status
        if status == "cancelling":
            return "running"
        return "pending"

    def _resolve_selected_java_version(self, run_id: str) -> str | None:
        request = self._requests.get(run_id)
        request_value = (
            getattr(request, "selected_java_version", None) if request is not None else None
        )
        if isinstance(request_value, str):
            normalized = request_value.strip()
            if normalized:
                return normalized

        session = self._sessions.get(run_id)
        if session is None:
            return None

        config_value = session.config_snapshot.get("execution", {}).get("selected_java_version")
        if isinstance(config_value, str):
            normalized = config_value.strip()
            if normalized:
                return normalized
        return None

    def _build_scoped_paths(self, run_id: str, *, user_id: int | None = None) -> dict[str, str]:
        user_segment = str(user_id) if user_id is not None else "anonymous"
        state_root = self.workspace_root / "state" / "users" / user_segment / "runs" / run_id
        output_root = self.workspace_root / "output" / "users" / user_segment / "runs" / run_id
        sandbox_root = self.workspace_root / "sandbox" / "users" / user_segment / "runs" / run_id
        log_file = (
            self.workspace_root / "logs" / "users" / user_segment / "runs" / run_id / "run.log"
        )

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
            "report_artifact": str(output_root / "report.md"),
        }

    def _build_scoped_request(
        self, request: RunRequest, scoped_paths: dict[str, str]
    ) -> RunRequest:
        return RunRequest(
            project_path=request.project_path,
            config_path=request.config_path,
            max_iterations=request.max_iterations,
            budget=request.budget,
            mutation_enabled=request.mutation_enabled,
            resume_state=request.resume_state,
            debug=request.debug,
            bug_reports_dir=request.bug_reports_dir,
            parallel=request.parallel,
            parallel_targets=request.parallel_targets,
            github_repo_url=request.github_repo_url,
            github_base_branch=request.github_base_branch,
            selected_java_version=request.selected_java_version,
            log_file=scoped_paths["log"],
            runtime_roots={
                "state": scoped_paths["state"],
                "output": scoped_paths["output"],
                "sandbox": scoped_paths["sandbox"],
            },
            observer=request.observer,
            source_metadata=copy.deepcopy(request.source_metadata),
        )

    def _ensure_scoped_directories(self, scoped_paths: dict[str, str]) -> None:
        for key in ["state", "output", "sandbox"]:
            Path(scoped_paths[key]).mkdir(parents=True, exist_ok=True)
        Path(scoped_paths["log"]).parent.mkdir(parents=True, exist_ok=True)

    def _write_config_snapshot(
        self,
        config_snapshot: dict[str, Any],
        config_snapshot_path: str,
    ) -> None:
        snapshot_path = Path(config_snapshot_path)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(
            json.dumps(config_snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _github_user_key(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return f"web-user:{user_id}"


def _normalize_config_path(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _extract_source_metadata(path_metadata: dict[str, Any]) -> dict[str, Any]:
    source_metadata = path_metadata.get("sourceMetadata")
    if isinstance(source_metadata, dict):
        return copy.deepcopy(source_metadata)
    upload_source = path_metadata.get("uploadSource")
    if isinstance(upload_source, dict):
        return {"uploadSource": copy.deepcopy(upload_source)}
    return {}


def _normalize_project_source_type(source_metadata: dict[str, Any]) -> str:
    upload_source = source_metadata.get("uploadSource")
    if isinstance(upload_source, dict) and upload_source.get("mode") == "upload":
        return "upload"
    return "local"


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
        github_repo_url=getattr(args, "github_repo_url", None),
        github_base_branch=getattr(args, "github_base_branch", None),
        selected_java_version=getattr(args, "selected_java_version", None),
    )


def apply_run_overrides(config: Settings, request: RunRequest) -> None:
    if request.max_iterations is not None:
        config.evolution.max_iterations = request.max_iterations
    if request.budget is not None:
        config.evolution.budget_llm_calls = request.budget
    if request.mutation_enabled is not None:
        config.evolution.mutation_enabled = request.mutation_enabled
    if request.parallel or request.parallel_targets is not None:
        config.agent.parallel.enabled = True
    if request.parallel_targets is not None:
        config.agent.parallel.max_parallel_targets = request.parallel_targets
    if request.selected_java_version is not None:
        config.execution.selected_java_version = request.selected_java_version
    if request.github_repo_url is not None:
        config.github.repo_url = request.github_repo_url
    if request.github_base_branch is not None:
        config.github.base_branch = request.github_base_branch

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


def _is_within_directory(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _clear_project_test_directories(project_path: str, logger: logging.Logger) -> None:
    project_root = Path(project_path).resolve()
    for relative in [Path("src/test/java"), Path("src/test/resources")]:
        target = (project_root / relative).resolve()
        if not _is_within_directory(target, project_root):
            raise RuntimeError(f"测试目录清理路径越界: {target}")
        if not target.exists():
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            logger.info(f"已清理旧测试目录: {target}")
        except OSError as exc:
            raise RuntimeError(f"清理旧测试目录失败: {target}") from exc


def _resolve_project_source(
    request: RunRequest,
    config: Settings,
    logger: logging.Logger,
    repo_import_service: GitHubRepoImportService | None,
    source_run_id: str | None,
    user_id: int | None = None,
) -> str:
    managed_root = Path(config.github.managed_clone_root).expanduser().resolve()

    if request.github_repo_url is not None:
        current_project_path = Path(request.project_path).expanduser().resolve()
        imported_within_managed_root = _is_within_directory(current_project_path, managed_root)
        if not imported_within_managed_root:
            if repo_import_service is None:
                raise GitCloneError("仓库导入服务未初始化，无法处理 GitHub 仓库请求。")
            try:
                imported = repo_import_service.import_repository(
                    run_id=source_run_id or "adhoc-run",
                    github_repo_url=request.github_repo_url,
                    github_config=config.github,
                    requested_base_branch=request.github_base_branch,
                    user_key=_github_user_key(user_id),
                )
            except RepoImportUrlError as exc:
                raise InvalidGitHubRepoUrlError(str(exc)) from exc
            except RepoImportPermissionError as exc:
                raise GitHubUnauthorizedError(str(exc)) from exc
            except RepoImportBranchResolutionError as exc:
                raise GitDefaultBranchResolutionError(str(exc)) from exc
            except RepoImportCloneError as exc:
                raise GitCloneError(str(exc)) from exc
            except RepoImportNonMavenError as exc:
                raise NonMavenRepositoryError(str(exc)) from exc

            request.project_path = imported.project_path
            request.github_base_branch = imported.base_branch
            config.github.base_branch = imported.base_branch
            config.github.repo_url = request.github_repo_url
            logger.info(f"已在运行启动阶段导入 GitHub 仓库: {request.project_path}")

    resolved_project_path = _validate_project_path(request.project_path, logger)
    _clear_project_test_directories(resolved_project_path, logger)
    return resolved_project_path


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
    repo_import_service: GitHubRepoImportService | None = None,
    source_run_id: str | None = None,
    user_id: int | None = None,
    run_control: threading.Event | None = None,
    timeout_deadline: datetime | None = None,
) -> int:
    runtime_logger = logger or logging.getLogger(__name__)
    event_sink = observer or request.observer

    config = settings_loader(request.config_path)
    apply_runtime_roots(config, request.runtime_roots)
    apply_run_overrides(config, request)
    enforce_deployment_policy(config)

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

    project_path = _resolve_project_source(
        request,
        config,
        runtime_logger,
        repo_import_service,
        source_run_id,
        user_id=user_id,
    )
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
        if isinstance(components, dict):
            components["run_control"] = run_control
            components["timeout_deadline"] = timeout_deadline
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
