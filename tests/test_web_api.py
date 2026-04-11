import json
import logging
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from comet.agent.state import AgentState, ParallelAgentState, WorkerResult
from comet.config.settings import Settings
from comet.executor.coverage_parser import MethodCoverage
from comet.models import Mutant, MutationPatch, TestCase, TestMethod
from comet.store.database import Database
from comet.utils.log_context import log_context
from comet.utils.method_keys import build_method_key
from comet.web.app import app, create_app
from comet.web.github_auth_service import GitHubAuthStatus, GitHubOAuthService, GitHubTokenStorage
from comet.web.log_router import RunLogRouter
from comet.web.run_service import RunLifecycleService, RunRequest
from comet.web.runtime_protocol import RuntimeEventBus, build_run_snapshot


class HealthApiTests(unittest.TestCase):
    def test_health_endpoint_returns_ok(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "activeRunId": None})


class ConfigApiTests(unittest.TestCase):
    def test_app_is_importable(self) -> None:
        self.assertEqual(app.title, "COMET-L Web API")

    def test_defaults_endpoint_returns_normalized_config(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.get("/api/config/defaults")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("config", payload)
        self.assertEqual(payload["config"]["llm"]["model"], "gpt-4")
        self.assertTrue(payload["config"]["evolution"]["mutation_enabled"])
        self.assertFalse(payload["config"]["preprocessing"]["exit_after_preprocessing"])
        self.assertNotIn("paths", payload["config"])

    def test_parse_valid_yaml_returns_normalized_config(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        (
                            "llm:\n"
                            "  api_key: test-key\n"
                            "  model: gpt-4o-mini\n"
                            "execution:\n"
                            "  timeout: 123\n"
                            "preprocessing:\n"
                            "  exit_after_preprocessing: true\n"
                            "evolution:\n"
                            "  mutation_enabled: false\n"
                            "agent:\n"
                            "  parallel:\n"
                            "    enabled: true\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["config"]["llm"]["api_key"], "test-key")
        self.assertEqual(payload["config"]["llm"]["model"], "gpt-4o-mini")
        self.assertEqual(payload["config"]["execution"]["timeout"], 123)
        self.assertTrue(payload["config"]["preprocessing"]["exit_after_preprocessing"])
        self.assertFalse(payload["config"]["evolution"]["mutation_enabled"])
        self.assertTrue(payload["config"]["agent"]["parallel"]["enabled"])
        self.assertNotIn("paths", payload["config"])

    def test_parse_yaml_rejects_invalid_mutation_enabled_type(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        (
                            "llm:\n"
                            "  api_key: test-key\n"
                            "evolution:\n"
                            "  mutation_enabled: 'disabled'\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_config")
        field_errors = payload["error"]["fieldErrors"]
        error_map = {tuple(item["path"]): item["code"] for item in field_errors}
        self.assertEqual(error_map[("evolution", "mutation_enabled")], "bool_type")

    def test_parse_valid_yaml_accepts_large_parallel_values(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        (
                            "llm:\n"
                            "  api_key: test-key\n"
                            "preprocessing:\n"
                            "  max_workers: 64\n"
                            "agent:\n"
                            "  parallel:\n"
                            "    max_parallel_targets: 64\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["config"]["preprocessing"]["max_workers"], 64)
        self.assertEqual(payload["config"]["agent"]["parallel"]["max_parallel_targets"], 64)

    def test_parse_valid_yaml_preserves_nullable_preprocessing_max_workers(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        ("llm:\n  api_key: test-key\npreprocessing:\n  max_workers: null\n").encode(
                            "utf-8"
                        )
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["config"]["preprocessing"]["max_workers"])

    def test_parse_invalid_yaml_returns_field_errors(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        ("llm:\n  temperature: 3.5\nexecution:\n  timeout: 0\n").encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_config")
        field_errors = payload["error"]["fieldErrors"]
        error_map = {tuple(item["path"]): item["code"] for item in field_errors}
        self.assertEqual(error_map[("llm", "api_key")], "missing")
        self.assertEqual(error_map[("llm", "temperature")], "less_than_equal")
        self.assertEqual(error_map[("execution", "timeout")], "greater_than_equal")

    def test_parse_large_parallel_targets_preserves_lower_bound_validation(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        (
                            "llm:\n"
                            "  api_key: test-key\n"
                            "agent:\n"
                            "  parallel:\n"
                            "    max_parallel_targets: 0\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_config")
        field_errors = payload["error"]["fieldErrors"]
        error_map = {tuple(item["path"]): item["code"] for item in field_errors}
        self.assertEqual(
            error_map[("agent", "parallel", "max_parallel_targets")],
            "greater_than_equal",
        )

    def test_parse_yaml_rejects_removed_paths_field(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        ("llm:\n  api_key: test-key\npaths:\n  output: ./custom-output\n").encode(
                            "utf-8"
                        )
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_config")
        self.assertTrue(any(item["path"] == ["paths"] for item in payload["error"]["fieldErrors"]))

    def test_parse_yaml_filters_github_deployment_config(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        (
                            "llm:\n"
                            "  api_key: test-key\n"
                            "github:\n"
                            "  oauth_client_id: uploaded-client-id\n"
                            "  managed_clone_root: /tmp/uploaded-managed-root\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["config"]["github"]["oauth_client_id"])
        self.assertEqual(
            payload["config"]["github"]["managed_clone_root"],
            "./sandbox/github-managed",
        )

    def test_defaults_endpoint_prefers_github_oauth_env_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_config_path = root / "config.example.yaml"
            default_config_path.write_text(
                "llm:\n  api_key: default-key\n",
                encoding="utf-8",
            )
            client = TestClient(
                create_app(
                    run_service=RunLifecycleService(workspace_root=root),
                    default_config_path=default_config_path,
                )
            )

            with patch.dict(
                "os.environ",
                {
                    "COMET_GITHUB_OAUTH_CLIENT_ID": "env-client-id",
                    "COMET_GITHUB_OAUTH_CLIENT_SECRET": "env-client-secret",
                    "COMET_GITHUB_OAUTH_REDIRECT_URI": "http://127.0.0.1:9000/api/github/auth/callback",
                    "COMET_GITHUB_OAUTH_SCOPE": "public_repo",
                },
                clear=False,
            ):
                response = client.get("/api/config/defaults")

            self.assertEqual(response.status_code, 200)
            payload = response.json()["config"]["github"]
            self.assertEqual(payload["oauth_client_id"], "env-client-id")
            self.assertEqual(payload["oauth_client_secret"], "env-client-secret")
            self.assertEqual(
                payload["oauth_redirect_uri"],
                "http://127.0.0.1:9000/api/github/auth/callback",
            )
            self.assertEqual(payload["oauth_scope"], "public_repo")


class _FailingKeyring:
    def get_password(self, service_name: str, account_name: str) -> str | None:
        del service_name, account_name
        raise RuntimeError("keyring unavailable")

    def set_password(self, service_name: str, account_name: str, password: str) -> None:
        del service_name, account_name, password
        raise RuntimeError("keyring unavailable")

    def delete_password(self, service_name: str, account_name: str) -> None:
        del service_name, account_name
        raise RuntimeError("keyring unavailable")


class _AlwaysAuthorizedGitHubOAuthService(GitHubOAuthService):
    def get_status(self, github_config: Any) -> GitHubAuthStatus:
        del github_config
        return GitHubAuthStatus(
            connected=True,
            requires_reauth=False,
            message="GitHub 已连接。",
        )

    def get_access_token(self, github_config: Any) -> str:
        del github_config
        return "gho-test-valid"


class GitHubAuthApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    default_config_path: Path | None = None
    run_service: RunLifecycleService | None = None
    github_auth_service: GitHubOAuthService | None = None
    client: TestClient | None = None
    env_patcher: Any = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir(parents=True)
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

        token_path = self.root / "state" / "github" / "auth" / "token.enc"
        key_path = self.root / "state" / "github" / "auth" / "token.key"
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text(
            "llm:\n  api_key: default-key\n  model: gpt-4\n",
            encoding="utf-8",
        )
        self.env_patcher = patch.dict(
            "os.environ",
            {
                "COMET_GITHUB_OAUTH_CLIENT_ID": "test-client-id",
                "COMET_GITHUB_OAUTH_CLIENT_SECRET": "test-client-secret",
                "COMET_GITHUB_OAUTH_REDIRECT_URI": "http://127.0.0.1:8000/api/github/auth/callback",
                "COMET_GITHUB_OAUTH_SCOPE": "repo",
                "COMET_GITHUB_ENCRYPTED_TOKEN_STORE_PATH": token_path.as_posix(),
                "COMET_GITHUB_ENCRYPTED_KEY_STORE_PATH": key_path.as_posix(),
            },
            clear=False,
        )
        self.env_patcher.start()

        self.run_service = RunLifecycleService(workspace_root=self.root)

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/login/oauth/access_token"):
                return httpx.Response(200, json={"access_token": "gho-test-valid"})

            if request.url.path.endswith("/applications/test-client-id/token"):
                body = json.loads(request.content.decode("utf-8"))
                token = body.get("access_token")
                if token == "gho-test-valid":
                    return httpx.Response(200, json={"token": "ok"})
                if token == "gho-test-expired":
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(401, json={"message": "Bad credentials"})

            return httpx.Response(404, json={"message": "not implemented"})

        transport = httpx.MockTransport(_handler)
        storage = GitHubTokenStorage(keyring_backend=_FailingKeyring())
        self.github_auth_service = GitHubOAuthService(
            storage=storage,
            http_client_factory=lambda: httpx.Client(transport=transport, timeout=10.0),
        )
        self.client = TestClient(
            create_app(
                run_service=self.run_service,
                default_config_path=self.default_config_path,
                github_auth_service=self.github_auth_service,
            )
        )

    def tearDown(self) -> None:
        if self.env_patcher is not None:
            self.env_patcher.stop()
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _github_files(self) -> tuple[Path, Path]:
        assert self.default_config_path is not None
        settings = Settings.from_yaml(str(self.default_config_path))
        token_path = Path(settings.github.encrypted_token_store_path).expanduser()
        key_path = Path(settings.github.encrypted_key_store_path).expanduser()
        return token_path, key_path

    def test_github_auth_status_reports_unauthorized_when_never_connected(self) -> None:
        assert self.client is not None
        response = self.client.get("/api/github/auth/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["connected"])
        self.assertFalse(payload["requiresReauth"])
        self.assertEqual(payload["provider"], "github-oauth-app")

    def test_github_auth_callback_persists_encrypted_token_and_marks_connected(self) -> None:
        assert self.client is not None
        connect_response = self.client.get("/api/github/auth/connect-url")
        self.assertEqual(connect_response.status_code, 200)
        connect_url = connect_response.json()["connectUrl"]
        query = parse_qs(urlparse(connect_url).query)
        state = query["state"][0]

        callback_response = self.client.get(f"/api/github/auth/callback?code=ok-code&state={state}")
        self.assertEqual(callback_response.status_code, 200)
        callback_payload = callback_response.json()
        self.assertTrue(callback_payload["connected"])
        self.assertFalse(callback_payload["requiresReauth"])

        token_path, key_path = self._github_files()
        self.assertTrue(token_path.exists())
        self.assertTrue(key_path.exists())
        self.assertNotIn("gho-test-valid", token_path.read_text(encoding="utf-8"))
        self.assertNotIn("gho-test-valid", key_path.read_text(encoding="utf-8"))

        status_response = self.client.get("/api/github/auth/status")
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.json()
        self.assertTrue(status_payload["connected"])
        self.assertFalse(status_payload["requiresReauth"])

    def test_github_auth_callback_redirects_browser_requests_back_to_home(self) -> None:
        assert self.client is not None
        connect_response = self.client.get("/api/github/auth/connect-url")
        state = parse_qs(urlparse(connect_response.json()["connectUrl"]).query)["state"][0]

        callback_response = self.client.get(
            f"/api/github/auth/callback?code=ok-code&state={state}",
            headers={"accept": "text/html,application/xhtml+xml"},
            follow_redirects=False,
        )

        self.assertEqual(callback_response.status_code, 303)
        self.assertEqual(
            callback_response.headers["location"],
            "/?github_oauth=connected",
        )

        status_response = self.client.get("/api/github/auth/status")
        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(status_response.json()["connected"])

    def test_github_auth_callback_redirects_browser_errors_back_to_home(self) -> None:
        assert self.client is not None

        callback_response = self.client.get(
            "/api/github/auth/callback?code=ok-code&state=missing-state",
            headers={"accept": "text/html,application/xhtml+xml"},
            follow_redirects=False,
        )

        self.assertEqual(callback_response.status_code, 303)
        self.assertEqual(
            callback_response.headers["location"],
            "/?github_oauth=error&message=OAuth+%E5%9B%9E%E8%B0%83%E7%8A%B6%E6%80%81%E6%97%A0%E6%95%88%E6%88%96%E5%B7%B2%E8%BF%87%E6%9C%9F%EF%BC%8C%E8%AF%B7%E9%87%8D%E6%96%B0%E5%8F%91%E8%B5%B7%E6%8E%88%E6%9D%83%E3%80%82",
        )

    def test_github_auth_callback_returns_json_for_generic_accept_header(self) -> None:
        assert self.client is not None
        connect_response = self.client.get("/api/github/auth/connect-url")
        state = parse_qs(urlparse(connect_response.json()["connectUrl"]).query)["state"][0]

        callback_response = self.client.get(
            f"/api/github/auth/callback?code=ok-code&state={state}",
            headers={"accept": "*/*"},
        )

        self.assertEqual(callback_response.status_code, 200)
        self.assertTrue(callback_response.json()["connected"])

    def test_github_auth_callback_handles_browser_cancel_redirect(self) -> None:
        assert self.client is not None
        connect_response = self.client.get("/api/github/auth/connect-url")
        state = parse_qs(urlparse(connect_response.json()["connectUrl"]).query)["state"][0]

        callback_response = self.client.get(
            f"/api/github/auth/callback?error=access_denied&state={state}",
            headers={"accept": "text/html,application/xhtml+xml"},
            follow_redirects=False,
        )

        self.assertEqual(callback_response.status_code, 303)
        self.assertEqual(
            callback_response.headers["location"],
            "/?github_oauth=error&message=GitHub+%E6%8E%88%E6%9D%83%E5%B7%B2%E5%8F%96%E6%B6%88%EF%BC%8C%E8%AF%B7%E9%87%8D%E6%96%B0%E5%8F%91%E8%B5%B7%E6%8E%88%E6%9D%83%E3%80%82",
        )

    def test_github_auth_status_reports_reauth_when_token_expired(self) -> None:
        assert self.client is not None
        assert self.default_config_path is not None
        assert self.github_auth_service is not None
        settings = Settings.from_yaml(str(self.default_config_path))
        self.github_auth_service._storage.write_token(settings.github, "gho-test-expired")

        response = self.client.get("/api/github/auth/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["connected"])
        self.assertTrue(payload["requiresReauth"])

    def test_github_auth_status_restores_after_restart_from_local_storage(self) -> None:
        assert self.client is not None
        assert self.default_config_path is not None
        assert self.root is not None
        connect_response = self.client.get("/api/github/auth/connect-url")
        state = parse_qs(urlparse(connect_response.json()["connectUrl"]).query)["state"][0]
        callback_response = self.client.get(f"/api/github/auth/callback?code=ok-code&state={state}")
        self.assertEqual(callback_response.status_code, 200)

        transport = httpx.MockTransport(lambda request: self._restart_mock_handler(request))
        restarted_service = GitHubOAuthService(
            storage=GitHubTokenStorage(keyring_backend=_FailingKeyring()),
            http_client_factory=lambda: httpx.Client(transport=transport, timeout=10.0),
        )
        restarted_client = TestClient(
            create_app(
                run_service=RunLifecycleService(workspace_root=self.root),
                default_config_path=self.default_config_path,
                github_auth_service=restarted_service,
            )
        )

        status_response = restarted_client.get("/api/github/auth/status")
        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(status_response.json()["connected"])

    def _restart_mock_handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/applications/test-client-id/token"):
            body = json.loads(request.content.decode("utf-8"))
            if body.get("access_token") == "gho-test-valid":
                return httpx.Response(200, json={"token": "ok"})
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path.endswith("/login/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "gho-test-valid"})
        return httpx.Response(404, json={"message": "not implemented"})

    def test_github_auth_disconnect_clears_only_github_auth_state(self) -> None:
        assert self.client is not None
        connect_response = self.client.get("/api/github/auth/connect-url")
        state = parse_qs(urlparse(connect_response.json()["connectUrl"]).query)["state"][0]
        callback_response = self.client.get(f"/api/github/auth/callback?code=ok-code&state={state}")
        self.assertEqual(callback_response.status_code, 200)

        disconnect_response = self.client.post("/api/github/auth/disconnect")
        self.assertEqual(disconnect_response.status_code, 200)
        self.assertFalse(disconnect_response.json()["connected"])

        token_path, key_path = self._github_files()
        self.assertFalse(token_path.exists())
        self.assertFalse(key_path.exists())

        status_response = self.client.get("/api/github/auth/status")
        self.assertEqual(status_response.status_code, 200)
        self.assertFalse(status_response.json()["connected"])

        config_response = self.client.get("/api/config/defaults")
        self.assertEqual(config_response.status_code, 200)
        self.assertEqual(config_response.json()["config"]["llm"]["model"], "gpt-4")

    def test_github_auth_connect_url_prefers_env_oauth_config(self) -> None:
        assert self.client is not None

        with patch.dict(
            "os.environ",
            {
                "COMET_GITHUB_OAUTH_CLIENT_ID": "env-client-id",
                "COMET_GITHUB_OAUTH_CLIENT_SECRET": "env-client-secret",
                "COMET_GITHUB_OAUTH_REDIRECT_URI": "http://127.0.0.1:9000/api/github/auth/callback",
                "COMET_GITHUB_OAUTH_SCOPE": "public_repo",
            },
            clear=False,
        ):
            response = self.client.get("/api/github/auth/connect-url")

        self.assertEqual(response.status_code, 200)
        connect_url = response.json()["connectUrl"]
        query = parse_qs(urlparse(connect_url).query)
        self.assertEqual(query["client_id"], ["env-client-id"])
        self.assertEqual(
            query["redirect_uri"],
            ["http://127.0.0.1:9000/api/github/auth/callback"],
        )
        self.assertEqual(query["scope"], ["public_repo"])


class GitHubRepoImportRunApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    default_config_path: Path | None = None
    run_service: RunLifecycleService | None = None
    github_auth_service: GitHubOAuthService | None = None
    client: TestClient | None = None
    env_patcher: Any = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        token_path = self.root / "state" / "github" / "auth" / "token.enc"
        key_path = self.root / "state" / "github" / "auth" / "token.key"
        managed_clone_root = self.root / "sandbox" / "managed-clones"
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text(
            "llm:\n  api_key: default-key\n  model: gpt-4\n",
            encoding="utf-8",
        )
        self.env_patcher = patch.dict(
            "os.environ",
            {
                "COMET_GITHUB_OAUTH_CLIENT_ID": "test-client-id",
                "COMET_GITHUB_OAUTH_CLIENT_SECRET": "test-client-secret",
                "COMET_GITHUB_OAUTH_REDIRECT_URI": "http://127.0.0.1:8000/api/github/auth/callback",
                "COMET_GITHUB_OAUTH_SCOPE": "repo",
                "COMET_GITHUB_ENCRYPTED_TOKEN_STORE_PATH": token_path.as_posix(),
                "COMET_GITHUB_ENCRYPTED_KEY_STORE_PATH": key_path.as_posix(),
                "COMET_GITHUB_MANAGED_CLONE_ROOT": managed_clone_root.as_posix(),
            },
            clear=False,
        )
        self.env_patcher.start()

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/applications/test-client-id/token"):
                body = json.loads(request.content.decode("utf-8"))
                if body.get("access_token") == "gho-test-valid":
                    return httpx.Response(200, json={"token": "ok"})
                return httpx.Response(401, json={"message": "Bad credentials"})

            if request.url.path.endswith("/repos/openai/example-repo"):
                return httpx.Response(200, json={"default_branch": "develop"})

            if request.url.path.endswith("/repos/openai/non-maven-repo"):
                return httpx.Response(200, json={"default_branch": "main"})

            if request.url.path.endswith("/repos/openai/no-default-branch"):
                return httpx.Response(200, json={"default_branch": ""})

            if request.url.path.endswith("/repos/openai/no-permission"):
                return httpx.Response(403, json={"message": "Forbidden"})

            return httpx.Response(404, json={"message": "not implemented"})

        transport = httpx.MockTransport(_handler)
        storage = GitHubTokenStorage(keyring_backend=_FailingKeyring())
        self.github_auth_service = _AlwaysAuthorizedGitHubOAuthService(
            storage=storage,
            http_client_factory=lambda: httpx.Client(transport=transport, timeout=10.0),
        )

        self.run_service = RunLifecycleService(workspace_root=self.root)

        def fake_initialize(
            config: Settings,
            bug_reports_dir: str | None = None,
            parallel_mode: bool = False,
        ) -> dict[str, object]:
            return {
                "config": config,
                "bug_reports_dir": bug_reports_dir,
                "parallel_mode": parallel_mode,
            }

        def fake_run(
            project_path: str,
            components: dict[str, object],
            resume_state: str | None = None,
        ) -> None:
            del project_path, components, resume_state

        self.client = TestClient(
            create_app(
                run_service=self.run_service,
                default_config_path=self.default_config_path,
                github_auth_service=self.github_auth_service,
                system_initializer=fake_initialize,
                evolution_runner=fake_run,
            )
        )

    def tearDown(self) -> None:
        if self.env_patcher is not None:
            self.env_patcher.stop()
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _mock_clone_success(
        self,
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check
        self.assertNotIn("AUTHORIZATION", " ".join(command))
        self.assertIn("AUTHORIZATION: basic", env["GIT_CONFIG_VALUE_0"])
        clone_path = Path(command[-1])
        clone_path.mkdir(parents=True, exist_ok=True)
        (clone_path / ".git").mkdir(parents=True, exist_ok=True)
        (clone_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        (clone_path / "src" / "main" / "java").mkdir(parents=True, exist_ok=True)
        (clone_path / "src" / "test" / "java").mkdir(parents=True, exist_ok=True)
        (clone_path / "src" / "test" / "resources").mkdir(parents=True, exist_ok=True)
        (clone_path / "src" / "test" / "java" / "OldTest.java").write_text(
            "class OldTest {}",
            encoding="utf-8",
        )
        (clone_path / "src" / "test" / "resources" / "fixture.txt").write_text(
            "fixture",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    def _mock_clone_non_maven(
        self,
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, check, env
        clone_path = Path(command[-1])
        clone_path.mkdir(parents=True, exist_ok=True)
        (clone_path / ".git").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    def test_post_runs_imports_github_repo_into_managed_root_and_cleans_test_dirs(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        assert self.default_config_path is not None

        with patch(
            "comet.web.repo_import_service.subprocess.run",
            side_effect=self._mock_clone_success,
        ) as mocked_clone:
            response = self.client.post(
                "/api/runs",
                data={
                    "projectPath": "/not-used-in-github-mode",
                    "githubRepoUrl": "https://github.com/openai/example-repo.git",
                },
            )

        self.assertEqual(response.status_code, 201)
        run_id = response.json()["runId"]
        run_request = self.run_service.get_run_request(run_id)
        imported_path = Path(run_request.project_path)
        managed_root = (
            Path(Settings.from_yaml(str(self.default_config_path)).github.managed_clone_root)
            .expanduser()
            .resolve()
        )

        self.assertTrue(imported_path.is_relative_to(managed_root))
        self.assertTrue((imported_path / ".git").is_dir())
        self.assertFalse((imported_path / "src" / "test" / "java").exists())
        self.assertFalse((imported_path / "src" / "test" / "resources").exists())
        self.assertTrue((imported_path / "src" / "main" / "java").exists())
        self.assertEqual(run_request.github_base_branch, "develop")
        self.assertTrue(mocked_clone.called)

    def test_post_runs_rejects_non_maven_github_repo(self) -> None:
        assert self.client is not None

        with patch(
            "comet.web.repo_import_service.subprocess.run",
            side_effect=self._mock_clone_non_maven,
        ):
            response = self.client.post(
                "/api/runs",
                data={
                    "projectPath": "/not-used-in-github-mode",
                    "githubRepoUrl": "https://github.com/openai/non-maven-repo",
                },
            )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "non_maven_repository")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "non_maven_repository")

    def test_post_runs_reports_default_branch_resolution_failure(self) -> None:
        assert self.client is not None

        response = self.client.post(
            "/api/runs",
            data={
                "projectPath": "/not-used-in-github-mode",
                "githubRepoUrl": "https://github.com/openai/no-default-branch",
            },
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "github_default_branch_unresolved")

    def test_post_runs_reports_github_permission_failure_during_import(self) -> None:
        assert self.client is not None

        response = self.client.post(
            "/api/runs",
            data={
                "projectPath": "/not-used-in-github-mode",
                "githubRepoUrl": "https://github.com/openai/no-permission",
            },
        )

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "github_unauthorized")


class SnapshotTests(unittest.TestCase):
    def test_standard_snapshot_includes_decision_reasoning(self) -> None:
        state = AgentState()
        state.current_target = {"class_name": "Calculator", "method_name": "add"}
        state.set_decision_reasoning("Need more assertions for Calculator.add")
        state.add_improvement(
            {
                "iteration": 3,
                "mutation_score_delta": 0.1,
                "coverage_delta": 0.05,
            }
        )

        snapshot = build_run_snapshot("run-001", "running", state)

        self.assertEqual(snapshot["mode"], "standard")
        self.assertEqual(snapshot["decisionReasoning"], "Need more assertions for Calculator.add")
        self.assertEqual(
            snapshot["currentTarget"],
            {"class_name": "Calculator", "method_name": "add"},
        )
        self.assertEqual(snapshot["improvementSummary"]["count"], 1)
        self.assertEqual(snapshot["improvementSummary"]["latest"]["mutation_score_delta"], 0.1)

    def test_snapshot_marks_disabled_mutation_and_nulls_mutation_metrics(self) -> None:
        state = AgentState()
        state.global_mutation_enabled = False
        state.total_mutants = 0
        state.global_total_mutants = 0
        state.killed_mutants = 0
        state.global_killed_mutants = 0
        state.survived_mutants = 0
        state.global_survived_mutants = 0
        state.mutation_score = 0.0
        state.global_mutation_score = 0.0
        state.total_tests = 2
        state.line_coverage = 0.6
        state.branch_coverage = 0.4

        snapshot = build_run_snapshot("run-disabled", "running", state)

        self.assertFalse(snapshot["mutationEnabled"])
        self.assertIsNone(snapshot["metrics"]["mutationScore"])
        self.assertIsNone(snapshot["metrics"]["globalMutationScore"])
        self.assertIsNone(snapshot["metrics"]["totalMutants"])
        self.assertIsNone(snapshot["metrics"]["globalTotalMutants"])
        self.assertIsNone(snapshot["metrics"]["killedMutants"])
        self.assertIsNone(snapshot["metrics"]["globalKilledMutants"])
        self.assertIsNone(snapshot["metrics"]["survivedMutants"])
        self.assertIsNone(snapshot["metrics"]["globalSurvivedMutants"])
        self.assertEqual(snapshot["metrics"]["totalTests"], 2)
        self.assertEqual(snapshot["metrics"]["lineCoverage"], 0.6)
        self.assertEqual(snapshot["metrics"]["branchCoverage"], 0.4)

    def test_parallel_snapshot_includes_worker_cards(self) -> None:
        state = ParallelAgentState()
        log_router = RunLogRouter()
        acquired = state.acquire_target(
            "Calculator",
            "add",
            method_signature="int add(int a, int b)",
            metadata={"method_coverage": 0.4, "source": "coverage"},
        )
        self.assertTrue(acquired)
        state.add_batch_result(
            [
                WorkerResult(
                    target_id=build_method_key("Calculator", "add", "int add(int a, int b)"),
                    class_name="Calculator",
                    method_name="add",
                    method_signature="int add(int a, int b)",
                    success=True,
                    tests_generated=2,
                    mutants_generated=3,
                    mutants_evaluated=3,
                    mutants_killed=2,
                    local_mutation_score=2 / 3,
                    processing_time=1.5,
                    method_coverage=0.4,
                )
            ]
        )

        snapshot = build_run_snapshot("run-002", "running", state, log_router=log_router)

        self.assertEqual(snapshot["mode"], "parallel")
        self.assertEqual(snapshot["currentBatch"], 1)
        self.assertEqual(snapshot["parallelStats"]["total_batches"], 1)
        self.assertEqual(len(snapshot["workerCards"]), 1)
        self.assertEqual(
            snapshot["workerCards"][0]["targetId"],
            build_method_key("Calculator", "add", "int add(int a, int b)"),
        )
        self.assertEqual(snapshot["workerCards"][0]["methodCoverage"], 0.4)
        self.assertEqual(len(snapshot["activeTargets"]), 1)
        self.assertEqual(
            snapshot["activeTargets"][0]["targetId"],
            build_method_key("Calculator", "add", "int add(int a, int b)"),
        )
        self.assertEqual(snapshot["activeTargets"][0]["method_coverage"], 0.4)
        self.assertEqual(len(snapshot["batchResults"]), 1)
        self.assertEqual(
            snapshot["batchResults"][0][0]["targetId"],
            build_method_key("Calculator", "add", "int add(int a, int b)"),
        )
        self.assertEqual(len(snapshot["targetLifecycle"]), 1)
        self.assertEqual(snapshot["targetLifecycle"][0]["status"], "running")
        self.assertEqual(
            snapshot["logStreams"]["taskIds"],
            ["main", build_method_key("Calculator", "add", "int add(int a, int b)")],
        )
        self.assertEqual(
            snapshot["logStreams"]["byTaskId"][
                build_method_key("Calculator", "add", "int add(int a, int b)")
            ]["bufferedEntryCount"],
            0,
        )
        self.assertEqual(
            snapshot["logStreams"]["byTaskId"][
                build_method_key("Calculator", "add", "int add(int a, int b)")
            ]["status"],
            "running",
        )

    def test_parallel_snapshot_merges_worker_logs_into_target_stream(self) -> None:
        state = ParallelAgentState()
        log_router = RunLogRouter()
        task_id = build_method_key("Calculator", "add", "int add(int a, int b)")
        self.assertTrue(state.acquire_target("Calculator", "add", "int add(int a, int b)"))

        logger = logging.getLogger("test.web.api.parallel_logs")
        logger.setLevel(logging.INFO)
        logger.addHandler(log_router)
        self.addCleanup(logger.removeHandler, log_router)

        with log_context(task_id):
            logger.info("worker log line")

        snapshot = build_run_snapshot("run-logs-merge", "running", state, log_router=log_router)

        self.assertEqual(snapshot["logStreams"]["taskIds"], ["main", task_id])
        self.assertEqual(
            snapshot["logStreams"]["byTaskId"][task_id]["bufferedEntryCount"],
            1,
        )
        self.assertEqual(
            snapshot["logStreams"]["byTaskId"][task_id]["totalEntryCount"],
            1,
        )
        self.assertNotIn(f"Worker:{task_id}", snapshot["logStreams"]["byTaskId"])

    def test_parallel_state_distinguishes_overloaded_target_ids(self) -> None:
        state = ParallelAgentState()

        self.assertTrue(state.acquire_target("Calculator", "add", "int add(int a, int b)"))
        self.assertTrue(state.acquire_target("Calculator", "add", "double add(double a, double b)"))

        active_targets = state.get_active_target_details()
        self.assertEqual(len(active_targets), 2)
        self.assertEqual(
            {target["targetId"] for target in active_targets},
            {
                build_method_key("Calculator", "add", "int add(int a, int b)"),
                build_method_key("Calculator", "add", "double add(double a, double b)"),
            },
        )


class EventBusTests(unittest.TestCase):
    def test_event_bus_keeps_ordered_snapshot_events(self) -> None:
        state = AgentState()
        state.set_decision_reasoning("reasoning")
        bus = RuntimeEventBus(max_events=2)

        first = bus.publish_snapshot("run-003", "running", state)
        second = bus.publish("run.completed", runId="run-003")

        events = bus.list_events()
        self.assertEqual([event["type"] for event in events], ["run.snapshot", "run.completed"])
        self.assertLess(first["sequence"], second["sequence"])


class StreamingApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    default_config_path: Path | None = None
    run_service: RunLifecycleService | None = None
    client: TestClient | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text(
            (
                "llm:\n"
                "  api_key: default-key\n"
                "  model: gpt-4\n"
                "github:\n"
                "  oauth_client_id: yaml-client-id\n"
                "  oauth_client_secret: yaml-client-secret\n"
                "  managed_clone_root: ./sandbox/default-managed-root\n"
            ),
            encoding="utf-8",
        )
        self.run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(
            create_app(
                run_service=self.run_service,
                default_config_path=self.default_config_path,
            )
        )

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _create_run(self) -> str:
        assert self.run_service is not None
        assert self.project_path is not None
        session = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        )
        return session.run_id

    def _parse_sse(self, payload: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for block in payload.strip().split("\n\n"):
            if not block.strip():
                continue
            event: dict[str, Any] = {}
            for line in block.splitlines():
                key, value = line.split(": ", 1)
                if key == "data":
                    event[key] = json.loads(value)
                else:
                    event[key] = value
            events.append(event)
        return events

    def test_events_endpoint_streams_snapshot_then_ordered_terminal_events(
        self,
    ) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        bus = self.run_service.get_event_bus(run_id)
        self.run_service.mark_running(run_id)
        bus.publish("run.started", runId=run_id, projectPath="/tmp/project")
        bus.publish(
            "run.phase",
            runId=run_id,
            phase={"key": "running", "label": "Running"},
        )
        bus.publish("run.completed", runId=run_id)
        self.run_service.mark_completed(run_id)

        response = self.client.get(f"/api/runs/{run_id}/events")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/event-stream; charset=utf-8")
        events = self._parse_sse(response.text)
        self.assertGreaterEqual(len(events), 4)
        self.assertEqual(events[0]["event"], "run.snapshot")
        self.assertEqual(events[0]["data"]["snapshot"]["status"], "completed")
        event_names = [event["event"] for event in events[1:]]
        self.assertIn("run.started", event_names)
        self.assertIn("run.phase", event_names)
        self.assertIn("run.completed", event_names)
        self.assertEqual(events[-2]["data"]["type"], "run.completed")
        self.assertEqual(events[-1]["event"], "run.snapshot")

    def test_log_endpoints_list_streams_and_return_bounded_entries(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        router = RunLogRouter(max_entries_per_stream=2)
        self.run_service._log_routers[run_id] = router

        logger = logging.getLogger("test.web.api.streaming")
        logger.setLevel(logging.INFO)
        logger.addHandler(router)
        self.addCleanup(logger.removeHandler, router)

        logger.info("main-1")
        logger.info("main-2")
        logger.info("main-3")
        with log_context("task-1"):
            logger.info("worker-1")
            logger.info("worker-2")
            logger.info("worker-3")

        state = ParallelAgentState()
        self.assertTrue(state.acquire_target("Task", "zeroLogs"))
        self.run_service.publish_runtime_snapshot(run_id, state=state)

        summary = self.client.get(f"/api/runs/{run_id}/logs")
        self.assertEqual(summary.status_code, 200)
        summary_payload = summary.json()
        self.assertEqual(summary_payload["runId"], run_id)
        self.assertEqual(
            summary_payload["streams"]["taskIds"],
            ["main", "task-1", "Task.zeroLogs"],
        )
        self.assertEqual(
            summary_payload["streams"]["counts"],
            {"main": 2, "task-1": 2, "Task.zeroLogs": 0},
        )
        self.assertEqual(summary_payload["streams"]["maxEntriesPerStream"], 2)
        self.assertEqual(
            summary_payload["streams"]["byTaskId"]["task-1"]["bufferedEntryCount"],
            2,
        )
        self.assertEqual(
            summary_payload["streams"]["byTaskId"]["task-1"]["totalEntryCount"],
            3,
        )
        self.assertEqual(
            summary_payload["streams"]["byTaskId"]["Task.zeroLogs"]["status"],
            "running",
        )
        self.assertEqual(
            summary_payload["streams"]["byTaskId"]["Task.zeroLogs"]["bufferedEntryCount"],
            0,
        )

        main_logs = self.client.get(f"/api/runs/{run_id}/logs/main")
        self.assertEqual(main_logs.status_code, 200)
        self.assertEqual(
            [entry["message"] for entry in main_logs.json()["entries"]],
            ["main-2", "main-3"],
        )

        worker_logs = self.client.get(f"/api/runs/{run_id}/logs/task-1")
        self.assertEqual(worker_logs.status_code, 200)
        worker_payload = worker_logs.json()
        self.assertEqual(
            worker_payload["availableTaskIds"],
            ["main", "task-1", "Task.zeroLogs"],
        )
        self.assertEqual(worker_payload["stream"]["taskId"], "task-1")
        self.assertEqual(worker_payload["stream"]["status"], "running")
        self.assertEqual(
            [entry["message"] for entry in worker_payload["entries"]],
            ["worker-2", "worker-3"],
        )

        zero_log_task = self.client.get(f"/api/runs/{run_id}/logs/Task.zeroLogs")
        self.assertEqual(zero_log_task.status_code, 200)
        zero_log_payload = zero_log_task.json()
        self.assertEqual(zero_log_payload["entries"], [])
        self.assertEqual(zero_log_payload["stream"]["taskId"], "Task.zeroLogs")
        self.assertEqual(zero_log_payload["stream"]["bufferedEntryCount"], 0)
        self.assertEqual(zero_log_payload["stream"]["status"], "running")


class RunApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    non_maven_path: Path | None = None
    default_config_path: Path | None = None
    release_run: threading.Event | None = None
    run_started: threading.Event | None = None
    run_service: RunLifecycleService | None = None
    client: TestClient | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.non_maven_path = self.root / "not-maven"
        self.non_maven_path.mkdir()
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text(
            ("llm:\n  api_key: default-key\n  model: gpt-4\n"),
            encoding="utf-8",
        )
        self.release_run = threading.Event()
        self.run_started = threading.Event()
        self.run_service = RunLifecycleService(workspace_root=self.root)

        def fake_initialize(
            config: Settings,
            bug_reports_dir: str | None = None,
            parallel_mode: bool = False,
        ) -> dict[str, object]:
            return {
                "config": config,
                "bug_reports_dir": bug_reports_dir,
                "parallel_mode": parallel_mode,
            }

        def fake_run(
            project_path: str,
            components: dict[str, object],
            resume_state: str | None = None,
        ) -> None:
            del project_path, resume_state
            assert self.run_started is not None
            assert self.release_run is not None
            config = components["config"]
            assert isinstance(config, Settings)
            state = ParallelAgentState() if components["parallel_mode"] else AgentState()
            state.global_mutation_enabled = config.evolution.mutation_enabled
            state.iteration = 1
            state.llm_calls = 3
            state.budget = config.evolution.budget_llm_calls
            state.total_tests = 1
            state.total_mutants = 2
            state.killed_mutants = 1
            state.survived_mutants = 1
            state.mutation_score = 0.5
            state.line_coverage = 0.25
            state.branch_coverage = 0.1
            runtime_snapshot_publisher = components.get("runtime_snapshot_publisher")
            if callable(runtime_snapshot_publisher):
                runtime_snapshot_publisher(
                    state=state,
                    phase={"key": "preprocessing", "label": "Preprocessing"},
                )

            self.run_started.set()
            released = self.release_run.wait(timeout=5)
            if not released:
                raise TimeoutError("run release timeout")

            state.iteration = 2
            state.llm_calls = 9
            state.budget = config.evolution.budget_llm_calls
            state.total_tests = 4
            state.total_mutants = 6
            state.killed_mutants = 5
            state.survived_mutants = 1
            state.mutation_score = 5 / 6
            state.line_coverage = 0.8
            state.branch_coverage = 0.6
            output_path = config.resolve_output_root()
            output_path.mkdir(parents=True, exist_ok=True)
            (output_path / "final_state.json").write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        self.client = TestClient(
            create_app(
                run_service=self.run_service,
                default_config_path=self.default_config_path,
                system_initializer=fake_initialize,
                evolution_runner=fake_run,
            )
        )

    def tearDown(self) -> None:
        if self.release_run is not None:
            self.release_run.set()
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _wait_for_status(self, run_id: str, expected: str, timeout: float = 5.0) -> None:
        assert self.run_service is not None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.run_service.get_session(run_id).status == expected:
                return
            time.sleep(0.01)
        self.fail(f"run {run_id} did not reach status {expected}")

    def test_post_runs_creates_background_run_and_returns_stable_snapshot(self) -> None:
        assert self.client is not None
        assert self.project_path is not None
        assert self.run_started is not None
        assert self.run_service is not None
        assert self.release_run is not None
        with patch.dict(
            "os.environ",
            {
                "COMET_GITHUB_OAUTH_CLIENT_ID": "env-client-id",
                "COMET_GITHUB_OAUTH_CLIENT_SECRET": "env-client-secret",
            },
            clear=False,
        ):
            response = self.client.post(
                "/api/runs",
                data={
                    "projectPath": str(self.project_path),
                    "maxIterations": "7",
                    "budget": "42",
                    "mutationEnabled": "false",
                    "parallel": "true",
                    "selectedJavaVersion": "17",
                },
                files={
                    "configFile": (
                        "config.yaml",
                        BytesIO(
                            (
                                "llm:\n"
                                "  api_key: yaml-key\n"
                                "evolution:\n"
                                "  mutation_enabled: true\n"
                                "agent:\n"
                                "  parallel:\n"
                                "    enabled: false\n"
                                "github:\n"
                                "  oauth_client_id: uploaded-client-id\n"
                                "  managed_clone_root: /tmp/uploaded-managed-root\n"
                            ).encode("utf-8")
                        ),
                        "application/x-yaml",
                    )
                },
            )

        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertEqual(created["status"], "created")
        self.assertEqual(created["mode"], "parallel")
        run_id = created["runId"]

        self.assertTrue(self.run_started.wait(timeout=5))
        self._wait_for_status(run_id, "running")

        current_response = self.client.get("/api/runs/current")
        self.assertEqual(current_response.status_code, 200)
        current_payload = current_response.json()
        self.assertEqual(current_payload["runId"], run_id)
        self.assertEqual(current_payload["status"], "running")
        self.assertEqual(current_payload["mode"], "parallel")
        self.assertEqual(current_payload["selectedJavaVersion"], "17")
        self.assertFalse(current_payload["mutationEnabled"])
        self.assertEqual(current_payload["phase"]["key"], "preprocessing")
        self.assertEqual(current_payload["phase"]["label"], "Preprocessing")
        self.assertEqual(current_payload["iteration"], 1)
        self.assertIn("metrics", current_payload)
        self.assertIn("mutationScore", current_payload["metrics"])
        self.assertIsNone(current_payload["metrics"]["mutationScore"])
        self.assertIsNone(current_payload["metrics"]["totalMutants"])
        self.assertEqual(current_payload["metrics"]["lineCoverage"], 0.25)
        self.assertTrue(current_payload["artifacts"]["resolvedConfig"]["exists"])
        self.assertEqual(current_payload["logStreams"]["taskIds"], ["main"])
        self.assertEqual(current_payload["logStreams"]["byTaskId"]["main"]["status"], "running")
        self.assertIsNotNone(current_payload["logStreams"]["byTaskId"]["main"]["startedAt"])

        by_id_response = self.client.get(f"/api/runs/{run_id}")
        self.assertEqual(by_id_response.status_code, 200)
        by_id_payload = by_id_response.json()
        self.assertEqual(by_id_payload["runId"], run_id)
        self.assertEqual(by_id_payload["artifacts"]["log"]["exists"], True)
        self.assertNotIn("path", by_id_payload["artifacts"]["log"])

        session = self.run_service.get_session(run_id)
        resolved_config = json.loads(
            Path(session.paths["resolved_config"]).read_text(encoding="utf-8")
        )
        self.assertEqual(resolved_config["llm"]["api_key"], "yaml-key")
        self.assertEqual(resolved_config["evolution"]["max_iterations"], 7)
        self.assertEqual(resolved_config["evolution"]["budget_llm_calls"], 42)
        self.assertEqual(resolved_config["execution"]["selected_java_version"], "17")
        self.assertFalse(resolved_config["evolution"]["mutation_enabled"])
        self.assertTrue(resolved_config["agent"]["parallel"]["enabled"])
        self.assertEqual(resolved_config["github"]["oauth_client_id"], "env-client-id")
        self.assertEqual(
            resolved_config["github"]["managed_clone_root"],
            "./sandbox/github-managed",
        )
        self.assertFalse(session.config_snapshot["evolution"]["mutation_enabled"])

        conflict = self.client.post("/api/runs", data={"projectPath": str(self.project_path)})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "active_run_conflict")

        self.release_run.set()
        self._wait_for_status(run_id, "completed")

        completed_response = self.client.get(f"/api/runs/{run_id}")
        self.assertEqual(completed_response.status_code, 200)
        completed_payload = completed_response.json()
        self.assertEqual(completed_payload["status"], "completed")
        self.assertEqual(completed_payload["selectedJavaVersion"], "17")
        self.assertFalse(completed_payload["mutationEnabled"])
        self.assertEqual(completed_payload["phase"]["key"], "completed")
        self.assertEqual(completed_payload["iteration"], 2)
        self.assertEqual(completed_payload["metrics"]["totalTests"], 4)
        self.assertIsNone(completed_payload["metrics"]["mutationScore"])
        self.assertIsNone(completed_payload["metrics"]["globalMutationScore"])
        self.assertEqual(
            completed_payload["logStreams"]["byTaskId"]["main"]["status"],
            "completed",
        )
        self.assertIsNotNone(completed_payload["logStreams"]["byTaskId"]["main"]["completedAt"])
        self.assertIsNotNone(completed_payload["logStreams"]["byTaskId"]["main"]["durationSeconds"])

        no_current = self.client.get("/api/runs/current")
        self.assertEqual(no_current.status_code, 404)
        self.assertEqual(no_current.json()["error"]["code"], "no_active_run")

    def test_post_runs_allows_only_one_active_run_when_requests_are_simultaneous(
        self,
    ) -> None:
        assert self.client is not None
        assert self.project_path is not None
        assert self.run_service is not None
        client = self.client
        run_service = self.run_service

        original_new_run_id = run_service._new_run_id

        def delayed_new_run_id() -> str:
            time.sleep(0.05)
            return original_new_run_id()

        run_service._new_run_id = delayed_new_run_id
        self.addCleanup(setattr, run_service, "_new_run_id", original_new_run_id)

        barrier = threading.Barrier(2)
        status_codes: list[int] = []

        def post_run() -> None:
            barrier.wait(timeout=5)
            response = client.post(
                "/api/runs",
                data={"projectPath": str(self.project_path)},
            )
            status_codes.append(response.status_code)

        first = threading.Thread(target=post_run)
        second = threading.Thread(target=post_run)
        first.start()
        second.start()
        first.join(timeout=5)
        second.join(timeout=5)

        self.assertCountEqual(status_codes, [201, 409])
        self.assertEqual(len(self.run_service._sessions), 1)

    def test_post_runs_rejects_missing_project_path(self) -> None:
        assert self.client is not None
        assert self.root is not None
        response = self.client.post(
            "/api/runs",
            data={"projectPath": str(self.root / "missing-project")},
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_project_path")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "path_not_found")

    def test_post_runs_rejects_non_maven_project(self) -> None:
        assert self.client is not None
        assert self.non_maven_path is not None
        response = self.client.post(
            "/api/runs",
            data={"projectPath": str(self.non_maven_path)},
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "non_maven_repository")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "non_maven_repository")

    def test_post_runs_rejects_invalid_github_repo_url(self) -> None:
        assert self.client is not None
        assert self.project_path is not None
        response = self.client.post(
            "/api/runs",
            data={
                "projectPath": str(self.project_path),
                "githubRepoUrl": "git@github.com:owner/repo.git",
            },
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_github_repo_url")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "invalid_github_repo_url")

    def test_post_runs_rejects_github_repo_when_unauthorized(self) -> None:
        assert self.client is not None
        assert self.project_path is not None
        response = self.client.post(
            "/api/runs",
            data={
                "projectPath": str(self.project_path),
                "githubRepoUrl": "https://github.com/openai/example-repo",
                "githubBaseBranch": "main",
            },
        )

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "github_unauthorized")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "github_unauthorized")

    def test_post_runs_rejects_invalid_selected_java_version(self) -> None:
        assert self.client is not None
        assert self.project_path is not None
        response = self.client.post(
            "/api/runs",
            data={
                "projectPath": str(self.project_path),
                "selectedJavaVersion": "26",
            },
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_java_version")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "invalid_java_version")


class ResultsApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    run_service: RunLifecycleService | None = None
    client: TestClient | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(create_app(run_service=self.run_service))

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _create_run(self, *, selected_java_version: str | None = None) -> str:
        assert self.run_service is not None
        assert self.project_path is not None
        session = self.run_service.create_run(
            RunRequest(
                project_path=str(self.project_path),
                selected_java_version=selected_java_version,
            ),
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        )
        return session.run_id

    def _write_completed_run_artifacts(self, run_id: str) -> None:
        assert self.run_service is not None
        session = self.run_service.get_session(run_id)

        state = AgentState()
        state.iteration = 4
        state.llm_calls = 13
        state.budget = 88
        state.total_tests = 7
        state.total_mutants = 2
        state.global_total_mutants = 5
        state.killed_mutants = 1
        state.global_killed_mutants = 4
        state.survived_mutants = 1
        state.global_survived_mutants = 1
        state.mutation_score = 0.5
        state.global_mutation_score = 0.8
        state.line_coverage = 0.9
        state.branch_coverage = 0.75
        state.current_method_coverage = 0.75
        state.current_target = {"class_name": "Calculator", "method_name": "add"}
        Path(session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["final_state"]).write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        Path(session.paths["log"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["log"]).write_text("run started\nrun completed\n", encoding="utf-8")

        database = Database(session.paths["database"])
        try:
            database.save_test_case(
                TestCase(
                    id="tc-1",
                    class_name="CalculatorAddTest",
                    target_class="Calculator",
                    methods=[
                        TestMethod(
                            method_name="testAddPositive",
                            code="assertEquals(3, calculator.add(1, 2));",
                            target_method="add",
                        ),
                        TestMethod(
                            method_name="testAddNegative",
                            code="assertEquals(-1, calculator.add(1, -2));",
                            target_method="add",
                        ),
                    ],
                    compile_success=True,
                )
            )
            database.save_mutant(
                Mutant(
                    id="mut-1",
                    class_name="Calculator",
                    method_name="add",
                    patch=MutationPatch(
                        file_path="src/main/java/Calculator.java",
                        line_start=10,
                        line_end=10,
                        original_code="return a + b;",
                        mutated_code="return a - b;",
                    ),
                    status="killed",
                    killed_by=["CalculatorAddTest.testAddPositive"],
                    survived=False,
                    evaluated_at=datetime.now(),
                )
            )
            database.save_mutant(
                Mutant(
                    id="mut-2",
                    class_name="Calculator",
                    method_name="add",
                    patch=MutationPatch(
                        file_path="src/main/java/Calculator.java",
                        line_start=11,
                        line_end=11,
                        original_code="return a + b;",
                        mutated_code="return a + 0;",
                    ),
                    status="valid",
                    survived=True,
                    evaluated_at=datetime.now(),
                )
            )
            database.save_method_coverage(
                MethodCoverage(
                    class_name="Calculator",
                    method_name="add",
                    covered_lines=[10, 11, 12],
                    missed_lines=[13],
                    total_lines=4,
                    covered_branches=1,
                    missed_branches=1,
                    total_branches=2,
                    line_coverage_rate=0.75,
                    branch_coverage_rate=0.5,
                ),
                iteration=3,
            )
        finally:
            database.close()

        self.run_service.publish_runtime_snapshot(
            run_id,
            pullRequestUrl="https://github.com/openai/example-repo/pull/42",
        )
        self.run_service.mark_completed(run_id)

    def test_results_endpoint_returns_aggregated_summary_and_artifact_metadata(
        self,
    ) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        self._write_completed_run_artifacts(run_id)

        response = self.client.get(f"/api/runs/{run_id}/results")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["runId"], run_id)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["summary"]["metrics"]["totalTests"], 7)
        self.assertEqual(payload["summary"]["metrics"]["mutationScore"], 0.5)
        self.assertEqual(payload["summary"]["tests"]["totalCases"], 1)
        self.assertEqual(payload["summary"]["tests"]["compiledCases"], 1)
        self.assertEqual(payload["summary"]["tests"]["totalMethods"], 2)
        self.assertEqual(payload["summary"]["tests"]["targetMethods"], 1)
        self.assertEqual(payload["summary"]["mutants"]["total"], 2)
        self.assertEqual(payload["summary"]["mutants"]["evaluated"], 2)
        self.assertEqual(payload["summary"]["mutants"]["killed"], 1)
        self.assertEqual(payload["summary"]["mutants"]["survived"], 1)
        self.assertEqual(payload["summary"]["coverage"]["latestIteration"], 3)
        self.assertEqual(payload["summary"]["coverage"]["methodsTracked"], 1)
        self.assertEqual(payload["summary"]["coverage"]["averageLineCoverage"], 0.75)
        self.assertEqual(payload["summary"]["coverage"]["averageBranchCoverage"], 0.5)
        self.assertTrue(payload["summary"]["sources"]["finalState"])
        self.assertTrue(payload["summary"]["sources"]["database"])
        self.assertTrue(payload["summary"]["sources"]["runLog"])
        self.assertEqual(
            payload["artifacts"]["finalState"]["downloadUrl"],
            f"/api/runs/{run_id}/artifacts/final-state",
        )
        self.assertEqual(
            payload["artifacts"]["runLog"]["downloadUrl"],
            f"/api/runs/{run_id}/artifacts/run-log",
        )
        self.assertEqual(
            payload["reportArtifact"]["downloadUrl"],
            f"/api/runs/{run_id}/artifacts/report",
        )
        self.assertTrue(payload["reportArtifact"]["exists"])
        self.assertEqual(
            payload["pullRequestUrl"],
            "https://github.com/openai/example-repo/pull/42",
        )
        self.assertNotIn("path", payload["artifacts"]["finalState"])
        self.assertNotIn("path", payload["artifacts"]["runLog"])
        self.assertGreater(payload["artifacts"]["finalState"]["sizeBytes"], 0)
        self.assertGreater(payload["artifacts"]["runLog"]["sizeBytes"], 0)
        self.assertGreater(payload["reportArtifact"]["sizeBytes"], 0)

        final_state_response = self.client.get(f"/api/runs/{run_id}/artifacts/final-state")
        self.assertEqual(final_state_response.status_code, 200)
        self.assertEqual(final_state_response.headers["content-type"], "application/json")
        self.assertIn('"total_tests": 7', final_state_response.text)

        run_log_response = self.client.get(f"/api/runs/{run_id}/artifacts/run-log")
        self.assertEqual(run_log_response.status_code, 200)
        self.assertEqual(run_log_response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertIn("run completed", run_log_response.text)

        report_response = self.client.get(f"/api/runs/{run_id}/artifacts/report")
        self.assertEqual(report_response.status_code, 200)
        self.assertEqual(report_response.headers["content-type"], "text/markdown; charset=utf-8")
        self.assertIn("attachment;", report_response.headers["content-disposition"])
        self.assertIn("report.md", report_response.headers["content-disposition"])
        expected_sections = [
            "## 执行摘要",
            "## 目标仓库与基线分支",
            "## 提交/分支信息",
            "## Java 版本",
            "## 生成测试文件列表",
            "## 关键结果指标（覆盖率/变异分数/测试数）",
            "## 失败与风险说明（若无则写“无”）",
            "## 后续建议",
        ]
        last_index = -1
        for section in expected_sections:
            current_index = report_response.text.index(section)
            self.assertGreater(current_index, last_index)
            last_index = current_index
        self.assertIn("- 变异分数: 50.00%", report_response.text)
        self.assertIn("- 测试数: 7", report_response.text)
        self.assertIn("- src/test/java/CalculatorAddTest.java", report_response.text)

    def test_results_endpoint_includes_selected_java_version(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run(selected_java_version="17")
        self._write_completed_run_artifacts(run_id)

        response = self.client.get(f"/api/runs/{run_id}/results")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selectedJavaVersion"], "17")

    def test_results_endpoint_includes_pull_request_failure_reason(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        session = self.run_service.get_session(run_id)
        Path(session.paths["report_artifact"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["report_artifact"]).write_text("# report\n", encoding="utf-8")
        self.run_service.mark_failed(run_id, "创建 GitHub PR 失败: HTTP 422 - Validation Failed")

        response = self.client.get(f"/api/runs/{run_id}/results")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["pullRequestUrl"])
        self.assertEqual(
            payload["pullRequestError"],
            "创建 GitHub PR 失败: HTTP 422 - Validation Failed",
        )

    def test_results_endpoint_gracefully_degrades_when_database_is_missing(
        self,
    ) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        session = self.run_service.get_session(run_id)
        Path(session.paths["log"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["log"]).write_text("run completed\n", encoding="utf-8")
        self.run_service.mark_completed(run_id)

        response = self.client.get(f"/api/runs/{run_id}/results")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["tests"]["totalCases"], 0)
        self.assertEqual(payload["summary"]["mutants"]["total"], 0)
        self.assertEqual(payload["summary"]["coverage"]["latestIteration"], None)
        self.assertFalse(payload["summary"]["sources"]["finalState"])
        self.assertFalse(payload["summary"]["sources"]["database"])
        self.assertTrue(payload["summary"]["sources"]["runLog"])
        self.assertEqual(payload["artifacts"]["finalState"]["exists"], False)

    def test_results_endpoint_preserves_disabled_mutation_as_null_metrics(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        session = self.run_service.get_session(run_id)

        session.config_snapshot.setdefault("evolution", {})["mutation_enabled"] = False
        self.run_service.get_run_request(run_id).mutation_enabled = False

        state = AgentState()
        state.global_mutation_enabled = False
        state.iteration = 2
        state.llm_calls = 5
        state.budget = 21
        state.total_tests = 3
        state.total_mutants = 0
        state.global_total_mutants = 0
        state.killed_mutants = 0
        state.global_killed_mutants = 0
        state.survived_mutants = 0
        state.global_survived_mutants = 0
        state.mutation_score = 0.0
        state.global_mutation_score = 0.0
        state.line_coverage = 0.7
        state.branch_coverage = 0.5

        Path(session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["final_state"]).write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        Path(session.paths["log"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["log"]).write_text("run completed\n", encoding="utf-8")
        self.run_service.mark_completed(run_id)

        response = self.client.get(f"/api/runs/{run_id}/results")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["mutationEnabled"])
        self.assertIsNone(payload["summary"]["metrics"]["mutationScore"])
        self.assertIsNone(payload["summary"]["metrics"]["globalMutationScore"])
        self.assertIsNone(payload["summary"]["metrics"]["totalMutants"])
        self.assertIsNone(payload["summary"]["metrics"]["globalTotalMutants"])
        self.assertEqual(payload["summary"]["metrics"]["totalTests"], 3)
        self.assertEqual(payload["summary"]["metrics"]["lineCoverage"], 0.7)

    def test_report_download_uses_placeholders_when_metrics_are_missing(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        session = self.run_service.get_session(run_id)

        Path(session.paths["log"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["log"]).write_text("run completed\n", encoding="utf-8")
        self.run_service.mark_completed(run_id)

        response = self.client.get(f"/api/runs/{run_id}/artifacts/report")

        self.assertEqual(response.status_code, 200)
        self.assertIn("- 行覆盖率: 未提供", response.text)
        self.assertIn("- 分支覆盖率: 未提供", response.text)
        self.assertIn("- 变异分数: 未提供", response.text)
        self.assertIn("- 测试数: 未提供", response.text)
        self.assertIn("- 未生成测试文件", response.text)
        self.assertIn("无", response.text)


class RunHistoryApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    run_service: RunLifecycleService | None = None
    client: TestClient | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(create_app(run_service=self.run_service))

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _create_run(self) -> str:
        assert self.run_service is not None
        assert self.project_path is not None
        session = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        )
        return session.run_id

    def test_history_endpoint_lists_runs_newest_first(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        first_run_id = self._create_run()
        self.run_service.mark_failed(first_run_id, "boom")

        second_run_id = self._create_run()
        second_session = self.run_service.get_session(second_run_id)
        Path(second_session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
        Path(second_session.paths["final_state"]).write_text(
            json.dumps({"iteration": 2, "total_tests": 3, "mutation_score": 0.5}),
            encoding="utf-8",
        )
        self.run_service.mark_completed(second_run_id)

        response = self.client.get("/api/runs/history")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [item["runId"] for item in payload["items"]], [second_run_id, first_run_id]
        )
        self.assertEqual(payload["items"][0]["status"], "completed")
        self.assertEqual(payload["items"][0]["iteration"], 2)
        self.assertEqual(payload["items"][0]["metrics"]["totalTests"], 3)
        self.assertEqual(payload["items"][1]["status"], "failed")
        self.assertEqual(payload["items"][1]["error"], "boom")

    def test_history_endpoint_ignores_runs_with_corrupted_manifest_or_state(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        valid_run_id = self._create_run()
        valid_session = self.run_service.get_session(valid_run_id)
        Path(valid_session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
        Path(valid_session.paths["final_state"]).write_text(
            json.dumps({"iteration": 1, "total_tests": 2}),
            encoding="utf-8",
        )
        self.run_service.mark_completed(valid_run_id)

        broken_manifest_dir = self.root / "state" / "runs" / "run-broken"
        broken_manifest_dir.mkdir(parents=True, exist_ok=True)
        (broken_manifest_dir / "session.json").write_text("{broken", encoding="utf-8")

        broken_state_run_id = self._create_run()
        broken_state_session = self.run_service.get_session(broken_state_run_id)
        Path(broken_state_session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
        Path(broken_state_session.paths["final_state"]).write_text("{broken", encoding="utf-8")
        self.run_service.mark_completed(broken_state_run_id)

        restored_service = RunLifecycleService(workspace_root=self.root)
        restored_client = TestClient(create_app(run_service=restored_service))

        response = restored_client.get("/api/runs/history")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [item["runId"] for item in payload["items"]],
            [broken_state_run_id, valid_run_id],
        )
        self.assertEqual(payload["items"][0]["iteration"], 0)
        self.assertTrue(payload["items"][0]["isHistorical"])


class StaticFrontendMountTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def test_create_app_mounts_built_frontend_without_breaking_api_routes(self) -> None:
        assert self.root is not None
        dist_path = self.root / "web" / "dist"
        dist_path.mkdir(parents=True)
        (dist_path / "index.html").write_text(
            "<html><body><div id='root'>COMET-L Web</div></body></html>",
            encoding="utf-8",
        )

        client = TestClient(
            create_app(
                run_service=RunLifecycleService(workspace_root=self.root),
                frontend_dist_path=dist_path,
            )
        )

        root_response = client.get("/")
        self.assertEqual(root_response.status_code, 200)
        self.assertIn("COMET-L Web", root_response.text)

        nested_response = client.get("/runs/run-42/results")
        self.assertEqual(nested_response.status_code, 200)
        self.assertIn("COMET-L Web", nested_response.text)

        health_response = client.get("/api/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
