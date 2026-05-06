from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from comet.config import Settings

from .git_pr_service import GitHubPullRequestService
from .github_auth_service import GitHubOAuthService
from .repo_import_service import GitHubRepoImportService
from .routes import (
    SESSION_COOKIE_NAME,
    ApiErrorException,
    AppServices,
    api_exception_response,
    router,
)
from .run_service import RunLifecycleService
from .storage import WebDatabase

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_app(
    *,
    run_service: RunLifecycleService | None = None,
    github_auth_service: GitHubOAuthService | None = None,
    default_config_path: Path | None = None,
    frontend_dist_path: Path | None = None,
    system_initializer: Callable[..., dict[str, object]] | None = None,
    evolution_runner: Callable[..., None] | None = None,
) -> FastAPI:
    repo_root = Path(__file__).resolve().parents[2]
    app = FastAPI(title="COMET-L Web API")
    dist_path = frontend_dist_path or repo_root / "web" / "dist"
    resolved_dist_path = dist_path.resolve()
    resolved_github_auth_service = github_auth_service or GitHubOAuthService()
    resolved_run_service = run_service or RunLifecycleService(workspace_root=repo_root)
    web_database = WebDatabase.for_workspace(resolved_run_service.workspace_root)
    web_database.bootstrap()
    resolved_run_service.set_web_database(web_database)
    resolved_run_service.set_repo_import_service(
        GitHubRepoImportService(github_auth_service=resolved_github_auth_service)
    )
    resolved_run_service.set_pull_request_service(
        GitHubPullRequestService(github_auth_service=resolved_github_auth_service)
    )
    app.state.services = AppServices(
        run_service=resolved_run_service,
        github_auth_service=resolved_github_auth_service,
        default_config_path=default_config_path or default_config_path_for_repo_root(repo_root),
        system_initializer=system_initializer,
        evolution_runner=evolution_runner,
        web_database=web_database,
    )

    @app.exception_handler(ApiErrorException)
    def handle_api_error_exception(
        _request: Request,
        exc: ApiErrorException,
    ) -> JSONResponse:
        return api_exception_response(exc)

    @app.middleware("http")
    async def reject_cross_origin_cookie_mutations(
        request: Request,
        call_next: Callable[[Request], Awaitable[JSONResponse]],
    ) -> JSONResponse:
        if _requires_origin_guard(request):
            settings = Settings.from_yaml(str(app.state.services.default_config_path))
            allowed_origins = _allowed_request_origins(request, settings)
            request_origin = _request_origin_or_referer(request)
            if request_origin is None or request_origin not in allowed_origins:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "csrf_origin_forbidden",
                            "message": "请求来源未被允许。",
                            "fieldErrors": [],
                        }
                    },
                )
        return await call_next(request)

    app.include_router(router)
    if (resolved_dist_path / "index.html").is_file():
        assets_path = resolved_dist_path / "assets"
        if assets_path.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_path)),
                name="web-assets",
            )

        def _resolve_frontend_path(full_path: str) -> Path:
            relative_path = full_path.strip("/")
            index_path = resolved_dist_path / "index.html"
            candidate = resolved_dist_path / relative_path if relative_path else index_path
            try:
                resolved_candidate = candidate.resolve()
            except OSError:
                return index_path
            if resolved_candidate.is_file() and resolved_candidate.is_relative_to(
                resolved_dist_path
            ):
                return resolved_candidate
            return index_path

        @app.get("/", include_in_schema=False)
        def serve_frontend_index() -> FileResponse:
            return FileResponse(_resolve_frontend_path(""))

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_frontend_app(full_path: str) -> FileResponse:
            return FileResponse(_resolve_frontend_path(full_path))

    return app


def default_config_path_for_repo_root(repo_root: Path) -> Path:
    return repo_root / "config.yaml"


class _LazyApp:
    def __init__(self) -> None:
        self._app: FastAPI | None = None

    def _resolve_app(self) -> FastAPI:
        if self._app is None:
            self._app = create_app()
        return self._app

    async def __call__(self, scope: dict[str, object], receive, send) -> None:
        await self._resolve_app()(scope, receive, send)


def _requires_origin_guard(request: Request) -> bool:
    return (
        request.url.path.startswith("/api/")
        and request.method.upper() not in SAFE_METHODS
        and SESSION_COOKIE_NAME in request.cookies
    )


def _request_origin_or_referer(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if not referer:
        return None
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _allowed_request_origins(request: Request, settings: Settings) -> set[str]:
    host_origin = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
    return {host_origin, *settings.deployment.allowed_origins}


app = _LazyApp()
