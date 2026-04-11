import json
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from comet.config.settings import GitHubConfig
from comet.web.git_pr_service import GitHubPullRequestService, GitPullRequestError
from comet.web.github_auth_service import GitHubOAuthService


class _AlwaysAuthorizedGitHubOAuthService(GitHubOAuthService):
    def get_access_token(self, github_config: Any) -> str:
        del github_config
        return "gho-test-valid"


@dataclass(slots=True)
class _FakeGitRunner:
    status_output: str
    fail_push: bool = False
    branch_exists_once: bool = False
    staged_files: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)

    def __call__(
        self,
        command: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check
        self.commands.append(command)
        git_args = command[1:]

        if git_args == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, stdout=self.status_output, stderr="")

        if git_args[:3] == ["show-ref", "--verify", "--quiet"]:
            if self.branch_exists_once:
                self.branch_exists_once = False
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

        if git_args[:3] == ["ls-remote", "--heads", "origin"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        if git_args[:2] == ["checkout", "-b"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        if git_args and git_args[0] == "add":
            self.staged_files = list(git_args[2:])
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        if git_args == ["diff", "--cached", "--name-only"]:
            stdout = "\n".join(self.staged_files)
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        if git_args[:2] == ["commit", "-m"]:
            return subprocess.CompletedProcess(command, 0, stdout="[test] commit ok", stderr="")

        if git_args[:3] == ["push", "-u", "origin"]:
            if self.fail_push:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="push denied")
            return subprocess.CompletedProcess(command, 0, stdout="push ok", stderr="")

        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class GitHubPullRequestServiceTests(unittest.TestCase):
    def _build_github_config(self) -> GitHubConfig:
        return GitHubConfig.model_validate(
            {
                "encrypted_token_store_path": "./state/github/auth/token.enc",
                "encrypted_key_store_path": "./state/github/auth/token.key",
                "managed_clone_root": "./sandbox/github-managed",
                "oauth_api_base_url": "https://api.github.com",
            }
        )

    def test_commit_and_pr_success_uses_report_as_pr_body(self) -> None:
        pr_calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            pr_calls.append(
                {
                    "url": str(request.url),
                    "json": request.read().decode("utf-8"),
                }
            )
            return httpx.Response(201, json={"html_url": "https://github.com/openai/demo/pull/9"})

        git_runner = _FakeGitRunner(
            status_output=(
                " M src/test/java/com/demo/CalculatorTest.java\n"
                "?? src/test/resources/fixture/input.json\n"
                " M README.md\n"
            )
        )
        service = GitHubPullRequestService(
            github_auth_service=_AlwaysAuthorizedGitHubOAuthService(),
            subprocess_runner=git_runner,
            http_client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(handler), timeout=10.0
            ),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / ".git").mkdir()
            report_path = project_root / "report.md"
            report_content = "# COMET-L 报告\n\n这是报告正文。\n"
            report_path.write_text(report_content, encoding="utf-8")

            result = service.commit_generated_tests_and_create_pr(
                run_id="run-20260411-abc",
                project_path=project_root,
                report_path=report_path,
                repo_url="https://github.com/openai/demo",
                base_branch="main",
                github_config=self._build_github_config(),
            )

        self.assertEqual(result.pull_request_url, "https://github.com/openai/demo/pull/9")
        self.assertEqual(len(pr_calls), 1)
        request_payload = json.loads(pr_calls[0]["json"])
        self.assertEqual(request_payload["title"], "test: add COMET-L generated tests")
        self.assertEqual(request_payload["base"], "main")
        self.assertEqual(request_payload["body"], report_content)
        self.assertEqual(
            git_runner.staged_files,
            [
                "src/test/java/com/demo/CalculatorTest.java",
                "src/test/resources/fixture/input.json",
            ],
        )

    def test_push_failure_stops_before_pr_creation(self) -> None:
        pr_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            pr_calls.append(str(request.url))
            return httpx.Response(201, json={"html_url": "https://github.com/openai/demo/pull/9"})

        git_runner = _FakeGitRunner(
            status_output="?? src/test/java/com/demo/CalculatorTest.java\n",
            fail_push=True,
        )
        service = GitHubPullRequestService(
            github_auth_service=_AlwaysAuthorizedGitHubOAuthService(),
            subprocess_runner=git_runner,
            http_client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(handler), timeout=10.0
            ),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / ".git").mkdir()
            report_path = project_root / "report.md"
            report_path.write_text("# report", encoding="utf-8")

            with self.assertRaisesRegex(GitPullRequestError, "推送提交到远端失败"):
                service.commit_generated_tests_and_create_pr(
                    run_id="run-20260411-abc",
                    project_path=project_root,
                    report_path=report_path,
                    repo_url="https://github.com/openai/demo",
                    base_branch="main",
                    github_config=self._build_github_config(),
                )

        self.assertEqual(pr_calls, [])


if __name__ == "__main__":
    unittest.main()
