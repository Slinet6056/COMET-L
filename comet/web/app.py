from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes import AppServices, router
from .run_service import RunLifecycleService


def create_app(
    *,
    run_service: RunLifecycleService | None = None,
    default_config_path: Path | None = None,
    frontend_dist_path: Path | None = None,
    system_initializer: Callable[..., dict[str, object]] | None = None,
    evolution_runner: Callable[..., None] | None = None,
) -> FastAPI:
    repo_root = Path(__file__).resolve().parents[2]
    app = FastAPI(title="COMET-L Web API")
    dist_path = frontend_dist_path or repo_root / "web" / "dist"
    app.state.services = AppServices(
        run_service=run_service or RunLifecycleService(workspace_root=repo_root),
        default_config_path=default_config_path or repo_root / "config.example.yaml",
        system_initializer=system_initializer,
        evolution_runner=evolution_runner,
    )
    app.include_router(router)
    if (dist_path / "index.html").is_file():
        assets_path = dist_path / "assets"
        if assets_path.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_path)),
                name="web-assets",
            )

        def _resolve_frontend_path(full_path: str) -> Path:
            relative_path = full_path.strip("/")
            candidate = (
                dist_path / relative_path if relative_path else dist_path / "index.html"
            )
            if candidate.is_file():
                return candidate
            return dist_path / "index.html"

        @app.get("/", include_in_schema=False)
        def serve_frontend_index() -> FileResponse:
            return FileResponse(_resolve_frontend_path(""))

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_frontend_app(full_path: str) -> FileResponse:
            return FileResponse(_resolve_frontend_path(full_path))

    return app


app = create_app()
