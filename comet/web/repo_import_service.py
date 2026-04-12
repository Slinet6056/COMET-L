from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, cast
from urllib.parse import urlparse

import httpx

from comet.config.settings import GitHubConfig

from .github_auth_service import GitHubAuthError, GitHubOAuthService

logger = logging.getLogger(__name__)

_GITHUB_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class RepoImportError(RuntimeError):
    """仓库导入失败。"""


class RepoImportUrlError(RepoImportError):
    """GitHub URL 非法。"""


class RepoImportPermissionError(RepoImportError):
    """仓库访问权限不足。"""


class RepoImportCloneError(RepoImportError):
    """Git clone 失败。"""


class RepoImportBranchResolutionError(RepoImportError):
    """默认分支解析失败。"""


class RepoImportNonMavenError(RepoImportError):
    """导入仓库不是 Maven 项目。"""


@dataclass(slots=True)
class ImportedRepository:
    project_path: str
    owner: str
    repo: str
    base_branch: str


@dataclass(slots=True)
class _RepoIdentity:
    owner: str
    repo: str


class GitHubRepoImportService:
    def __init__(
        self,
        github_auth_service: GitHubOAuthService,
        *,
        default_clone_depth: int = 1,
        subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        http_client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._github_auth_service: GitHubOAuthService = github_auth_service
        self._default_clone_depth: int = default_clone_depth
        self._subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = (
            subprocess_runner
        )
        fallback_http_client_factory = getattr(github_auth_service, "_http_client_factory", None)
        self._http_client_factory: Callable[[], httpx.Client]
        if http_client_factory is not None:
            self._http_client_factory = http_client_factory
        elif callable(fallback_http_client_factory):
            self._http_client_factory = cast(
                Callable[[], httpx.Client],
                fallback_http_client_factory,
            )
        else:
            self._http_client_factory = lambda: httpx.Client(timeout=10.0)

    def import_repository(
        self,
        *,
        run_id: str,
        github_repo_url: str,
        github_config: GitHubConfig,
        requested_base_branch: str | None,
    ) -> ImportedRepository:
        identity = self._parse_repo_url(github_repo_url)
        token = self._load_access_token(github_config)
        clone_root = Path(github_config.managed_clone_root).expanduser().resolve()
        clone_path = self._build_clone_path(clone_root, identity, run_id)
        base_branch = self._resolve_base_branch(
            identity=identity,
            requested_base_branch=requested_base_branch,
            token=token,
            github_config=github_config,
        )

        try:
            self._clone_repository(
                identity=identity,
                token=token,
                clone_path=clone_path,
                base_branch=base_branch,
                depth=self._default_clone_depth,
            )
            self._assert_maven_project(clone_path)
            self._cleanup_test_directories(clone_path)
        except Exception:
            if clone_path.exists():
                shutil.rmtree(clone_path, ignore_errors=True)
            raise

        return ImportedRepository(
            project_path=str(clone_path),
            owner=identity.owner,
            repo=identity.repo,
            base_branch=base_branch,
        )

    def _parse_repo_url(self, raw_url: str) -> _RepoIdentity:
        repo_url = raw_url.strip()
        parsed = urlparse(repo_url)
        path_parts = [segment for segment in parsed.path.split("/") if segment]
        if parsed.scheme != "https" or parsed.netloc.lower() not in {
            "github.com",
            "www.github.com",
        }:
            raise RepoImportUrlError("仓库地址必须是 GitHub HTTPS 地址。")
        if len(path_parts) != 2:
            raise RepoImportUrlError("仓库地址必须是 https://github.com/<owner>/<repo> 格式。")

        owner = path_parts[0].strip()
        repo = path_parts[1].strip()
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not owner or not repo:
            raise RepoImportUrlError("仓库地址缺少 owner 或 repo。")
        if not _GITHUB_SEGMENT_PATTERN.fullmatch(owner) or not _GITHUB_SEGMENT_PATTERN.fullmatch(
            repo
        ):
            raise RepoImportUrlError("仓库地址包含非法字符。")

        return _RepoIdentity(owner=owner, repo=repo)

    def _load_access_token(self, github_config: GitHubConfig) -> str:
        try:
            token = self._github_auth_service.get_access_token(github_config)
        except GitHubAuthError as exc:
            raise RepoImportPermissionError(str(exc)) from exc
        normalized = token.strip()
        if not normalized:
            raise RepoImportPermissionError("未检测到有效的 GitHub 访问令牌，请重新授权。")
        return normalized

    def _build_clone_path(self, clone_root: Path, identity: _RepoIdentity, run_id: str) -> Path:
        clone_root.mkdir(parents=True, exist_ok=True)
        candidate = (clone_root / identity.owner / identity.repo / run_id).resolve()
        if not candidate.is_relative_to(clone_root):
            raise RepoImportError("导入目录越界，已拒绝该请求。")
        if candidate.exists():
            shutil.rmtree(candidate)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def _resolve_base_branch(
        self,
        *,
        identity: _RepoIdentity,
        requested_base_branch: str | None,
        token: str,
        github_config: GitHubConfig,
    ) -> str:
        if requested_base_branch and requested_base_branch.strip():
            return requested_base_branch.strip()

        fallback_branch = "main"
        api_url = f"{github_config.oauth_api_base_url}/repos/{identity.owner}/{identity.repo}"
        try:
            with self._http_client_factory() as http_client:
                response = http_client.get(
                    api_url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {token}",
                    },
                )
        except httpx.HTTPError as exc:
            logger.warning("查询 GitHub 默认分支失败，回退 main: %s", exc)
            return fallback_branch

        if response.status_code == 200:
            payload = response.json()
            default_branch = str(payload.get("default_branch", "")).strip()
            if default_branch:
                return default_branch
            raise RepoImportBranchResolutionError("GitHub 返回的默认分支为空，无法继续导入。")

        if response.status_code in {401, 403}:
            raise RepoImportPermissionError("无权限访问该仓库，请检查 GitHub 授权范围。")
        if response.status_code == 404:
            raise RepoImportPermissionError("仓库不存在或当前账号无访问权限。")

        logger.warning(
            "查询默认分支返回异常状态 %s，回退 main。",
            response.status_code,
        )
        return fallback_branch

    def _clone_repository(
        self,
        *,
        identity: _RepoIdentity,
        token: str,
        clone_path: Path,
        base_branch: str,
        depth: int,
    ) -> None:
        clean_repo_url = f"https://github.com/{identity.owner}/{identity.repo}.git"
        clone_env = self._build_clone_env(token)
        command = [
            "git",
            "clone",
            "--depth",
            str(depth),
            "--single-branch",
            "--branch",
            base_branch,
            clean_repo_url,
            str(clone_path),
        ]
        subprocess_runner = self._subprocess_runner or subprocess.run
        try:
            result = subprocess_runner(
                command,
                capture_output=True,
                text=True,
                check=False,
                env=clone_env,
            )
        except FileNotFoundError as exc:
            raise RepoImportCloneError("系统缺少 git 可执行文件，无法克隆仓库。") from exc
        if result.returncode == 0:
            return

        stderr = self._sanitize_clone_error(result.stderr or result.stdout or "")
        lower_error = stderr.lower()
        if any(
            keyword in lower_error for keyword in ["authentication failed", "permission denied"]
        ):
            raise RepoImportPermissionError("无权限克隆该仓库，请检查授权后重试。")
        if "remote branch" in lower_error and "not found" in lower_error:
            raise RepoImportBranchResolutionError(f"默认分支不可用，无法检出分支: {base_branch}。")
        raise RepoImportCloneError(f"仓库克隆失败: {stderr or '未知错误'}")

    @staticmethod
    def _build_clone_env(token: str) -> dict[str, str]:
        auth_pair = f"x-access-token:{token}".encode("utf-8")
        auth_token = base64.b64encode(auth_pair).decode("ascii")
        clone_env = os.environ.copy()
        clone_env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth_token}",
            }
        )
        return clone_env

    @staticmethod
    def _sanitize_clone_error(raw_error: str) -> str:
        return re.sub(r"x-access-token:[^@]+@", "x-access-token:***@", raw_error).strip()

    @staticmethod
    def _assert_maven_project(project_root: Path) -> None:
        if not (project_root / "pom.xml").is_file():
            raise RepoImportNonMavenError("导入仓库不是 Maven 项目，缺少 pom.xml。")

    @staticmethod
    def _cleanup_test_directories(project_root: Path) -> None:
        for relative in [Path("src/test/java"), Path("src/test/resources")]:
            target = (project_root / relative).resolve()
            if not target.is_relative_to(project_root.resolve()):
                raise RepoImportError("测试目录清理路径越界，已中止导入。")
            if not target.exists():
                continue
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
