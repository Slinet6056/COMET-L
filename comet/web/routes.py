from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import yaml
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

from comet.config import Settings

from .run_service import ActiveRunConflictError, RunLifecycleService, RunRequest
from .schemas import (
    ApiError,
    ConfigParseResponse,
    ConfigPayload,
    ErrorResponse,
    FieldError,
    HealthResponse,
    RunCreateResponse,
    RunHistoryEntry,
    RunHistoryResponse,
    RunResultsResponse,
    RunSnapshotResponse,
)


@dataclass(slots=True)
class AppServices:
    run_service: RunLifecycleService
    default_config_path: Path
    system_initializer: Callable[..., dict[str, Any]] | None = None
    evolution_runner: Callable[..., None] | None = None


router = APIRouter(prefix="/api")
TERMINAL_RUN_STATUSES = {"completed", "failed"}


def get_app_services(request: Request) -> AppServices:
    return request.app.state.services


def get_run_service(
    services: AppServices = Depends(get_app_services),
) -> RunLifecycleService:
    return services.run_service


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
    field_errors: list[FieldError],
) -> JSONResponse:
    payload = ErrorResponse(error=ApiError(code=code, message=message, fieldErrors=field_errors))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _validation_errors(exc: ValidationError) -> list[FieldError]:
    return [
        FieldError(
            path=list(error.get("loc", ())),
            code=str(error.get("type", "validation_error")),
            message=str(error.get("msg", "Invalid value")),
        )
        for error in exc.errors()
    ]


def _load_settings_from_yaml(content: str) -> Settings:
    config_data = yaml.safe_load(content)
    if config_data is None:
        config_data = {}
    return Settings.model_validate(config_data)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _load_default_settings(default_config_path: Path) -> Settings:
    return Settings.from_yaml(str(default_config_path))


def _config_error_response(exc: ValidationError) -> JSONResponse:
    return _error_response(
        422,
        code="invalid_config",
        message="Configuration validation failed.",
        field_errors=_validation_errors(exc),
    )


def _build_sse_snapshot_event(run_id: str, run_service: RunLifecycleService) -> dict[str, Any]:
    snapshot = run_service.build_snapshot(run_id)
    return {
        "sequence": 0,
        "type": "run.snapshot",
        "runId": run_id,
        "status": snapshot["status"],
        "mode": snapshot["mode"],
        "snapshot": snapshot,
    }


def _encode_sse_event(event: dict[str, Any]) -> str:
    return "".join(
        [
            f"id: {event.get('sequence', 0)}\n",
            f"event: {event['type']}\n",
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n",
        ]
    )


def _run_not_found_response(run_id: str) -> JSONResponse:
    return _error_response(
        404,
        code="run_not_found",
        message="Run does not exist.",
        field_errors=[FieldError(path=["runId"], code="unknown_run", message=run_id)],
    )


def _log_stream_not_found_response(run_id: str, task_id: str) -> JSONResponse:
    return _error_response(
        404,
        code="log_stream_not_found",
        message="Log stream does not exist.",
        field_errors=[
            FieldError(path=["runId"], code="known_run", message=run_id),
            FieldError(path=["taskId"], code="unknown_task", message=task_id),
        ],
    )


def _artifact_not_found_response(run_id: str, artifact_name: str) -> JSONResponse:
    return _error_response(
        404,
        code="artifact_not_found",
        message="Artifact does not exist.",
        field_errors=[
            FieldError(path=["runId"], code="known_run", message=run_id),
            FieldError(path=["artifact"], code="missing_artifact", message=artifact_name),
        ],
    )


def _resolve_runtime_hooks(
    services: AppServices,
) -> tuple[Callable[..., dict[str, Any]], Callable[..., None]]:
    if services.system_initializer is not None and services.evolution_runner is not None:
        return services.system_initializer, services.evolution_runner

    import main

    return main.initialize_system, main.run_evolution


def _validate_project_path(project_path: str) -> list[FieldError]:
    resolved_path = Path(project_path).expanduser()
    if not resolved_path.exists():
        return [
            FieldError(
                path=["projectPath"],
                code="path_not_found",
                message="Project path does not exist.",
            )
        ]
    if not resolved_path.is_dir() or not (resolved_path / "pom.xml").is_file():
        return [
            FieldError(
                path=["projectPath"],
                code="not_maven_project",
                message="Project path must point to a Maven project containing pom.xml.",
            )
        ]
    return []


async def _load_merged_settings(
    services: AppServices,
    config_file: UploadFile | None,
) -> tuple[Settings, str] | JSONResponse:
    base_settings = _load_default_settings(services.default_config_path).to_dict()
    config_label = str(services.default_config_path)

    if config_file is None:
        return Settings.model_validate(base_settings), config_label

    try:
        content = (await config_file.read()).decode("utf-8")
    except UnicodeDecodeError:
        return _error_response(
            422,
            code="invalid_config_encoding",
            message="Configuration file must be UTF-8 encoded.",
            field_errors=[
                FieldError(
                    path=["configFile"],
                    code="unicode_decode_error",
                    message="Configuration file is not valid UTF-8.",
                )
            ],
        )

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        path: list[str | int] = ["configFile"]
        if mark is not None:
            path = ["configFile", "line", mark.line + 1, "column", mark.column + 1]
        return _error_response(
            422,
            code="invalid_yaml",
            message="Configuration file contains invalid YAML.",
            field_errors=[
                FieldError(
                    path=path,
                    code="yaml_syntax_error",
                    message=str(exc),
                )
            ],
        )

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return _error_response(
            422,
            code="invalid_yaml",
            message="Configuration file root must be a mapping.",
            field_errors=[
                FieldError(
                    path=["configFile"],
                    code="yaml_root_type",
                    message="Configuration file root must be a YAML mapping.",
                )
            ],
        )

    try:
        merged_settings = Settings.model_validate(_deep_merge(base_settings, parsed))
    except ValidationError as exc:
        return _config_error_response(exc)

    return merged_settings, (config_file.filename or "uploaded-config.yaml")


@router.get("/health", response_model=HealthResponse)
def get_health(
    run_service: RunLifecycleService = Depends(get_run_service),
) -> HealthResponse:
    return HealthResponse(status="ok", activeRunId=run_service.active_run_id())


@router.get("/config/defaults", response_model=ConfigPayload)
def get_config_defaults(
    services: AppServices = Depends(get_app_services),
) -> ConfigPayload:
    settings = Settings.from_yaml(str(services.default_config_path))
    return ConfigPayload(config=settings.to_dict())


@router.post(
    "/config/parse",
    response_model=ConfigParseResponse,
    responses={422: {"model": ErrorResponse}},
)
async def parse_config(
    file: UploadFile = File(...),
    _: RunLifecycleService = Depends(get_run_service),
) -> ConfigParseResponse | JSONResponse:
    try:
        content = (await file.read()).decode("utf-8")
    except UnicodeDecodeError:
        return _error_response(
            422,
            code="invalid_config_encoding",
            message="Configuration file must be UTF-8 encoded.",
            field_errors=[
                FieldError(
                    path=[],
                    code="unicode_decode_error",
                    message="Configuration file is not valid UTF-8.",
                )
            ],
        )

    try:
        settings = _load_settings_from_yaml(content)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        path: list[str | int] = []
        if mark is not None:
            path = ["line", mark.line + 1, "column", mark.column + 1]
        return _error_response(
            422,
            code="invalid_yaml",
            message="Configuration file contains invalid YAML.",
            field_errors=[
                FieldError(
                    path=path,
                    code="yaml_syntax_error",
                    message=str(exc),
                )
            ],
        )
    except ValidationError as exc:
        return _config_error_response(exc)

    return ConfigParseResponse(config=settings.to_dict())


@router.post(
    "/runs",
    response_model=RunCreateResponse,
    status_code=201,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def create_run(
    request: Request,
    projectPath: str = Form(...),
    configFile: UploadFile | None = File(default=None),
    maxIterations: int | None = Form(default=None),
    budget: int | None = Form(default=None),
    mutationEnabled: bool | None = Form(default=None),
    resumeState: str | None = Form(default=None),
    debug: bool = Form(default=False),
    bugReportsDir: str | None = Form(default=None),
    parallel: bool = Form(default=False),
    parallelTargets: int | None = Form(default=None),
    services: AppServices = Depends(get_app_services),
    run_service: RunLifecycleService = Depends(get_run_service),
) -> RunCreateResponse | JSONResponse:
    del request
    field_errors = _validate_project_path(projectPath)
    if field_errors:
        return _error_response(
            422,
            code="invalid_project_path",
            message="Project path validation failed.",
            field_errors=field_errors,
        )

    merged = await _load_merged_settings(services, configFile)
    if isinstance(merged, JSONResponse):
        return merged
    settings, config_label = merged

    try:
        run_session = run_service.create_run(
            RunRequest(
                project_path=str(Path(projectPath).expanduser().resolve()),
                config_path=config_label,
                max_iterations=maxIterations,
                budget=budget,
                mutation_enabled=mutationEnabled,
                resume_state=resumeState,
                debug=debug,
                bug_reports_dir=bugReportsDir,
                parallel=parallel,
                parallel_targets=parallelTargets,
            ),
            settings_loader=lambda _config_path: settings,
        )
    except ActiveRunConflictError as exc:
        return _error_response(
            409,
            code="active_run_conflict",
            message="Another run is already active.",
            field_errors=[FieldError(path=[], code="active_run_exists", message=str(exc))],
        )

    created_response = RunCreateResponse(
        runId=run_session.run_id,
        status=run_session.status,
        mode=run_service.run_mode(run_session.run_id),
    )
    system_initializer, evolution_runner = _resolve_runtime_hooks(services)
    run_service.start_run(
        run_session.run_id,
        settings_loader=lambda _config_path: settings,
        system_initializer=system_initializer,
        evolution_runner=evolution_runner,
    )
    return created_response


@router.get(
    "/runs/history",
    response_model=RunHistoryResponse,
)
def get_run_history(
    run_service: RunLifecycleService = Depends(get_run_service),
) -> RunHistoryResponse:
    return RunHistoryResponse(
        items=[RunHistoryEntry.model_validate(item) for item in run_service.list_runs()]
    )


@router.get(
    "/runs/current",
    response_model=RunSnapshotResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_current_run(
    run_service: RunLifecycleService = Depends(get_run_service),
) -> RunSnapshotResponse | JSONResponse:
    run_id = run_service.active_run_id()
    if run_id is None:
        return _error_response(
            404,
            code="no_active_run",
            message="No active run exists.",
            field_errors=[],
        )
    return RunSnapshotResponse.model_validate(run_service.build_snapshot(run_id))


@router.get(
    "/runs/{run_id}",
    response_model=RunSnapshotResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_run_snapshot(
    run_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
) -> RunSnapshotResponse | JSONResponse:
    try:
        snapshot = run_service.build_snapshot(run_id)
    except KeyError:
        return _error_response(
            404,
            code="run_not_found",
            message="Run does not exist.",
            field_errors=[FieldError(path=["runId"], code="unknown_run", message=run_id)],
        )
    return RunSnapshotResponse.model_validate(snapshot)


@router.get("/runs/{run_id}/events", response_model=None)
async def get_run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    run_service: RunLifecycleService = Depends(get_run_service),
) -> StreamingResponse | JSONResponse:
    try:
        event_bus = run_service.get_event_bus(run_id)
        run_service.get_session(run_id)
    except KeyError:
        return _run_not_found_response(run_id)

    async def stream_events() -> AsyncIterator[str]:
        snapshot_event = _build_sse_snapshot_event(run_id, run_service)
        yield _encode_sse_event(snapshot_event)

        last_sequence = after
        terminal_seen = False

        while True:
            events = event_bus.list_events(after_sequence=last_sequence)
            for event in events:
                yield _encode_sse_event(event)
                last_sequence = int(event["sequence"])
                if str(event["type"]) in {"run.completed", "run.failed"}:
                    terminal_seen = True

            if terminal_seen:
                break

            if snapshot_event["status"] in TERMINAL_RUN_STATUSES and not events:
                break

            if await request.is_disconnected():
                break

            await asyncio.sleep(0.05)
            snapshot_event = _build_sse_snapshot_event(run_id, run_service)

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/runs/{run_id}/logs", response_model=None)
def get_run_logs(
    run_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
) -> JSONResponse:
    try:
        run_service.get_session(run_id)
    except KeyError:
        return _run_not_found_response(run_id)

    payload = {
        "runId": run_id,
        "streams": run_service.get_log_streams_snapshot(run_id),
    }
    return JSONResponse(status_code=200, content=payload)


@router.get("/runs/{run_id}/logs/{task_id}", response_model=None)
def get_run_logs_for_task(
    run_id: str,
    task_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
) -> JSONResponse:
    try:
        run_service.get_session(run_id)
    except KeyError:
        return _run_not_found_response(run_id)

    try:
        payload = run_service.get_task_log_payload(run_id, task_id)
    except KeyError:
        return _log_stream_not_found_response(run_id, task_id)

    return JSONResponse(status_code=200, content=payload)


@router.get(
    "/runs/{run_id}/results",
    response_model=RunResultsResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_run_results(
    run_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
) -> RunResultsResponse | JSONResponse:
    try:
        payload = run_service.build_results(run_id)
    except KeyError:
        return _run_not_found_response(run_id)
    return RunResultsResponse.model_validate(payload)


@router.get(
    "/runs/{run_id}/artifacts/final-state",
    response_model=None,
    responses={404: {"model": ErrorResponse}},
)
def download_final_state_artifact(
    run_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
) -> FileResponse | JSONResponse:
    try:
        artifact = run_service.get_download_artifact(run_id, "final-state")
    except KeyError:
        return _run_not_found_response(run_id)
    if not artifact["exists"]:
        return _artifact_not_found_response(run_id, "final-state")
    return FileResponse(
        artifact["filePath"],
        media_type=artifact["contentType"],
        filename=artifact["filename"],
    )


@router.get(
    "/runs/{run_id}/artifacts/run-log",
    response_model=None,
    responses={404: {"model": ErrorResponse}},
)
def download_run_log_artifact(
    run_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
) -> FileResponse | JSONResponse:
    try:
        artifact = run_service.get_download_artifact(run_id, "run-log")
    except KeyError:
        return _run_not_found_response(run_id)
    if not artifact["exists"]:
        return _artifact_not_found_response(run_id, "run-log")
    return FileResponse(
        artifact["filePath"],
        media_type=artifact["contentType"],
        filename=artifact["filename"],
    )
