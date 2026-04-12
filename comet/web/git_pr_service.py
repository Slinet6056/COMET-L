from __future__ import annotations

import os
import re
import secrets
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, cast
from urllib.parse import urlparse

import httpx

from comet.config.settings import GitHubConfig

from .github_auth_service import GitHubAuthError, GitHubOAuthService

_GITHUB_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_ALLOWED_STAGE_PREFIXES = ("src/test/java/", "src/test/resources/")
_COMMIT_MESSAGE = "test: add COMET-L generated tests"
_PR_TITLE = "test: add COMET-L generated tests"


class GitPullRequestError(RuntimeError):
    """自动提交并创建 PR 失败。"""


@dataclass(slots=True)
class GitPullRequestResult:
    branch_name: str
    pull_request_url: str


@dataclass(slots=True)
class _RepoIdentity:
    owner: str
    repo: str


class GitHubPullRequestService:
    def __init__(
        self,
        github_auth_service: GitHubOAuthService,
        *,
        subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        http_client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._github_auth_service: GitHubOAuthService = github_auth_service
        self._subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess_runner
        fallback_http_client_factory = getattr(github_auth_service, "_http_client_factory", None)
        self._http_client_factory: Callable[[], httpx.Client]
        if http_client_factory is not None:
            self._http_client_factory = http_client_factory
        elif callable(fallback_http_client_factory):
            self._http_client_factory = cast(
                Callable[[], httpx.Client], fallback_http_client_factory
            )
        else:
            self._http_client_factory = lambda: httpx.Client(timeout=10.0)

    def commit_generated_tests_and_create_pr(
        self,
        *,
        run_id: str,
        project_path: Path,
        report_path: Path,
        repo_url: str,
        base_branch: str,
        github_config: GitHubConfig,
    ) -> GitPullRequestResult:
        resolved_project_path = project_path.expanduser().resolve()
        if not (resolved_project_path / ".git").is_dir():
            raise GitPullRequestError("受管仓库目录缺少 .git，无法自动提交并创建 PR。")

        if not report_path.exists():
            raise GitPullRequestError("未找到 report.md，无法创建 PR。")

        report_body = report_path.read_text(encoding="utf-8")
        if not report_body:
            raise GitPullRequestError("report.md 内容为空，无法创建 PR。")

        identity = self._parse_repo_url(repo_url)
        normalized_base_branch = base_branch.strip()
        if not normalized_base_branch:
            raise GitPullRequestError("缺少基线分支信息，无法创建 PR。")

        token = self._load_access_token(github_config)
        status_lines = self._collect_git_status_lines(resolved_project_path)
        stage_candidates = self._collect_stage_candidates(status_lines)
        if not stage_candidates:
            raise GitPullRequestError("未检测到可提交的测试源码或测试资源文件，已跳过自动 PR。")

        branch_name = self._create_unique_branch(run_id, resolved_project_path)
        _ = self._run_git_command(
            ["checkout", "-b", branch_name],
            cwd=resolved_project_path,
            error_message="创建提交分支失败。",
        )

        _ = self._run_git_command(
            ["add", "--", *stage_candidates],
            cwd=resolved_project_path,
            error_message="暂存生成测试文件失败。",
        )

        staged_files = self._collect_staged_files(resolved_project_path)
        if not staged_files:
            raise GitPullRequestError("暂存区为空，无法创建提交。")

        _ = self._run_git_command(
            ["commit", "-m", _COMMIT_MESSAGE],
            cwd=resolved_project_path,
            error_message="创建 Git 提交失败。",
        )

        authed_remote_url = (
            f"https://x-access-token:{token}@github.com/{identity.owner}/{identity.repo}.git"
        )
        _ = self._run_git_command(
            ["push", "-u", authed_remote_url, branch_name],
            cwd=resolved_project_path,
            error_message="推送提交到远端失败，请检查 GitHub 权限或网络连接。",
        )

        pull_request_url = self._create_pull_request(
            identity=identity,
            branch_name=branch_name,
            base_branch=normalized_base_branch,
            report_body=report_body,
            token=token,
            github_config=github_config,
        )
        return GitPullRequestResult(branch_name=branch_name, pull_request_url=pull_request_url)

    def _collect_git_status_lines(self, project_path: Path) -> list[str]:
        result = self._run_git_command(
            ["status", "--porcelain", "-uall"],
            cwd=project_path,
            error_message="检查仓库 Git 状态失败。",
        )
        lines = [line.rstrip("\n") for line in result.stdout.splitlines() if line.strip()]
        return lines

    def _collect_stage_candidates(self, status_lines: list[str]) -> list[str]:
        candidates: list[str] = []
        for line in status_lines:
            if len(line) < 4:
                continue
            status = line[:2]
            raw_path = line[3:].strip()
            if not raw_path:
                continue
            if " -> " in raw_path:
                raw_path = raw_path.split(" -> ", 1)[1].strip()

            normalized_path = raw_path.strip('"')
            if "D" in status:
                continue
            if not normalized_path.startswith(_ALLOWED_STAGE_PREFIXES):
                continue
            candidates.append(normalized_path)

        unique_candidates: list[str] = []
        for path in candidates:
            if path not in unique_candidates:
                unique_candidates.append(path)
        return unique_candidates

    def _create_unique_branch(self, run_id: str, project_path: Path) -> str:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        base_name = f"comet-l/{run_id}-{timestamp}"
        candidate = base_name

        for attempt in range(8):
            if not self._branch_exists(project_path, candidate):
                return candidate
            if attempt == 0:
                candidate = f"{base_name}-{secrets.token_hex(2)}"
            else:
                candidate = f"{base_name}-{secrets.token_hex(3)}"

        raise GitPullRequestError("分支名冲突过多，无法生成可用分支名。")

    def _branch_exists(self, project_path: Path, branch_name: str) -> bool:
        local_exists = (
            self._run_git_command(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                cwd=project_path,
                error_message="检查本地分支失败。",
                check=False,
            ).returncode
            == 0
        )
        if local_exists:
            return True

        remote_check = self._run_git_command(
            ["ls-remote", "--heads", "origin", f"refs/heads/{branch_name}"],
            cwd=project_path,
            error_message="检查远端分支失败。",
            check=False,
        )
        if remote_check.returncode != 0:
            raise GitPullRequestError("检查远端分支失败，请确认仓库远端配置有效。")
        return bool(remote_check.stdout.strip())

    def _collect_staged_files(self, project_path: Path) -> list[str]:
        result = self._run_git_command(
            ["diff", "--cached", "--name-only"],
            cwd=project_path,
            error_message="检查暂存区失败。",
        )
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        allowed = [path for path in files if path.startswith(_ALLOWED_STAGE_PREFIXES)]
        return allowed

    def _create_pull_request(
        self,
        *,
        identity: _RepoIdentity,
        branch_name: str,
        base_branch: str,
        report_body: str,
        token: str,
        github_config: GitHubConfig,
    ) -> str:
        url = f"{github_config.oauth_api_base_url}/repos/{identity.owner}/{identity.repo}/pulls"
        payload = {
            "title": _PR_TITLE,
            "head": branch_name,
            "base": base_branch,
            "body": report_body,
        }

        with self._http_client_factory() as http_client:
            response = http_client.post(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                },
                json=payload,
            )

        if response.status_code != 201:
            message = self._extract_github_error_message(response)
            raise GitPullRequestError(f"创建 GitHub PR 失败: {message}")

        pr_url = str(response.json().get("html_url", "")).strip()
        if not pr_url:
            raise GitPullRequestError("创建 GitHub PR 失败: 响应缺少 PR 地址。")
        return pr_url

    def _load_access_token(self, github_config: GitHubConfig) -> str:
        try:
            token = self._github_auth_service.get_access_token(github_config)
        except GitHubAuthError as exc:
            raise GitPullRequestError(str(exc)) from exc
        normalized_token = token.strip()
        if not normalized_token:
            raise GitPullRequestError("GitHub 授权令牌为空，无法创建 PR。")
        return normalized_token

    @staticmethod
    def _parse_repo_url(repo_url: str) -> _RepoIdentity:
        parsed = urlparse(repo_url.strip())
        path_parts = [segment for segment in parsed.path.split("/") if segment]
        if parsed.scheme != "https" or parsed.netloc.lower() not in {
            "github.com",
            "www.github.com",
        }:
            raise GitPullRequestError("仓库地址必须是 GitHub HTTPS 地址。")
        if len(path_parts) != 2:
            raise GitPullRequestError("仓库地址必须是 https://github.com/<owner>/<repo> 格式。")

        owner = path_parts[0].strip()
        repo = path_parts[1].strip()
        if repo.endswith(".git"):
            repo = repo[:-4]

        if not owner or not repo:
            raise GitPullRequestError("仓库地址缺少 owner 或 repo。")
        if not _GITHUB_SEGMENT_PATTERN.fullmatch(owner) or not _GITHUB_SEGMENT_PATTERN.fullmatch(
            repo
        ):
            raise GitPullRequestError("仓库地址包含非法字符。")
        return _RepoIdentity(owner=owner, repo=repo)

    @staticmethod
    def _extract_github_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"HTTP {response.status_code}"

        if isinstance(payload, dict):
            message = str(payload.get("message", "")).strip()
            if message:
                return f"HTTP {response.status_code} - {message}"
        return f"HTTP {response.status_code}"

    def _run_git_command(
        self,
        args: list[str],
        *,
        cwd: Path,
        error_message: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._subprocess_runner(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitPullRequestError(error_message) from exc

        if check and result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            if stderr:
                raise GitPullRequestError(f"{error_message} {stderr}")
            raise GitPullRequestError(error_message)
        return result
