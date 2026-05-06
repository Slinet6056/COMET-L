from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import stat
import time
import uuid
import zipfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

import yaml
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import ValidationError

from comet.config import Settings
from comet.config.policy import (
    ConfigPolicyAnnotations,
    ConfigPolicyValueError,
    UnknownConfigFieldError,
    apply_run_form_policy,
    apply_uploaded_config_policy,
    enforce_deployment_policy,
    redacted_settings_dict,
)
from comet.config.settings import DeploymentPolicyConfig, GitHubConfig, ServerConfig

from .github_auth_service import GitHubAuthError, GitHubOAuthService
from .run_service import (
    ActiveRunConflictError,
    GitBranchConflictError,
    GitCloneError,
    GitDefaultBranchResolutionError,
    GitHubUnauthorizedError,
    GitNoWritePermissionError,
    InvalidGitHubRepoUrlError,
    InvalidJavaVersionError,
    NonMavenRepositoryError,
    QueueLimitExceededError,
    ReportGenerationError,
    RunLifecycleService,
    RunRequest,
)
from .schemas import (
    AdminCreateUserRequest,
    AdminResetPasswordRequest,
    AdminUpdateRoleRequest,
    AdminUserListResponse,
    AdminUserResponse,
    ApiError,
    AuthResponse,
    ConfigParseResponse,
    ConfigPayload,
    ErrorResponse,
    FieldError,
    GitHubRepositoriesResponse,
    GitHubRepositoryEntry,
    HealthResponse,
    LoginRequest,
    PublicDeploymentConfigResponse,
    RunCreateResponse,
    RunHistoryEntry,
    RunHistoryResponse,
    RunResultsResponse,
    RunSnapshotResponse,
    UploadCreateResponse,
)
from .storage import (
    AuthenticatedUser,
    DuplicateUserError,
    LastActiveAdminError,
    SafeUserRecord,
    UploadRecord,
    UserNotFoundError,
    WebDatabase,
)


@dataclass(slots=True)
class AppServices:
    run_service: RunLifecycleService
    github_auth_service: GitHubOAuthService
    default_config_path: Path
    web_database: WebDatabase | None = None
    system_initializer: Callable[..., dict[str, Any]] | None = None
    evolution_runner: Callable[..., None] | None = None


router = APIRouter(prefix="/api")
TERMINAL_RUN_STATUSES = {"completed", "failed"}
SESSION_COOKIE_NAME = "comet_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCKOUT_SECONDS = 5 * 60
PASSWORD_HASHER = PasswordHasher()
UPLOAD_STATUS_READY = "ready"
UPLOAD_KIND_PROJECT = "project"
UPLOAD_KIND_BUG_REPORTS = "bug_reports"
ALLOWED_BUG_REPORT_EXTENSIONS = {".md", ".txt", ".diff", ".patch"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ZIP_FILE_BYTES = 50 * 1024 * 1024
MAX_ZIP_FILE_COUNT = 5000
MAX_ZIP_COMPRESSION_RATIO = 100
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ApiErrorException(Exception):
    status_code: int
    code: str
    message: str
    field_errors: list[FieldError]


@dataclass(slots=True)
class _LoginFailureState:
    failed_attempts: int = 0
    locked_until: float = 0.0


class _LoginFailureThrottle:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str], _LoginFailureState] = {}

    def locked_for_seconds(self, *, username: str, ip_address: str) -> int | None:
        key = (username, ip_address)
        state = self._states.get(key)
        now = time.monotonic()
        if state is None or state.locked_until <= now:
            if state is not None and state.locked_until > 0:
                _ = self._states.pop(key, None)
            return None
        return max(1, int(state.locked_until - now))

    def record_failure(self, *, username: str, ip_address: str) -> bool:
        key = (username, ip_address)
        state = self._states.setdefault(key, _LoginFailureState())
        state.failed_attempts += 1
        if state.failed_attempts >= LOGIN_FAILURE_LIMIT:
            state.locked_until = time.monotonic() + LOGIN_LOCKOUT_SECONDS
            return True
        return False

    def record_success(self, *, username: str, ip_address: str) -> None:
        _ = self._states.pop((username, ip_address), None)


LOGIN_FAILURE_THROTTLE = _LoginFailureThrottle()


def get_app_services(request: Request) -> AppServices:
    return request.app.state.services


def get_run_service(
    services: AppServices = Depends(get_app_services),
) -> RunLifecycleService:
    return services.run_service


def get_github_auth_service(
    services: AppServices = Depends(get_app_services),
) -> GitHubOAuthService:
    return services.github_auth_service


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
    field_errors: list[FieldError],
) -> JSONResponse:
    payload = ErrorResponse(error=ApiError(code=code, message=message, fieldErrors=field_errors))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def api_exception_response(exc: ApiErrorException) -> JSONResponse:
    return _error_response(
        exc.status_code,
        code=exc.code,
        message=exc.message,
        field_errors=exc.field_errors,
    )


def _session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_expires_at() -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS)
    return expires_at.isoformat(timespec="seconds")


def _client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


def _auth_user_payload(user: AuthenticatedUser) -> AuthResponse:
    return AuthResponse.model_validate(
        {"user": {"id": user.id, "username": user.username, "role": user.role}}
    )


def _safe_user_payload(user: SafeUserRecord) -> AdminUserResponse:
    return AdminUserResponse.model_validate(
        {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "isActive": user.is_active,
            "createdAt": user.created_at,
            "updatedAt": user.updated_at,
            "disabledAt": user.disabled_at,
            "passwordChangedAt": user.password_changed_at,
        }
    )


def _admin_user_not_found_response(user_id: int) -> JSONResponse:
    return _error_response(
        404,
        code="user_not_found",
        message="用户不存在。",
        field_errors=[FieldError(path=["userId"], code="user_not_found", message=str(user_id))],
    )


def _last_admin_protected_response() -> JSONResponse:
    return _error_response(
        409,
        code="last_admin_protected",
        message="不能禁用或降级最后一个启用的管理员。",
        field_errors=[],
    )


def _duplicate_user_response(username: str) -> JSONResponse:
    return _error_response(
        409,
        code="duplicate_user",
        message="用户名已存在。",
        field_errors=[
            FieldError(path=["username"], code="duplicate_user", message=username.strip().lower())
        ],
    )


def _hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def _public_deployment_config(deployment: DeploymentPolicyConfig) -> dict[str, object]:
    return deployment.to_public_dict()


def _web_database(services: AppServices) -> WebDatabase:
    if services.web_database is None:
        raise ApiErrorException(
            503,
            code="auth_unavailable",
            message="认证服务暂不可用。",
            field_errors=[],
        )
    return services.web_database


def _authenticate_request(
    request: Request,
    services: AppServices,
) -> AuthenticatedUser | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return _web_database(services).get_active_session_user(token_hash=_session_token_hash(token))


def _auth_required_error() -> ApiErrorException:
    return ApiErrorException(
        401,
        code="auth_required",
        message="请先登录。",
        field_errors=[],
    )


def require_user(
    request: Request,
    services: AppServices = Depends(get_app_services),
) -> AuthenticatedUser:
    user = _authenticate_request(request, services)
    if user is None:
        raise _auth_required_error()
    return user


def require_admin(
    user: AuthenticatedUser = Depends(require_user),
) -> AuthenticatedUser:
    if user.role != "admin":
        raise ApiErrorException(
            403,
            code="admin_required",
            message="需要管理员权限。",
            field_errors=[],
        )
    return user


def _validation_errors(exc: ValidationError) -> list[FieldError]:
    return [
        FieldError(
            path=list(error.get("loc", ())),
            code=str(error.get("type", "validation_error")),
            message=str(error.get("msg", "Invalid value")),
        )
        for error in exc.errors()
    ]


def _github_oauth_result_redirect(
    result: str,
    *,
    message: str | None = None,
) -> RedirectResponse:
    query: dict[str, str] = {"github_oauth": result}
    if message:
        query["message"] = message
    return RedirectResponse(url=f"/?{urlencode(query)}", status_code=303)


def _load_settings_from_yaml(content: str) -> Settings:
    config_data = yaml.safe_load(content)
    if config_data is None:
        config_data = {}
    return Settings.model_validate(_strip_uploaded_runtime_overrides(config_data))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _strip_uploaded_runtime_overrides(config_data: dict[str, Any]) -> dict[str, Any]:
    return GitHubConfig.strip_yaml_config(config_data)


def _server_config_overrides(default_config_path: Path) -> dict[str, Any]:
    return _load_server_config(default_config_path).model_dump()


def _runtime_base_settings(
    default_config_path: Path,
    server_overrides: dict[str, Any],
    uploaded_config: dict[str, Any],
) -> Settings:
    try:
        base_settings = _load_default_settings(default_config_path)
        return Settings.model_validate(_deep_merge(base_settings.to_dict(), server_overrides))
    except ValidationError:
        return Settings.model_validate(_deep_merge(uploaded_config, server_overrides))


def _load_default_settings(default_config_path: Path) -> Settings:
    return Settings.from_yaml(str(default_config_path))


def _load_server_config(default_config_path: Path) -> ServerConfig:
    return ServerConfig.from_yaml(str(default_config_path))


def _config_error_response(exc: ValidationError) -> JSONResponse:
    return _error_response(
        422,
        code="invalid_config",
        message="Configuration validation failed.",
        field_errors=_validation_errors(exc),
    )


def _unknown_config_field_response(exc: UnknownConfigFieldError) -> JSONResponse:
    return _error_response(
        400,
        code="unknown_config_field",
        message="Configuration contains unknown fields.",
        field_errors=[
            FieldError(path=error.path, code=error.code, message=error.message)
            for error in exc.to_field_errors()
        ],
    )


def _policy_value_error_response(exc: ConfigPolicyValueError) -> JSONResponse:
    return _error_response(
        422,
        code=exc.code,
        message=exc.message,
        field_errors=[
            FieldError(path=error.path, code=error.code, message=error.message)
            for error in exc.to_field_errors()
        ],
    )


def _upload_error_response(
    *,
    code: str,
    message: str,
    field_code: str | None = None,
) -> JSONResponse:
    return _error_response(
        422,
        code=code,
        message=message,
        field_errors=[
            FieldError(path=["file"], code=field_code or code, message=message),
        ],
    )


def _is_unsafe_zip_name(name: str) -> bool:
    if not name or "\x00" in name:
        return True
    windows_path = PureWindowsPath(name)
    if windows_path.is_absolute() or windows_path.drive:
        return True
    posix_path = PurePosixPath(name)
    if posix_path.is_absolute():
        return True
    return any(part in {"", ".."} for part in posix_path.parts)


def _zip_entry_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0xFFFF


def _zip_entry_is_directory(info: zipfile.ZipInfo) -> bool:
    mode = _zip_entry_mode(info)
    if stat.S_IFMT(mode):
        return stat.S_ISDIR(mode)
    return info.filename.endswith("/")


def _zip_entry_is_regular_file(info: zipfile.ZipInfo) -> bool:
    mode = _zip_entry_mode(info)
    if stat.S_IFMT(mode):
        return stat.S_ISREG(mode)
    return not info.filename.endswith("/")


def _normalized_zip_entry_name(info: zipfile.ZipInfo) -> str:
    return PurePosixPath(info.filename).as_posix().rstrip("/")


def _validate_zip_entries(zip_file: zipfile.ZipFile) -> list[zipfile.ZipInfo] | JSONResponse:
    entries = zip_file.infolist()
    if not entries:
        return _upload_error_response(code="empty_zip", message="上传的 ZIP 文件不能为空。")
    if len(entries) > MAX_ZIP_FILE_COUNT:
        return _upload_error_response(
            code="zip_too_many_files",
            message="上传的 ZIP 文件条目过多。",
        )

    total_size = 0
    regular_files: list[zipfile.ZipInfo] = []
    seen_entry_names: set[str] = set()
    for info in entries:
        if _is_unsafe_zip_name(info.filename):
            return _upload_error_response(
                code="unsafe_zip_entry",
                message="ZIP 文件包含不安全路径。",
            )
        normalized_name = _normalized_zip_entry_name(info)
        if normalized_name in seen_entry_names:
            return _upload_error_response(
                code="unsafe_zip_entry",
                message="ZIP 文件包含重复路径。",
            )
        seen_entry_names.add(normalized_name)
        if not (_zip_entry_is_directory(info) or _zip_entry_is_regular_file(info)):
            return _upload_error_response(
                code="unsafe_zip_entry",
                message="ZIP 文件包含不受支持的文件类型。",
            )
        if _zip_entry_is_directory(info):
            continue
        if info.file_size > MAX_ZIP_FILE_BYTES:
            return _upload_error_response(
                code="zip_file_too_large",
                message="ZIP 文件中的单个文件过大。",
            )
        if info.file_size > MAX_ZIP_COMPRESSION_RATIO * max(info.compress_size, 1):
            return _upload_error_response(
                code="suspicious_zip_compression",
                message="ZIP 文件压缩率异常。",
            )
        total_size += info.file_size
        if total_size > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
            return _upload_error_response(
                code="zip_too_large",
                message="ZIP 文件解压后总大小过大。",
            )
        regular_files.append(info)

    if not regular_files:
        return _upload_error_response(code="empty_zip", message="上传的 ZIP 文件不能为空。")
    return regular_files


def _normalize_project_root(files: list[zipfile.ZipInfo]) -> str | JSONResponse:
    file_paths = [PurePosixPath(info.filename) for info in files]
    maven_roots = sorted(
        {
            path.parent.as_posix() if path.parent.as_posix() != "." else ""
            for path in file_paths
            if path.name == "pom.xml"
        }
    )
    if len(maven_roots) != 1:
        return _upload_error_response(
            code="non_maven_repository",
            message="项目 ZIP 必须包含且只能包含一个 Maven 根目录。",
        )
    return maven_roots[0]


def _validate_bug_report_files(files: list[zipfile.ZipInfo]) -> JSONResponse | None:
    for info in files:
        suffix = PurePosixPath(info.filename).suffix.lower()
        if suffix not in ALLOWED_BUG_REPORT_EXTENSIONS:
            return _upload_error_response(
                code="unsupported_bug_report_file",
                message="缺陷报告 ZIP 只能包含 .md、.txt、.diff 或 .patch 文件。",
            )
    return None


def _safe_extract_zip(
    zip_file: zipfile.ZipFile, entries: list[zipfile.ZipInfo], root: Path
) -> None:
    resolved_root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for info in entries:
        target = (root / PurePosixPath(info.filename).as_posix()).resolve()
        if os.path.commonpath([str(resolved_root), str(target)]) != str(resolved_root):
            raise ApiErrorException(
                422,
                code="unsafe_zip_entry",
                message="ZIP 文件包含不安全路径。",
                field_errors=[
                    FieldError(
                        path=["file"], code="unsafe_zip_entry", message="ZIP 文件包含不安全路径。"
                    )
                ],
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        with zip_file.open(info, "r") as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


async def _handle_upload(
    *,
    kind: Literal["project", "bug_reports"],
    file: UploadFile,
    user: AuthenticatedUser,
    services: AppServices,
) -> UploadCreateResponse | JSONResponse:
    original_filename = Path(file.filename or "upload.zip").name
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        return _upload_error_response(code="upload_too_large", message="上传文件过大。")

    try:
        with zipfile.ZipFile(BytesIO(content)) as zip_file:
            files_or_error = _validate_zip_entries(zip_file)
            if isinstance(files_or_error, JSONResponse):
                return files_or_error
            regular_files = files_or_error
            if kind == UPLOAD_KIND_PROJECT:
                extracted_root = _normalize_project_root(regular_files)
                if isinstance(extracted_root, JSONResponse):
                    return extracted_root
            else:
                bug_error = _validate_bug_report_files(regular_files)
                if bug_error is not None:
                    return bug_error
                extracted_root = ""

            upload_id = uuid.uuid4().hex
            upload_root = (
                services.run_service.workspace_root
                / "sandbox"
                / "users"
                / str(user.id)
                / "uploads"
                / upload_id
            )
            raw_root = upload_root / "raw"
            extracted_path = upload_root / "extracted"
            raw_root.mkdir(parents=True, exist_ok=True)
            raw_path = raw_root / original_filename
            raw_path.write_bytes(content)
            _safe_extract_zip(zip_file, regular_files, extracted_path)
    except zipfile.BadZipFile:
        return _upload_error_response(code="invalid_zip", message="上传文件不是有效的 ZIP。")

    metadata = {
        "original_filename": original_filename,
        "size_bytes": len(content),
        "file_count": len(regular_files),
        "extracted_root": extracted_root,
    }
    _web_database(services).create_upload_record(
        upload_id=upload_id,
        user_id=user.id,
        status=UPLOAD_STATUS_READY,
        kind=kind,
        original_filename=original_filename,
        storage_path=str(raw_path),
        extracted_path=str(extracted_path / extracted_root)
        if extracted_root
        else str(extracted_path),
        size_bytes=len(content),
        path_metadata=metadata,
    )
    return UploadCreateResponse(
        uploadId=upload_id,
        kind=kind,
        status=UPLOAD_STATUS_READY,
        originalFilename=original_filename,
        extractedRoot=extracted_root,
    )


@router.post(
    "/auth/login",
    response_model=AuthResponse,
    responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
def login(
    request: Request,
    payload: LoginRequest,
    services: AppServices = Depends(get_app_services),
) -> AuthResponse | JSONResponse:
    username = payload.username.strip().lower()
    ip_address = _client_ip(request)
    locked_for = LOGIN_FAILURE_THROTTLE.locked_for_seconds(
        username=username,
        ip_address=ip_address,
    )
    if locked_for is not None:
        return _error_response(
            429,
            code="login_locked",
            message="登录失败次数过多，请稍后再试。",
            field_errors=[
                FieldError(
                    path=["username"],
                    code="login_temporarily_locked",
                    message=f"请在 {locked_for} 秒后重试。",
                )
            ],
        )

    database = _web_database(services)
    user = database.get_user_by_username(username) if username else None
    password_ok = False
    if user is not None and user.is_active:
        try:
            password_ok = PASSWORD_HASHER.verify(user.password_hash, payload.password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            password_ok = False

    if user is None or not user.is_active or not password_ok:
        locked = LOGIN_FAILURE_THROTTLE.record_failure(username=username, ip_address=ip_address)
        return _error_response(
            429 if locked else 401,
            code="login_locked" if locked else "invalid_credentials",
            message="登录失败次数过多，请稍后再试。" if locked else "用户名或密码不正确。",
            field_errors=[],
        )

    LOGIN_FAILURE_THROTTLE.record_success(username=username, ip_address=ip_address)
    token = secrets.token_urlsafe(32)
    database.create_session(
        user_id=user.id,
        token_hash=_session_token_hash(token),
        expires_at=_session_expires_at(),
        user_agent=request.headers.get("user-agent"),
        ip_address=ip_address,
    )
    response = JSONResponse(
        status_code=200,
        content=_auth_user_payload(
            AuthenticatedUser(id=user.id, username=user.username, role=user.role)
        ).model_dump(),
    )
    server_config = _load_server_config(services.default_config_path)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        path="/",
        secure=server_config.deployment.secure_auth_cookies,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/auth/logout", response_model=None)
def logout(
    request: Request,
    services: AppServices = Depends(get_app_services),
) -> JSONResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        _web_database(services).revoke_session(token_hash=_session_token_hash(token))
    response = JSONResponse(status_code=200, content={"user": None})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@router.get(
    "/auth/me",
    response_model=AuthResponse,
    responses={401: {"model": ErrorResponse}},
)
def get_current_user(
    user: AuthenticatedUser = Depends(require_user),
) -> AuthResponse:
    return _auth_user_payload(user)


@router.get(
    "/deployment/public-config",
    response_model=PublicDeploymentConfigResponse,
)
def get_public_deployment_config(
    services: AppServices = Depends(get_app_services),
    _user: AuthenticatedUser = Depends(require_user),
) -> PublicDeploymentConfigResponse:
    server_config = _load_server_config(services.default_config_path)
    return PublicDeploymentConfigResponse(
        deployment=_public_deployment_config(server_config.deployment)
    )


@router.get(
    "/admin/users",
    response_model=AdminUserListResponse,
    responses={403: {"model": ErrorResponse}},
)
def list_admin_users(
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
) -> AdminUserListResponse:
    users = _web_database(services).list_users()
    return AdminUserListResponse(users=[_safe_user_payload(user) for user in users])


@router.post(
    "/admin/users",
    response_model=AdminUserResponse,
    status_code=201,
    responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def create_admin_user(
    payload: AdminCreateUserRequest,
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
) -> AdminUserResponse | JSONResponse:
    database = _web_database(services)
    try:
        user_id = database.create_user(
            username=payload.username,
            password_hash=_hash_password(payload.password),
            role=payload.role,
        )
    except DuplicateUserError:
        return _duplicate_user_response(payload.username)
    user = database.get_safe_user_by_id(user_id)
    if user is None:  # pragma: no cover - create_user returning a missing id is unexpected
        return _admin_user_not_found_response(user_id)
    return _safe_user_payload(user)


@router.post(
    "/admin/users/{user_id}/disable",
    response_model=AdminUserResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def disable_admin_user(
    user_id: int,
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
) -> AdminUserResponse | JSONResponse:
    try:
        user = _web_database(services).disable_user(user_id)
    except UserNotFoundError:
        return _admin_user_not_found_response(user_id)
    except LastActiveAdminError:
        return _last_admin_protected_response()
    return _safe_user_payload(user)


@router.post(
    "/admin/users/{user_id}/reset-password",
    response_model=AdminUserResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def reset_admin_user_password(
    user_id: int,
    payload: AdminResetPasswordRequest,
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
) -> AdminUserResponse | JSONResponse:
    try:
        user = _web_database(services).reset_user_password(
            user_id,
            password_hash=_hash_password(payload.password),
        )
    except UserNotFoundError:
        return _admin_user_not_found_response(user_id)
    return _safe_user_payload(user)


@router.post(
    "/admin/users/{user_id}/role",
    response_model=AdminUserResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def update_admin_user_role(
    user_id: int,
    payload: AdminUpdateRoleRequest,
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
) -> AdminUserResponse | JSONResponse:
    try:
        user = _web_database(services).update_user_role(user_id, role=payload.role)
    except UserNotFoundError:
        return _admin_user_not_found_response(user_id)
    except LastActiveAdminError:
        return _last_admin_protected_response()
    return _safe_user_payload(user)


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


def _run_is_visible_to_user(session: Any, user: AuthenticatedUser) -> bool:
    return user.role == "admin" or getattr(session, "user_id", None) == user.id


def _load_visible_run_session(
    run_service: RunLifecycleService,
    run_id: str,
    user: AuthenticatedUser,
) -> Any | None:
    try:
        return run_service.get_visible_session(
            run_id,
            user_id=user.id,
            include_all=user.role == "admin",
        )
    except KeyError:
        return None


def _visible_run_session(
    run_service: RunLifecycleService,
    run_id: str,
    user: AuthenticatedUser,
) -> Any | None:
    return _load_visible_run_session(run_service, run_id, user)


def _visible_run_history_items(
    run_service: RunLifecycleService,
    user: AuthenticatedUser,
) -> list[dict[str, Any]]:
    return run_service.list_runs_for_user(user_id=user.id, include_all=user.role == "admin")


def _resolve_runtime_hooks(
    services: AppServices,
) -> tuple[Callable[..., dict[str, Any]], Callable[..., None]]:
    if services.system_initializer is not None and services.evolution_runner is not None:
        return services.system_initializer, services.evolution_runner

    import main

    return main.initialize_system, main.run_evolution


def _validate_project_path(project_path: str | None) -> list[FieldError]:
    normalized_path = project_path.strip() if project_path is not None else ""
    if not normalized_path:
        return [
            FieldError(
                path=["projectPath"],
                code="missing_project_path",
                message="项目路径不能为空。",
            )
        ]

    resolved_path = Path(normalized_path).expanduser()
    if not resolved_path.exists():
        return [
            FieldError(
                path=["projectPath"],
                code="path_not_found",
                message="项目路径不存在。",
            )
        ]
    if not resolved_path.is_dir() or not (resolved_path / "pom.xml").is_file():
        return [
            FieldError(
                path=["projectPath"],
                code="non_maven_repository",
                message="项目路径必须指向包含 pom.xml 的 Maven 仓库。",
            )
        ]
    return []


def _local_path_forbidden_response(field_paths: list[list[str]]) -> JSONResponse:
    return _error_response(
        403,
        code="local_path_forbidden",
        message="当前用户不能使用服务器本地路径创建运行。",
        field_errors=[
            FieldError(
                path=list[str | int](path),
                code="local_path_forbidden",
                message="请使用上传 ID 创建运行。",
            )
            for path in field_paths
        ],
    )


def _upload_not_found_response(field_name: str) -> JSONResponse:
    return _error_response(
        404,
        code="upload_not_found",
        message="上传记录不存在或不可用。",
        field_errors=[
            FieldError(
                path=[field_name], code="upload_not_found", message="上传记录不存在或不可用。"
            )
        ],
    )


def _upload_already_used_response(field_name: str) -> JSONResponse:
    return _error_response(
        409,
        code="upload_already_used",
        message="上传文件已被使用，请重新上传后再创建运行。",
        field_errors=[
            FieldError(path=[field_name], code="upload_already_used", message="上传文件已被使用。")
        ],
    )


def _validate_ready_upload(
    database: WebDatabase,
    *,
    upload_id: str,
    user_id: int,
    kind: str,
    field_name: str,
) -> UploadRecord | JSONResponse:
    current = database.get_upload_record(upload_id)
    if (
        current is None
        or current.user_id != user_id
        or current.kind != kind
        or current.status != UPLOAD_STATUS_READY
    ):
        return _upload_not_found_response(field_name)
    if current.used_at is not None:
        return _upload_already_used_response(field_name)
    return current


def _mark_validated_upload_used(
    database: WebDatabase,
    upload: UploadRecord,
    *,
    field_name: str,
) -> UploadRecord | JSONResponse:
    marked = database.mark_upload_used_once(
        upload_id=upload.id,
        user_id=upload.user_id if upload.user_id is not None else -1,
        kind=upload.kind,
        status=UPLOAD_STATUS_READY,
    )
    if marked is None:
        return _upload_already_used_response(field_name)
    return marked


def _mark_validated_uploads_used(
    database: WebDatabase,
    uploads: list[tuple[UploadRecord, str]],
) -> list[UploadRecord] | JSONResponse:
    marked_uploads: list[UploadRecord] = []
    for upload, field_name in uploads:
        marked = _mark_validated_upload_used(database, upload, field_name=field_name)
        if isinstance(marked, JSONResponse):
            _rollback_upload_consumption(database, marked_uploads)
            return marked
        marked_uploads.append(marked)
    return marked_uploads


def _rollback_upload_consumption(database: WebDatabase, uploads: list[UploadRecord]) -> None:
    upload_ids = [upload.id for upload in uploads]
    if not upload_ids:
        return
    try:
        database.reset_uploads_used_at(upload_ids)
    except Exception:
        logger.exception("回滚上传消费状态失败: %s", upload_ids)


def _safe_field_error(path: list[str | int], code: str, message: str) -> FieldError:
    return FieldError(path=path, code=code, message=message)


def _resolve_allowlisted_local_path(path_value: str | None, allowlist: list[str]) -> Path | None:
    if path_value is None or not path_value.strip() or not allowlist:
        return None
    try:
        resolved_path = Path(path_value).expanduser().resolve()
        allowlist_roots = [Path(root).expanduser().resolve() for root in allowlist if root.strip()]
    except OSError:
        return None
    if not allowlist_roots:
        return None
    if not any(resolved_path.is_relative_to(root) for root in allowlist_roots):
        return None
    return resolved_path


def _validate_admin_local_path(
    *,
    settings: Settings,
    project_path: str | None,
    bug_reports_dir: str | None,
) -> tuple[Path, Path | None] | JSONResponse:
    if (
        not settings.deployment.allow_local_path_mode
        or not settings.deployment.local_path_allowlist
    ):
        field_paths = [["projectPath"]]
        if bug_reports_dir is not None:
            field_paths.append(["bugReportsDir"])
        return _local_path_forbidden_response(field_paths)

    resolved_project = _resolve_allowlisted_local_path(
        project_path,
        settings.deployment.local_path_allowlist,
    )
    if resolved_project is None:
        return _local_path_forbidden_response([["projectPath"]])

    field_errors = _validate_project_path(str(resolved_project))
    if field_errors:
        top_level_code = field_errors[0].code
        return _error_response(
            422,
            code="non_maven_repository"
            if top_level_code == "non_maven_repository"
            else "invalid_project_path",
            message="项目路径校验失败。",
            field_errors=field_errors,
        )

    resolved_bug_reports: Path | None = None
    if bug_reports_dir is not None and bug_reports_dir.strip():
        resolved_bug_reports = _resolve_allowlisted_local_path(
            bug_reports_dir,
            settings.deployment.local_path_allowlist,
        )
        if resolved_bug_reports is None or not resolved_bug_reports.is_dir():
            return _local_path_forbidden_response([["bugReportsDir"]])

    return resolved_project, resolved_bug_reports


def _validate_github_repo_url(repo_url: str | None) -> list[FieldError]:
    if repo_url is None or not repo_url.strip():
        return []

    parsed = urlparse(repo_url.strip())
    path_parts = [segment for segment in parsed.path.split("/") if segment]
    is_valid_host = parsed.netloc.lower() in {"github.com", "www.github.com"}
    if parsed.scheme != "https" or not is_valid_host or len(path_parts) != 2:
        return [
            FieldError(
                path=["githubRepoUrl"],
                code="invalid_github_repo_url",
                message="仓库地址必须是 https://github.com/<owner>/<repo> 或 .git 结尾格式。",
            )
        ]

    owner = path_parts[0].strip()
    repo = path_parts[1].strip()
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return [
            FieldError(
                path=["githubRepoUrl"],
                code="invalid_github_repo_url",
                message="仓库地址必须是 https://github.com/<owner>/<repo> 或 .git 结尾格式。",
            )
        ]
    return []


def _validate_github_base_branch(
    github_repo_url: str | None,
    github_base_branch: str | None,
) -> list[FieldError]:
    if github_base_branch is None or not github_base_branch.strip():
        return []
    if github_repo_url is None or not github_repo_url.strip():
        return [
            FieldError(
                path=["githubBaseBranch"],
                code="missing_github_repo_url",
                message="指定基线分支前必须提供 GitHub 仓库地址。",
            )
        ]
    return []


def _validate_selected_java_version(
    settings: Settings,
    selected_java_version: str | None,
) -> list[FieldError]:
    if selected_java_version is None or not selected_java_version.strip():
        return []

    available_versions = settings.deployment.allowed_java_versions
    normalized_version = selected_java_version.strip()
    if normalized_version not in available_versions:
        return [
            FieldError(
                path=["selectedJavaVersion"],
                code="invalid_java_version",
                message=(
                    f"不支持的 Java 版本: {normalized_version}。"
                    f"可选值: {', '.join(available_versions)}。"
                ),
            )
        ]
    return []


def _validate_github_authorization(
    settings: Settings,
    github_repo_url: str | None,
    github_auth_service: GitHubOAuthService,
) -> list[FieldError]:
    if github_repo_url is None or not github_repo_url.strip():
        return []

    try:
        status = github_auth_service.get_status(settings.github)
    except GitHubAuthError:
        return [
            FieldError(
                path=["githubRepoUrl"],
                code="github_unauthorized",
                message="GitHub 授权状态异常，请重新授权后重试。",
            )
        ]

    if status.connected:
        return []

    return [
        FieldError(
            path=["githubRepoUrl"],
            code="github_unauthorized",
            message="未检测到 GitHub 授权，请先完成授权后再启动运行。",
        )
    ]


def _validate_managed_clone_root_writable(
    settings: Settings,
    github_repo_url: str | None,
) -> list[FieldError]:
    if github_repo_url is None or not github_repo_url.strip():
        return []

    clone_root = Path(settings.github.managed_clone_root).expanduser()
    probe = clone_root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent

    if not os.access(probe, os.W_OK):
        return [
            FieldError(
                path=["githubRepoUrl"],
                code="git_no_write_permission",
                message=f"无写权限，无法写入受管 clone 目录: {clone_root}",
            )
        ]
    return []


async def _load_merged_settings(
    services: AppServices,
    config_file: UploadFile | None,
) -> tuple[Settings, str, ConfigPolicyAnnotations] | JSONResponse:
    server_overrides = _server_config_overrides(services.default_config_path)
    config_label = str(services.default_config_path)

    if config_file is None:
        try:
            base_settings = _load_default_settings(services.default_config_path)
            effective = Settings.model_validate(
                _deep_merge(base_settings.to_dict(), server_overrides)
            )
            annotations = enforce_deployment_policy(effective)
        except ValidationError as exc:
            return _config_error_response(exc)
        return effective, config_label, annotations

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
        normalized = _strip_uploaded_runtime_overrides(parsed)
        base_settings = _runtime_base_settings(
            services.default_config_path,
            server_overrides,
            normalized,
        )
        result = apply_uploaded_config_policy(base_settings, normalized)
    except ValidationError as exc:
        return _config_error_response(exc)
    except UnknownConfigFieldError as exc:
        return _unknown_config_field_response(exc)
    except ConfigPolicyValueError as exc:
        return _policy_value_error_response(exc)

    return result.settings, (config_file.filename or "uploaded-config.yaml"), result.annotations


@router.get("/health", response_model=HealthResponse)
def get_health(
    run_service: RunLifecycleService = Depends(get_run_service),
) -> HealthResponse:
    return HealthResponse(status="ok", activeRunId=run_service.active_run_id())


@router.get("/config/defaults", response_model=ConfigPayload)
def get_config_defaults(
    services: AppServices = Depends(get_app_services),
    _: AuthenticatedUser = Depends(require_user),
) -> ConfigPayload:
    settings = _load_default_settings(services.default_config_path)
    settings.deployment = _load_server_config(services.default_config_path).deployment
    safe_config, annotations = redacted_settings_dict(settings)
    return ConfigPayload.model_validate(
        {"config": safe_config, "configPolicy": annotations.to_api_dict()}
    )


@router.post(
    "/config/parse",
    response_model=ConfigParseResponse,
    responses={422: {"model": ErrorResponse}},
)
async def parse_config(
    file: UploadFile = File(...),
    _user: AuthenticatedUser = Depends(require_user),
    _run_service: RunLifecycleService = Depends(get_run_service),
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
        config_data = yaml.safe_load(content)
        if config_data is None:
            config_data = {}
        if not isinstance(config_data, dict):
            return _error_response(
                422,
                code="invalid_yaml",
                message="Configuration file root must be a mapping.",
                field_errors=[
                    FieldError(
                        path=[],
                        code="yaml_root_type",
                        message="Configuration file root must be a YAML mapping.",
                    )
                ],
            )
        settings = Settings.model_validate(_strip_uploaded_runtime_overrides(config_data))
        safe_config, annotations = redacted_settings_dict(settings)
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

    return ConfigParseResponse.model_validate(
        {"config": safe_config, "configPolicy": annotations.to_api_dict()}
    )


@router.post(
    "/runs",
    response_model=RunCreateResponse,
    status_code=201,
    responses={
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
async def create_run(
    request: Request,
    projectPath: str | None = Form(default=None),
    projectUploadId: str | None = Form(default=None),
    configFile: UploadFile | None = File(default=None),
    maxIterations: int | None = Form(default=None),
    budget: int | None = Form(default=None),
    mutationEnabled: bool | None = Form(default=None),
    resumeState: str | None = Form(default=None),
    debug: bool = Form(default=False),
    bugReportsDir: str | None = Form(default=None),
    bugReportsUploadId: str | None = Form(default=None),
    parallel: bool = Form(default=False),
    parallelTargets: int | None = Form(default=None),
    githubRepoUrl: str | None = Form(default=None),
    githubBaseBranch: str | None = Form(default=None),
    selectedJavaVersion: str | None = Form(default=None),
    user: AuthenticatedUser = Depends(require_user),
    services: AppServices = Depends(get_app_services),
    run_service: RunLifecycleService = Depends(get_run_service),
    github_auth_service: GitHubOAuthService = Depends(get_github_auth_service),
) -> RunCreateResponse | JSONResponse:
    del request
    normalized_github_repo_url = githubRepoUrl.strip() if githubRepoUrl else None
    if normalized_github_repo_url is not None and user.role != "admin":
        return _error_response(
            403,
            code="admin_required",
            message="需要管理员权限。",
            field_errors=[],
        )

    merged = await _load_merged_settings(services, configFile)
    if isinstance(merged, JSONResponse):
        return merged
    settings, config_label, uploaded_policy_annotations = merged

    github_field_errors = _validate_github_repo_url(githubRepoUrl)
    github_field_errors.extend(_validate_github_base_branch(githubRepoUrl, githubBaseBranch))
    if github_field_errors:
        return _error_response(
            422,
            code="invalid_github_repo_url",
            message="GitHub 仓库参数无效。",
            field_errors=github_field_errors,
        )

    java_field_errors = _validate_selected_java_version(settings, selectedJavaVersion)
    if java_field_errors:
        return _error_response(
            422,
            code="invalid_java_version",
            message="Java 版本参数无效。",
            field_errors=java_field_errors,
        )

    try:
        policy_result = apply_run_form_policy(
            settings,
            max_iterations=maxIterations,
            budget=budget,
            mutation_enabled=mutationEnabled,
            parallel=parallel,
            parallel_targets=parallelTargets,
            selected_java_version=selectedJavaVersion,
        )
    except ConfigPolicyValueError as exc:
        return _policy_value_error_response(exc)
    settings = policy_result.settings
    policy_result.annotations.extend(uploaded_policy_annotations)
    request_max_iterations = (
        settings.evolution.max_iterations if maxIterations is not None else None
    )
    request_budget = settings.evolution.budget_llm_calls if budget is not None else None
    request_selected_java_version = (
        settings.execution.selected_java_version if selectedJavaVersion is not None else None
    )

    database = _web_database(services)
    normalized_project_upload_id = projectUploadId.strip() if projectUploadId else None
    normalized_bug_reports_upload_id = bugReportsUploadId.strip() if bugReportsUploadId else None
    normalized_project_path = ""
    normalized_bug_reports_dir: str | None = None
    source_metadata: dict[str, Any]
    upload_source_response: dict[str, object]
    uploads_to_mark_used: list[tuple[UploadRecord, str]] = []

    if normalized_github_repo_url is None:
        local_path_fields: list[list[str]] = []
        if projectPath is not None and projectPath.strip():
            local_path_fields.append(["projectPath"])
        if bugReportsDir is not None and bugReportsDir.strip():
            local_path_fields.append(["bugReportsDir"])
        if user.role != "admin" and local_path_fields:
            return _local_path_forbidden_response(local_path_fields)

        if user.role == "admin" and (projectPath is not None and projectPath.strip()):
            resolved_local = _validate_admin_local_path(
                settings=settings,
                project_path=projectPath,
                bug_reports_dir=bugReportsDir,
            )
            if isinstance(resolved_local, JSONResponse):
                return resolved_local
            resolved_project, resolved_bug_reports = resolved_local
            normalized_project_path = str(resolved_project)
            normalized_bug_reports_dir = str(resolved_bug_reports) if resolved_bug_reports else None
            source_metadata = {"uploadSource": {"mode": "local_path"}}
            upload_source_response = {"mode": "local_path"}
        else:
            if not normalized_project_upload_id:
                return _upload_not_found_response("projectUploadId")
            project_upload = _validate_ready_upload(
                database,
                upload_id=normalized_project_upload_id,
                user_id=user.id,
                kind=UPLOAD_KIND_PROJECT,
                field_name="projectUploadId",
            )
            if isinstance(project_upload, JSONResponse):
                return project_upload
            bug_reports_upload: UploadRecord | None = None
            if normalized_bug_reports_upload_id:
                marked_bug_upload = _validate_ready_upload(
                    database,
                    upload_id=normalized_bug_reports_upload_id,
                    user_id=user.id,
                    kind=UPLOAD_KIND_BUG_REPORTS,
                    field_name="bugReportsUploadId",
                )
                if isinstance(marked_bug_upload, JSONResponse):
                    return marked_bug_upload
                bug_reports_upload = marked_bug_upload
            uploads_to_mark_used = [(project_upload, "projectUploadId")]
            if bug_reports_upload is not None:
                uploads_to_mark_used.append((bug_reports_upload, "bugReportsUploadId"))

            normalized_project_path = str(
                Path(project_upload.extracted_path).expanduser().resolve()
            )
            normalized_bug_reports_dir = (
                str(Path(bug_reports_upload.extracted_path).expanduser().resolve())
                if bug_reports_upload is not None
                else None
            )
            field_errors = _validate_project_path(normalized_project_path)
            if field_errors:
                return _error_response(
                    422,
                    code="non_maven_repository"
                    if field_errors[0].code == "non_maven_repository"
                    else "invalid_project_path",
                    message="项目路径校验失败。",
                    field_errors=field_errors,
                )
            upload_source_response = {
                "mode": "upload",
                "projectUploadId": project_upload.id,
                "bugReportsUploadId": bug_reports_upload.id if bug_reports_upload else None,
            }
            source_metadata = {
                "uploadSource": {
                    "mode": "upload",
                    "projectUploadId": project_upload.id,
                    "bugReportsUploadId": bug_reports_upload.id if bug_reports_upload else None,
                    "project": {
                        "uploadId": project_upload.id,
                        "kind": project_upload.kind,
                        "originalFilename": project_upload.original_filename,
                        "extractedRoot": project_upload.path_metadata.get("extracted_root"),
                    },
                    "bugReports": {
                        "uploadId": bug_reports_upload.id,
                        "kind": bug_reports_upload.kind,
                        "originalFilename": bug_reports_upload.original_filename,
                        "extractedRoot": bug_reports_upload.path_metadata.get("extracted_root"),
                    }
                    if bug_reports_upload is not None
                    else None,
                }
            }
    else:
        normalized_project_path = (
            str(Path(projectPath).expanduser().resolve())
            if projectPath is not None and projectPath.strip()
            else ""
        )
        normalized_bug_reports_dir = bugReportsDir
        source_metadata = {"uploadSource": {"mode": "github"}}
        upload_source_response = {"mode": "github"}

    github_auth_errors = _validate_github_authorization(
        settings,
        githubRepoUrl,
        github_auth_service,
    )
    if github_auth_errors:
        return _error_response(
            401,
            code="github_unauthorized",
            message="当前未授权 GitHub，无法处理仓库模式运行请求。",
            field_errors=github_auth_errors,
        )

    git_write_errors = _validate_managed_clone_root_writable(settings, githubRepoUrl)
    if git_write_errors:
        return _error_response(
            403,
            code="git_no_write_permission",
            message="受管 clone 目录无写权限。",
            field_errors=git_write_errors,
        )

    effective_config_response, _ = redacted_settings_dict(settings)

    marked_uploads_for_rollback: list[UploadRecord] = []
    try:
        if uploads_to_mark_used:
            run_service.assert_queue_capacity(settings, user_id=user.id)
            marked_uploads = _mark_validated_uploads_used(database, uploads_to_mark_used)
            if isinstance(marked_uploads, JSONResponse):
                _rollback_upload_consumption(database, marked_uploads_for_rollback)
                return marked_uploads
            marked_uploads_for_rollback = marked_uploads
        run_session = run_service.create_run(
            RunRequest(
                project_path=normalized_project_path,
                config_path=config_label,
                max_iterations=request_max_iterations,
                budget=request_budget,
                mutation_enabled=None,
                resume_state=resumeState,
                debug=debug,
                bug_reports_dir=normalized_bug_reports_dir,
                parallel=parallel,
                parallel_targets=None,
                github_repo_url=normalized_github_repo_url,
                github_base_branch=githubBaseBranch.strip() if githubBaseBranch else None,
                selected_java_version=request_selected_java_version,
                source_metadata=source_metadata,
            ),
            user_id=user.id,
            settings_loader=lambda _config_path: settings,
        )
    except ActiveRunConflictError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: active_run_conflict: %s", exc)
        return _error_response(
            409,
            code="active_run_conflict",
            message="已有运行任务正在执行，请稍后重试。",
            field_errors=[_safe_field_error([], "active_run_exists", "已有运行任务正在执行。")],
        )
    except QueueLimitExceededError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: queue_limit_exceeded: %s", exc)
        return _error_response(
            429,
            code="queue_limit_exceeded",
            message="运行队列已满，请稍后重试。",
            field_errors=[_safe_field_error([], "queue_limit_exceeded", "运行队列已满。")],
        )
    except InvalidGitHubRepoUrlError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: invalid_github_repo_url: %s", exc)
        return _error_response(
            422,
            code="invalid_github_repo_url",
            message="GitHub 仓库地址不合法。",
            field_errors=[
                _safe_field_error(
                    ["githubRepoUrl"], "invalid_github_repo_url", "GitHub 仓库地址不合法。"
                )
            ],
        )
    except InvalidJavaVersionError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: invalid_java_version: %s", exc)
        return _error_response(
            422,
            code="invalid_java_version",
            message="Java 版本参数无效。",
            field_errors=[
                _safe_field_error(
                    ["selectedJavaVersion"], "invalid_java_version", "Java 版本参数无效。"
                )
            ],
        )
    except GitHubUnauthorizedError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: github_unauthorized: %s", exc)
        return _error_response(
            401,
            code="github_unauthorized",
            message="当前未授权 GitHub，请先完成授权。",
            field_errors=[
                _safe_field_error(
                    ["githubRepoUrl"], "github_unauthorized", "当前未授权 GitHub，请先完成授权。"
                )
            ],
        )
    except GitNoWritePermissionError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: git_no_write_permission: %s", exc)
        return _error_response(
            403,
            code="git_no_write_permission",
            message="无写权限，无法写入受管仓库目录。",
            field_errors=[
                _safe_field_error(
                    ["githubRepoUrl"], "git_no_write_permission", "无写权限，无法写入受管仓库目录。"
                )
            ],
        )
    except GitBranchConflictError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: git_branch_conflict: %s", exc)
        return _error_response(
            409,
            code="git_branch_conflict",
            message="目标分支发生冲突，请先同步后重试。",
            field_errors=[
                _safe_field_error(["githubBaseBranch"], "git_branch_conflict", "目标分支发生冲突。")
            ],
        )
    except GitDefaultBranchResolutionError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: github_default_branch_unresolved: %s", exc)
        return _error_response(
            422,
            code="github_default_branch_unresolved",
            message="默认分支解析失败，请稍后重试或手动指定基线分支。",
            field_errors=[
                _safe_field_error(
                    ["githubBaseBranch"],
                    "github_default_branch_unresolved",
                    "默认分支解析失败。",
                )
            ],
        )
    except GitCloneError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: git_clone_failed: %s", exc)
        return _error_response(
            502,
            code="git_clone_failed",
            message="仓库克隆失败，请检查仓库地址、分支和网络后重试。",
            field_errors=[
                _safe_field_error(["githubRepoUrl"], "git_clone_failed", "仓库克隆失败。")
            ],
        )
    except NonMavenRepositoryError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: non_maven_repository: %s", exc)
        return _error_response(
            422,
            code="non_maven_repository",
            message="导入仓库不是 Maven 仓库。",
            field_errors=[
                _safe_field_error(
                    ["projectPath"], "non_maven_repository", "导入仓库不是 Maven 仓库。"
                )
            ],
        )
    except ReportGenerationError as exc:
        _rollback_upload_consumption(database, marked_uploads_for_rollback)
        logger.warning("创建运行失败: report_generation_failed: %s", exc)
        return _error_response(
            500,
            code="report_generation_failed",
            message="报告生成失败，请检查日志后重试。",
            field_errors=[
                _safe_field_error(["reportArtifact"], "report_generation_failed", "报告生成失败。")
            ],
        )

    created_response = RunCreateResponse(
        runId=run_session.run_id,
        status=run_session.status,
        mode=run_service.run_mode(run_session.run_id),
        configPolicy=policy_result.annotations.to_api_dict(),
        queuePosition=run_session.queue_position,
        effectiveConfig=effective_config_response,
        uploadSource=upload_source_response,
    )
    system_initializer, evolution_runner = _resolve_runtime_hooks(services)

    def load_created_run_settings(config_path: str | None) -> Settings:
        if config_path != run_session.config_path:
            raise RuntimeError("运行配置路径与创建时固化的配置快照不一致。")
        return settings.model_copy(deep=True)

    run_service.start_run(
        run_session.run_id,
        settings_loader=load_created_run_settings,
        system_initializer=system_initializer,
        evolution_runner=evolution_runner,
    )
    return created_response


@router.post(
    "/uploads/project",
    response_model=UploadCreateResponse,
    status_code=201,
    responses={422: {"model": ErrorResponse}},
)
async def upload_project_zip(
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_user),
    services: AppServices = Depends(get_app_services),
) -> UploadCreateResponse | JSONResponse:
    return await _handle_upload(
        kind=UPLOAD_KIND_PROJECT,
        file=file,
        user=user,
        services=services,
    )


@router.post(
    "/uploads/bug-reports",
    response_model=UploadCreateResponse,
    status_code=201,
    responses={422: {"model": ErrorResponse}},
)
async def upload_bug_reports_zip(
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_user),
    services: AppServices = Depends(get_app_services),
) -> UploadCreateResponse | JSONResponse:
    return await _handle_upload(
        kind=UPLOAD_KIND_BUG_REPORTS,
        file=file,
        user=user,
        services=services,
    )


@router.get("/github/auth/connect-url", response_model=None)
def get_github_auth_connect_url(
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
    github_auth_service: GitHubOAuthService = Depends(get_github_auth_service),
) -> JSONResponse:
    server_config = _load_server_config(services.default_config_path)
    try:
        connect_url = github_auth_service.build_connect_url(server_config.github)
    except GitHubAuthError as exc:
        logger.warning("GitHub OAuth 配置无效: %s", exc)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "github_oauth_config_invalid",
                    "message": "GitHub OAuth 配置无效，请联系管理员。",
                    "fieldErrors": [],
                }
            },
        )
    return JSONResponse(status_code=200, content={"connectUrl": connect_url})


@router.get("/github/auth/callback", response_model=None)
def handle_github_auth_callback(
    request: Request,
    code: str | None = Query(default=None, min_length=1),
    state: str | None = Query(default=None, min_length=1),
    error: str | None = Query(default=None, min_length=1),
    error_description: str | None = Query(default=None, min_length=1),
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
    github_auth_service: GitHubOAuthService = Depends(get_github_auth_service),
) -> JSONResponse | RedirectResponse:
    server_config = _load_server_config(services.default_config_path)
    accept_header = request.headers.get("accept", "").lower()
    accepts_html = "application/json" not in accept_header and "text/html" in accept_header

    if error is not None:
        message = (
            "GitHub 授权已取消，请重新发起授权。"
            if error == "access_denied"
            else error_description or "GitHub 授权失败，请重试。"
        )
        if accepts_html:
            return _github_oauth_result_redirect("error", message=message)
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "github_oauth_callback_failed",
                    "message": message,
                    "fieldErrors": [],
                }
            },
        )

    if code is None or state is None:
        message = "GitHub OAuth 回调缺少必要参数，请重新发起授权。"
        if accepts_html:
            return _github_oauth_result_redirect("error", message=message)
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "github_oauth_callback_failed",
                    "message": message,
                    "fieldErrors": [],
                }
            },
        )

    try:
        status = github_auth_service.handle_callback(server_config.github, code=code, state=state)
    except GitHubAuthError as exc:
        logger.warning("GitHub OAuth 回调失败: %s", exc)
        message = "GitHub 授权失败，请重新发起授权。"
        if accepts_html:
            return _github_oauth_result_redirect("error", message=message)
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "github_oauth_callback_failed",
                    "message": message,
                    "fieldErrors": [],
                }
            },
        )
    if accepts_html:
        return _github_oauth_result_redirect("connected")
    return JSONResponse(status_code=200, content=status.to_payload())


@router.get("/github/auth/status", response_model=None)
def get_github_auth_status(
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
    github_auth_service: GitHubOAuthService = Depends(get_github_auth_service),
) -> JSONResponse:
    server_config = _load_server_config(services.default_config_path)
    try:
        status = github_auth_service.get_status(server_config.github)
    except GitHubAuthError as exc:
        logger.warning("GitHub OAuth 状态查询失败: %s", exc)
        return JSONResponse(
            status_code=200,
            content={
                "provider": "github-oauth-app",
                "connected": False,
                "requiresReauth": True,
                "message": "GitHub 授权状态不可用，请重新授权。",
            },
        )
    return JSONResponse(status_code=200, content=status.to_payload())


@router.post("/github/auth/disconnect", response_model=None)
def disconnect_github_auth(
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
    github_auth_service: GitHubOAuthService = Depends(get_github_auth_service),
) -> JSONResponse:
    server_config = _load_server_config(services.default_config_path)
    github_auth_service.disconnect(server_config.github)
    return JSONResponse(
        status_code=200,
        content={
            "provider": "github-oauth-app",
            "connected": False,
            "requiresReauth": False,
            "message": "已断开 GitHub 连接。",
        },
    )


@router.get(
    "/github/repositories",
    response_model=GitHubRepositoriesResponse,
    responses={401: {"model": ErrorResponse}},
)
def get_github_repositories(
    services: AppServices = Depends(get_app_services),
    _admin: AuthenticatedUser = Depends(require_admin),
    github_auth_service: GitHubOAuthService = Depends(get_github_auth_service),
) -> GitHubRepositoriesResponse | JSONResponse:
    server_config = _load_server_config(services.default_config_path)
    try:
        repositories = github_auth_service.list_repositories(server_config.github)
    except GitHubAuthError as exc:
        logger.warning("GitHub 仓库列表获取失败: %s", exc)
        return _error_response(
            401,
            code="github_auth_required",
            message="GitHub 授权不可用，请重新授权。",
            field_errors=[],
        )
    return GitHubRepositoriesResponse(
        repositories=[
            GitHubRepositoryEntry.model_validate(repository.to_payload())
            for repository in repositories
        ]
    )


@router.get(
    "/runs/history",
    response_model=RunHistoryResponse,
)
def get_run_history(
    run_service: RunLifecycleService = Depends(get_run_service),
    user: AuthenticatedUser = Depends(require_user),
) -> RunHistoryResponse:
    return RunHistoryResponse(
        items=[
            RunHistoryEntry.model_validate(item)
            for item in _visible_run_history_items(run_service, user)
        ]
    )


@router.get(
    "/runs/current",
    response_model=RunSnapshotResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_current_run(
    run_service: RunLifecycleService = Depends(get_run_service),
    user: AuthenticatedUser = Depends(require_user),
) -> RunSnapshotResponse | JSONResponse:
    run_id = run_service.active_run_id()
    if run_id is None:
        return _error_response(
            404,
            code="no_active_run",
            message="No active run exists.",
            field_errors=[],
        )
    if _visible_run_session(run_service, run_id, user) is None:
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
    user: AuthenticatedUser = Depends(require_user),
) -> RunSnapshotResponse | JSONResponse:
    session = _load_visible_run_session(run_service, run_id, user)
    if session is None:
        return _run_not_found_response(run_id)
    snapshot = run_service.build_snapshot(run_id)
    return RunSnapshotResponse.model_validate(snapshot)


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunSnapshotResponse,
    responses={404: {"model": ErrorResponse}},
)
def cancel_run(
    run_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
    user: AuthenticatedUser = Depends(require_user),
) -> RunSnapshotResponse | JSONResponse:
    session = _load_visible_run_session(run_service, run_id, user)
    if session is None:
        return _run_not_found_response(run_id)
    run_service.cancel_run(run_id)
    return RunSnapshotResponse.model_validate(run_service.build_snapshot(run_id))


@router.get("/runs/{run_id}/events", response_model=None)
async def get_run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    run_service: RunLifecycleService = Depends(get_run_service),
    user: AuthenticatedUser = Depends(require_user),
) -> StreamingResponse | JSONResponse:
    session = _load_visible_run_session(run_service, run_id, user)
    if session is None:
        return _run_not_found_response(run_id)
    event_bus = run_service.get_event_bus(run_id)

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
    user: AuthenticatedUser = Depends(require_user),
) -> JSONResponse:
    session = _load_visible_run_session(run_service, run_id, user)
    if session is None:
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
    user: AuthenticatedUser = Depends(require_user),
) -> JSONResponse:
    session = _load_visible_run_session(run_service, run_id, user)
    if session is None:
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
    user: AuthenticatedUser = Depends(require_user),
) -> RunResultsResponse | JSONResponse:
    session = _load_visible_run_session(run_service, run_id, user)
    if session is None:
        return _run_not_found_response(run_id)
    try:
        payload = run_service.build_results(run_id)
    except KeyError:
        return _run_not_found_response(run_id)
    return RunResultsResponse.model_validate(payload)


def _download_artifact_response(
    run_id: str,
    artifact_slug: str,
    run_service: RunLifecycleService,
    user: AuthenticatedUser,
) -> FileResponse | JSONResponse:
    session = _load_visible_run_session(run_service, run_id, user)
    if session is None:
        return _run_not_found_response(run_id)
    try:
        artifact = run_service.get_download_artifact(run_id, artifact_slug)
    except KeyError:
        return _artifact_not_found_response(run_id, artifact_slug)
    if not artifact["exists"]:
        return _artifact_not_found_response(run_id, artifact_slug)
    return FileResponse(
        artifact["filePath"],
        media_type=artifact["contentType"],
        filename=artifact["filename"],
    )


@router.get(
    "/runs/{run_id}/artifacts/final-state",
    response_model=None,
    responses={404: {"model": ErrorResponse}},
)
def download_final_state_artifact(
    run_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
    user: AuthenticatedUser = Depends(require_user),
) -> FileResponse | JSONResponse:
    return _download_artifact_response(run_id, "final-state", run_service, user)


@router.get(
    "/runs/{run_id}/artifacts/run-log",
    response_model=None,
    responses={404: {"model": ErrorResponse}},
)
def download_run_log_artifact(
    run_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
    user: AuthenticatedUser = Depends(require_user),
) -> FileResponse | JSONResponse:
    return _download_artifact_response(run_id, "run-log", run_service, user)


@router.get(
    "/runs/{run_id}/artifacts/report",
    response_model=None,
    responses={404: {"model": ErrorResponse}},
)
def download_report_artifact(
    run_id: str,
    run_service: RunLifecycleService = Depends(get_run_service),
    user: AuthenticatedUser = Depends(require_user),
) -> FileResponse | JSONResponse:
    return _download_artifact_response(run_id, "report", run_service, user)


@router.get(
    "/runs/{run_id}/artifacts/{artifact_slug:path}",
    response_model=None,
    responses={404: {"model": ErrorResponse}},
)
def download_named_artifact(
    run_id: str,
    artifact_slug: str,
    run_service: RunLifecycleService = Depends(get_run_service),
    user: AuthenticatedUser = Depends(require_user),
) -> FileResponse | JSONResponse:
    return _download_artifact_response(run_id, artifact_slug, run_service, user)
