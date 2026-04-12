import errno
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from comet.config.settings import GitHubConfig
from comet.web.github_auth_service import GitHubOAuthService
from comet.web.repo_import_service import GitHubRepoImportService, RepoImportCloneError


class _AlwaysAuthorizedGitHubOAuthService(GitHubOAuthService):
    def get_access_token(self, github_config: Any) -> str:
        del github_config
        return "gho-plain-secret-token"


class GitHubRepoImportServiceTests(unittest.TestCase):
    def _build_github_config(self, managed_clone_root: str) -> GitHubConfig:
        return GitHubConfig.model_validate(
            {
                "encrypted_token_store_path": "./state/github/auth/token.enc",
                "encrypted_key_store_path": "./state/github/auth/token.key",
                "managed_clone_root": managed_clone_root,
                "oauth_api_base_url": "https://api.github.com",
            }
        )

    def test_clone_does_not_persist_plain_token_in_git_config(self) -> None:
        captured_command: list[str] = []
        captured_env: dict[str, str] = {}

        def fake_subprocess_runner(
            command: list[str],
            capture_output: bool,
            text: bool,
            check: bool,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del capture_output, text, check
            captured_command.extend(command)
            captured_env.update(env)

            clone_path = Path(command[-1])
            clone_path.mkdir(parents=True, exist_ok=True)
            git_dir = clone_path / ".git"
            git_dir.mkdir(parents=True, exist_ok=True)
            (clone_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            remote_url = command[-2]
            (git_dir / "config").write_text(
                '[remote "origin"]\n'
                f"\turl = {remote_url}\n"
                "\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp_dir:
            managed_root = Path(tmp_dir) / "managed"
            service = GitHubRepoImportService(
                github_auth_service=_AlwaysAuthorizedGitHubOAuthService(),
                subprocess_runner=fake_subprocess_runner,
            )

            imported = service.import_repository(
                run_id="run-001",
                github_repo_url="https://github.com/openai/demo",
                github_config=self._build_github_config(str(managed_root)),
                requested_base_branch="main",
            )

            imported_path = Path(imported.project_path)
            git_config_text = (imported_path / ".git" / "config").read_text(encoding="utf-8")

        self.assertIn("git", captured_command[0])
        self.assertNotIn("-c", captured_command)
        self.assertNotIn("AUTHORIZATION", " ".join(captured_command))
        self.assertNotIn("gho-plain-secret-token", " ".join(captured_command))
        self.assertNotIn("x-access-token:gho-plain-secret-token@", " ".join(captured_command))
        self.assertEqual(captured_env["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
        self.assertIn("AUTHORIZATION: basic", captured_env["GIT_CONFIG_VALUE_0"])
        self.assertIn("https://github.com/openai/demo.git", git_config_text)
        self.assertNotIn("gho-plain-secret-token", git_config_text)

    def test_default_clone_runner_resolves_subprocess_run_at_runtime(self) -> None:
        captured_command: list[str] = []

        def fake_subprocess_run(
            command: list[str],
            capture_output: bool,
            text: bool,
            check: bool,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del capture_output, text, check, env
            captured_command.extend(command)
            clone_path = Path(command[-1])
            clone_path.mkdir(parents=True, exist_ok=True)
            (clone_path / ".git").mkdir(parents=True, exist_ok=True)
            (clone_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp_dir:
            managed_root = Path(tmp_dir) / "managed"
            service = GitHubRepoImportService(
                github_auth_service=_AlwaysAuthorizedGitHubOAuthService(),
            )

            with patch(
                "comet.web.repo_import_service.subprocess.run", side_effect=fake_subprocess_run
            ):
                imported = service.import_repository(
                    run_id="run-002",
                    github_repo_url="https://github.com/openai/demo",
                    github_config=self._build_github_config(str(managed_root)),
                    requested_base_branch="main",
                )

        self.assertEqual(Path(imported.project_path).name, "run-002")
        self.assertEqual(captured_command[0], "git")

    def test_clone_reports_missing_git_binary_as_clone_error(self) -> None:
        def fake_subprocess_runner(
            command: list[str],
            capture_output: bool,
            text: bool,
            check: bool,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del command, capture_output, text, check, env
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", "git")

        with tempfile.TemporaryDirectory() as tmp_dir:
            managed_root = Path(tmp_dir) / "managed"
            service = GitHubRepoImportService(
                github_auth_service=_AlwaysAuthorizedGitHubOAuthService(),
                subprocess_runner=fake_subprocess_runner,
            )

            with self.assertRaises(RepoImportCloneError) as exc_context:
                service.import_repository(
                    run_id="run-003",
                    github_repo_url="https://github.com/openai/demo",
                    github_config=self._build_github_config(str(managed_root)),
                    requested_base_branch="main",
                )

        self.assertEqual(str(exc_context.exception), "系统缺少 git 可执行文件，无法克隆仓库。")


if __name__ == "__main__":
    unittest.main()
