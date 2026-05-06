import json
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from comet.agent.state import AgentState, ParallelAgentState, WorkerResult
from comet.config.settings import Settings
from comet.executor.coverage_parser import MethodCoverage
from comet.models import Mutant, MutationPatch, TestCase, TestMethod
from comet.store.database import Database
from comet.utils.log_context import log_context
from comet.utils.method_keys import build_method_key
from comet.web.app import app, create_app, default_config_path_for_repo_root
from comet.web.github_auth_service import GitHubAuthStatus, GitHubOAuthService, GitHubTokenStorage
from comet.web.log_router import RunLogRouter
from comet.web.routes import ApiErrorException, require_admin
from comet.web.run_service import InvalidJavaVersionError, RunLifecycleService, RunRequest
from comet.web.runtime_protocol import RuntimeEventBus, build_run_snapshot
from comet.web.storage import AuthenticatedUser, DuplicateUserError, WebDatabase

TEST_PASSWORD_HASHER = PasswordHasher()
TEST_PASSWORD = "correct-password"


def login_test_user(
    client: TestClient,
    database: WebDatabase,
    *,
    username: str = "alice",
    role: str = "user",
    password: str = TEST_PASSWORD,
) -> int:
    try:
        user_id = database.create_user(
            username=username,
            password_hash=TEST_PASSWORD_HASHER.hash(password),
            role=role,
        )
    except DuplicateUserError:
        existing_user = database.get_user_by_username(username)
        assert existing_user is not None
        user_id = existing_user.id
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    client.headers.update({"origin": "http://testserver"})
    return user_id


def authenticated_client(
    *,
    run_service: RunLifecycleService | None = None,
    default_config_path: Path | None = None,
) -> TestClient:
    owned_temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if run_service is None:
        owned_temp_dir = tempfile.TemporaryDirectory()
        resolved_run_service = RunLifecycleService(workspace_root=owned_temp_dir.name)
    else:
        resolved_run_service = run_service
    client = TestClient(
        create_app(run_service=resolved_run_service, default_config_path=default_config_path)
    )
    client.__dict__["_comet_owned_temp_dir"] = owned_temp_dir
    login_test_user(client, WebDatabase.for_workspace(resolved_run_service.workspace_root))
    return client


class HealthApiTests(unittest.TestCase):
    def test_health_endpoint_returns_ok(self) -> None:
        client = authenticated_client()

        response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "activeRunId": None})


class IdentitySchemaBootstrapTests(unittest.TestCase):
    def test_identity_schema_bootstraps_clean_web_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "state" / "web" / "comet-web.sqlite3"
            run_service = RunLifecycleService(workspace_root=root)

            client = TestClient(create_app(run_service=run_service))

            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(db_path.is_file())

            with sqlite3.connect(db_path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertEqual(
                    {"users", "sessions", "runs", "uploads", "audit_events"},
                    tables,
                )
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal",
                )
                self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
                user_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()
                }
                self.assertEqual(
                    {
                        "id",
                        "username",
                        "password_hash",
                        "role",
                        "is_active",
                        "created_at",
                        "updated_at",
                        "disabled_at",
                        "password_changed_at",
                    },
                    user_columns,
                )

    def test_identity_schema_rejects_incompatible_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "state" / "web" / "comet-web.sqlite3"
            db_path.parent.mkdir(parents=True)
            with sqlite3.connect(db_path) as connection:
                connection.execute("CREATE TABLE legacy_runs (id TEXT PRIMARY KEY)")

            with self.assertRaisesRegex(RuntimeError, "incompatible.*clean database"):
                create_app(run_service=RunLifecycleService(workspace_root=root))

    def test_admin_bootstrap_cli_creates_secure_admin_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "comet-web.sqlite3"
            env = {**os.environ, "COMET_WEB_DB_PATH": str(db_path)}
            password = "admin-secret-123"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "comet.web.admin",
                    "create-admin",
                    "--username",
                    "Admin",
                    "--password",
                    password,
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    "SELECT username, password_hash, role, is_active FROM users"
                ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row[0], "admin")
            self.assertNotEqual(row[1], password)
            self.assertTrue(str(row[1]).startswith("$argon2id$"))
            self.assertEqual(row[2], "admin")
            self.assertEqual(row[3], 1)

    def test_admin_bootstrap_cli_rejects_duplicate_username(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "comet-web.sqlite3"
            env = {**os.environ, "COMET_WEB_DB_PATH": str(db_path)}
            command = [
                sys.executable,
                "-m",
                "comet.web.admin",
                "create-admin",
                "--username",
                "admin",
                "--password",
                "admin-secret-123",
            ]

            first = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            second = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already exists", second.stderr)
            with sqlite3.connect(db_path) as connection:
                admin_count = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE username = 'admin'"
                ).fetchone()[0]
            self.assertEqual(admin_count, 1)

    def test_admin_user_management_cli_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "comet-web.sqlite3"
            env = {**os.environ, "COMET_WEB_DB_PATH": str(db_path)}
            cwd = Path(__file__).resolve().parents[1]

            def run_admin_command(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, "-m", "comet.web.admin", *args],
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            create_admin = run_admin_command(
                "create-admin",
                "--username",
                "admin",
                "--password",
                "admin-secret-123",
            )
            create_user = run_admin_command(
                "create-user",
                "--username",
                "operator",
                "--password",
                "operator-secret-123",
            )
            self.assertEqual(create_admin.returncode, 0, create_admin.stderr)
            self.assertEqual(create_user.returncode, 0, create_user.stderr)
            operator = json.loads(create_user.stdout)
            operator_id = str(operator["id"])

            promote = run_admin_command("promote-user", "--user-id", operator_id)
            reset = run_admin_command(
                "reset-password",
                "--user-id",
                operator_id,
                "--password",
                "changed-secret-123",
            )
            demote = run_admin_command("demote-user", "--user-id", operator_id)
            disable = run_admin_command("disable-user", "--user-id", operator_id)
            list_users = run_admin_command("list-users")

            self.assertEqual(promote.returncode, 0, promote.stderr)
            self.assertEqual(reset.returncode, 0, reset.stderr)
            self.assertEqual(demote.returncode, 0, demote.stderr)
            self.assertEqual(disable.returncode, 0, disable.stderr)
            self.assertEqual(list_users.returncode, 0, list_users.stderr)
            self.assertEqual(json.loads(promote.stdout)["role"], "admin")
            self.assertEqual(json.loads(demote.stdout)["role"], "user")
            self.assertFalse(json.loads(disable.stdout)["isActive"])
            serialized = list_users.stdout.lower()
            self.assertIn("operator", serialized)
            self.assertNotIn("password_hash", serialized)
            self.assertNotIn("token", serialized)

            with sqlite3.connect(db_path) as connection:
                row = connection.execute(
                    "SELECT password_hash, is_active FROM users WHERE username = 'operator'"
                ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertTrue(TEST_PASSWORD_HASHER.verify(row[0], "changed-secret-123"))
            self.assertEqual(row[1], 0)


class LocalAuthApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    default_config_path: Path | None = None
    database: WebDatabase | None = None
    client: TestClient | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text(
            "llm:\n  api_key: default-key\n  model: gpt-4\n",
            encoding="utf-8",
        )
        run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(
            create_app(run_service=run_service, default_config_path=self.default_config_path)
        )
        self.database = WebDatabase.for_workspace(self.root)

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _create_user(
        self,
        *,
        username: str = "alice",
        password: str = "correct-password",
        role: str = "user",
        is_active: bool = True,
    ) -> int:
        assert self.database is not None
        return self.database.create_user(
            username=username,
            password_hash=TEST_PASSWORD_HASHER.hash(password),
            role=role,
            is_active=is_active,
        )

    def test_auth_lifecycle_sets_hash_only_session_cookie_and_revokes_on_logout(self) -> None:
        assert self.client is not None
        assert self.database is not None
        user_id = self._create_user(username="Admin", password="secret-123", role="admin")

        login_response = self.client.post(
            "/api/auth/login",
            json={"username": "ADMIN", "password": "secret-123"},
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(
            login_response.json(),
            {"user": {"id": user_id, "username": "admin", "role": "admin"}},
        )
        self.assertIn("comet_session", login_response.cookies)
        set_cookie = login_response.headers["set-cookie"]
        self.assertIn("comet_session=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("samesite=lax", set_cookie.lower())
        self.assertIn("Path=/", set_cookie)
        self.assertNotIn("Secure", set_cookie)
        session_token = login_response.cookies["comet_session"]

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT token_hash, expires_at, revoked_at FROM sessions"
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertNotEqual(row["token_hash"], session_token)
        self.assertEqual(len(row["token_hash"]), 64)
        self.assertIsNone(row["revoked_at"])
        self.assertGreater(
            datetime.fromisoformat(row["expires_at"]).timestamp() - time.time(),
            6 * 24 * 60 * 60,
        )

        me_response = self.client.get("/api/auth/me")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["user"]["username"], "admin")

        logout_response = self.client.post(
            "/api/auth/logout",
            headers={"origin": "http://testserver"},
        )
        self.assertEqual(logout_response.status_code, 200)
        self.assertIn("comet_session=", logout_response.headers["set-cookie"])

        me_after_logout = self.client.get("/api/auth/me")
        self.assertEqual(me_after_logout.status_code, 401)
        self.assertEqual(me_after_logout.json()["error"]["code"], "auth_required")
        with self.database.connect() as connection:
            revoked_at = connection.execute("SELECT revoked_at FROM sessions").fetchone()[0]
        self.assertIsNotNone(revoked_at)

    def test_auth_rejects_disabled_users_and_throttles_failed_logins(self) -> None:
        assert self.client is not None
        self._create_user(username="disabled", password="secret-123", is_active=False)
        disabled_response = self.client.post(
            "/api/auth/login",
            json={"username": "disabled", "password": "secret-123"},
        )
        self.assertEqual(disabled_response.status_code, 401)
        self.assertEqual(disabled_response.json()["error"]["code"], "invalid_credentials")

        self._create_user(username="throttle", password="secret-123")
        statuses = [
            self.client.post(
                "/api/auth/login",
                json={"username": "throttle", "password": "wrong-password"},
            ).status_code
            for _ in range(5)
        ]
        self.assertEqual(statuses[:4], [401, 401, 401, 401])
        self.assertEqual(statuses[4], 429)

        locked_response = self.client.post(
            "/api/auth/login",
            json={"username": "throttle", "password": "secret-123"},
        )
        self.assertEqual(locked_response.status_code, 429)
        self.assertEqual(locked_response.json()["error"]["code"], "login_locked")

    def test_origin_guard_rejects_cookie_authenticated_mutation_before_logout_revocation(
        self,
    ) -> None:
        assert self.client is not None
        assert self.database is not None
        self._create_user(username="origin-user", password="secret-123")
        login_response = self.client.post(
            "/api/auth/login",
            json={"username": "origin-user", "password": "secret-123"},
        )
        self.assertEqual(login_response.status_code, 200)

        hostile_logout = self.client.post(
            "/api/auth/logout",
            headers={"origin": "https://evil.example"},
        )

        self.assertEqual(hostile_logout.status_code, 403)
        self.assertEqual(hostile_logout.json()["error"]["code"], "csrf_origin_forbidden")
        with self.database.connect() as connection:
            revoked_at = connection.execute("SELECT revoked_at FROM sessions").fetchone()[0]
        self.assertIsNone(revoked_at)

        allowed_logout = self.client.post(
            "/api/auth/logout",
            headers={"referer": "http://testserver/settings"},
        )
        self.assertEqual(allowed_logout.status_code, 200)


class AdminUserManagementApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    default_config_path: Path | None = None
    database: WebDatabase | None = None
    client: TestClient | None = None
    admin_id: int | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text(
            (
                "llm:\n"
                "  api_key: default-key\n"
                "github:\n"
                "  oauth_client_secret: yaml-secret\n"
                "deployment:\n"
                "  secure_auth_cookies: false\n"
                "  allowed_origins:\n"
                "    - https://console.example\n"
                "  allow_local_path_mode: true\n"
                f"  local_path_allowlist:\n    - {self.root.as_posix()}\n"
                "  global_max_running_tasks: 3\n"
                "  per_user_max_running_tasks: 2\n"
                "  global_max_pending_tasks: 7\n"
                "  per_user_max_pending_tasks: 4\n"
                "  upload_retention_hours: 12\n"
                "  run_artifact_retention_days: 9\n"
            ),
            encoding="utf-8",
        )
        run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(
            create_app(run_service=run_service, default_config_path=self.default_config_path)
        )
        self.database = WebDatabase.for_workspace(self.root)
        self.admin_id = login_test_user(
            self.client,
            self.database,
            username="admin-manager",
            role="admin",
        )

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def test_admin_user_management_create_list_role_reset_and_disable_are_safe(self) -> None:
        assert self.client is not None
        assert self.database is not None

        created_response = self.client.post(
            "/api/admin/users",
            json={"username": "Managed", "password": "managed-secret", "role": "user"},
        )
        self.assertEqual(created_response.status_code, 201, created_response.text)
        created = created_response.json()
        user_id = created["id"]
        self.assertEqual(created["username"], "managed")
        self.assertEqual(created["role"], "user")
        self.assertTrue(created["isActive"])
        created_serialized = json.dumps(created).lower()
        self.assertNotIn("managed-secret", created_serialized)
        self.assertNotIn("password_hash", created_serialized)
        self.assertNotIn("hash", created_serialized)
        self.assertNotIn("token", created_serialized)

        list_response = self.client.get("/api/admin/users")
        self.assertEqual(list_response.status_code, 200)
        users_payload = list_response.json()["users"]
        self.assertIn("managed", {user["username"] for user in users_payload})
        serialized = json.dumps(users_payload, ensure_ascii=False).lower()
        self.assertNotIn("password_hash", serialized)
        self.assertNotIn("token_hash", serialized)
        self.assertNotIn("comet_session", serialized)

        promote_response = self.client.post(
            f"/api/admin/users/{user_id}/role",
            json={"role": "admin"},
        )
        self.assertEqual(promote_response.status_code, 200)
        self.assertEqual(promote_response.json()["role"], "admin")

        reset_response = self.client.post(
            f"/api/admin/users/{user_id}/reset-password",
            json={"password": "new-managed-secret"},
        )
        self.assertEqual(reset_response.status_code, 200)
        managed_user = self.database.get_user_by_username("managed")
        self.assertIsNotNone(managed_user)
        assert managed_user is not None
        self.assertTrue(
            TEST_PASSWORD_HASHER.verify(managed_user.password_hash, "new-managed-secret")
        )

        demote_response = self.client.post(
            f"/api/admin/users/{user_id}/role",
            json={"role": "user"},
        )
        self.assertEqual(demote_response.status_code, 200)
        self.assertEqual(demote_response.json()["role"], "user")

        disable_response = self.client.post(f"/api/admin/users/{user_id}/disable")
        self.assertEqual(disable_response.status_code, 200)
        self.assertFalse(disable_response.json()["isActive"])

    def test_user_management_admin_required_for_ordinary_users(self) -> None:
        assert self.client is not None
        assert self.database is not None
        login_test_user(self.client, self.database, username="regular-manager", role="user")

        response = self.client.get("/api/admin/users")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "admin_required")

    def test_disabled_session_returns_401_immediately_after_admin_disable(self) -> None:
        assert self.root is not None
        assert self.database is not None
        assert self.default_config_path is not None
        user_client = TestClient(
            create_app(
                run_service=RunLifecycleService(workspace_root=self.root),
                default_config_path=self.default_config_path,
            )
        )
        user_id = login_test_user(user_client, self.database, username="disable-me", role="user")
        active_before = user_client.get("/api/auth/me")
        self.assertEqual(active_before.status_code, 200)

        assert self.client is not None
        disable_response = self.client.post(f"/api/admin/users/{user_id}/disable")
        self.assertEqual(disable_response.status_code, 200)

        after_disable = user_client.get("/api/auth/me")
        self.assertEqual(after_disable.status_code, 401)
        self.assertEqual(after_disable.json()["error"]["code"], "auth_required")

        evidence_path = Path(".sisyphus/evidence/task-15-disabled-session.json")
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "userId": user_id,
                    "beforeStatus": active_before.status_code,
                    "disableStatus": disable_response.status_code,
                    "afterStatus": after_disable.status_code,
                    "afterCode": after_disable.json()["error"]["code"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_last_admin_protected_for_disable_and_demote(self) -> None:
        assert self.client is not None
        assert self.admin_id is not None

        disable_response = self.client.post(f"/api/admin/users/{self.admin_id}/disable")
        demote_response = self.client.post(
            f"/api/admin/users/{self.admin_id}/role",
            json={"role": "user"},
        )

        self.assertEqual(disable_response.status_code, 409)
        self.assertEqual(disable_response.json()["error"]["code"], "last_admin_protected")
        self.assertEqual(demote_response.status_code, 409)
        self.assertEqual(demote_response.json()["error"]["code"], "last_admin_protected")

        evidence_path = Path(".sisyphus/evidence/task-15-last-admin.txt")
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            "disable=last_admin_protected\nrole=last_admin_protected\n",
            encoding="utf-8",
        )

    def test_public_config_exposes_deployment_controls_without_secrets(self) -> None:
        assert self.client is not None

        response = self.client.get("/api/deployment/public-config")

        self.assertEqual(response.status_code, 200)
        deployment = response.json()["deployment"]
        self.assertFalse(deployment["secureAuthCookies"])
        self.assertEqual(deployment["allowedOrigins"], ["https://console.example"])
        self.assertTrue(deployment["allowLocalPathMode"])
        self.assertNotIn("localPathAllowlist", deployment)
        self.assertTrue(deployment["localPathAllowlistConfigured"])
        self.assertEqual(deployment["localPathAllowlistCount"], 1)
        self.assertEqual(deployment["globalMaxRunningTasks"], 3)
        self.assertEqual(deployment["perUserMaxPendingTasks"], 4)
        self.assertEqual(deployment["uploadRetentionHours"], 12)
        self.assertEqual(deployment["runArtifactRetentionDays"], 9)
        serialized = json.dumps(response.json(), ensure_ascii=False).lower()
        assert self.root is not None
        self.assertNotIn(self.root.as_posix().lower(), serialized)
        self.assertNotIn("/home", serialized)
        self.assertNotIn("state/users", serialized)
        self.assertNotIn("sandbox/users", serialized)
        self.assertNotIn("config.yaml", serialized)
        self.assertNotIn("default-key", serialized)
        self.assertNotIn("yaml-secret", serialized)
        self.assertNotIn("oauth", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("api_key", serialized)


class DefaultConfigPathTests(unittest.TestCase):
    def test_prefers_config_yaml_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.yaml"
            config_example_path = root / "config.example.yaml"
            config_example_path.write_text("llm:\n  api_key: example\n", encoding="utf-8")
            config_path.write_text("llm:\n  api_key: live\n", encoding="utf-8")

            self.assertEqual(default_config_path_for_repo_root(root), config_path)

    def test_ignores_config_example_when_resolving_default_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_example_path = root / "config.example.yaml"
            config_example_path.write_text("llm:\n  api_key: example\n", encoding="utf-8")

            self.assertEqual(default_config_path_for_repo_root(root), root / "config.yaml")


class ConfigApiTests(unittest.TestCase):
    def test_app_is_importable(self) -> None:
        self.assertIsNotNone(app)
        self.assertTrue(callable(app))

    def test_defaults_endpoint_returns_normalized_config(self) -> None:
        client = authenticated_client()

        response = client.get("/api/config/defaults")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("config", payload)
        self.assertIsInstance(payload["config"]["llm"]["model"], str)
        self.assertTrue(payload["config"]["llm"]["model"])
        self.assertTrue(payload["config"]["evolution"]["mutation_enabled"])
        self.assertFalse(payload["config"]["preprocessing"]["exit_after_preprocessing"])
        self.assertNotIn("paths", payload["config"])

    def test_parse_valid_yaml_returns_normalized_config(self) -> None:
        client = authenticated_client()

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
        self.assertEqual(payload["config"]["llm"]["api_key"], "[REDACTED]")
        self.assertEqual(payload["config"]["llm"]["model"], "gpt-4o-mini")
        self.assertEqual(payload["config"]["execution"]["timeout"], 123)
        self.assertTrue(payload["config"]["preprocessing"]["exit_after_preprocessing"])
        self.assertFalse(payload["config"]["evolution"]["mutation_enabled"])
        self.assertTrue(payload["config"]["agent"]["parallel"]["enabled"])
        self.assertNotIn("paths", payload["config"])

    def test_parse_yaml_rejects_invalid_mutation_enabled_type(self) -> None:
        client = authenticated_client()

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
        client = authenticated_client()

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
        client = authenticated_client()

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
        client = authenticated_client()

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
        client = authenticated_client()

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
        client = authenticated_client()

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
        client = authenticated_client()

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
            client = authenticated_client(
                run_service=RunLifecycleService(workspace_root=root),
                default_config_path=default_config_path,
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
            self.assertEqual(payload["oauth_client_secret"], "[REDACTED]")
            self.assertEqual(
                payload["oauth_redirect_uri"],
                "http://127.0.0.1:9000/api/github/auth/callback",
            )
            self.assertEqual(payload["oauth_scope"], "public_repo")

    def test_policy_redacts_secret_like_config_snapshot_values(self) -> None:
        settings = Settings.model_validate(
            {
                "llm": {"api_key": "llm-secret"},
                "knowledge": {"embedding": {"api_key": "embedding-secret"}},
                "github": {
                    "oauth_client_secret": "oauth-secret",
                    "encrypted_token_store_path": "/tmp/token.enc",
                    "encrypted_key_store_path": "/tmp/token.key",
                },
            }
        )

        from comet.config.policy import redacted_settings_dict

        snapshot, annotations = redacted_settings_dict(settings)
        serialized = json.dumps(snapshot, ensure_ascii=False)

        self.assertNotIn("llm-secret", serialized)
        self.assertNotIn("embedding-secret", serialized)
        self.assertNotIn("oauth-secret", serialized)
        self.assertNotIn("/tmp/token.enc", serialized)
        self.assertEqual(snapshot["llm"]["api_key"], "[REDACTED]")
        self.assertIn("github.encrypted_token_store_path", annotations.redacted_fields)


def _zip_bytes(entries: dict[str, bytes | str], *, symlink_name: str | None = None) -> BytesIO:
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for name, content in entries.items():
            payload = content.encode("utf-8") if isinstance(content, str) else content
            zip_file.writestr(name, payload)
        if symlink_name is not None:
            link_info = zipfile.ZipInfo(symlink_name)
            link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zip_file.writestr(link_info, "target.txt")
    archive.seek(0)
    return archive


class UploadApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    database: WebDatabase | None = None
    client: TestClient | None = None
    user_id: int | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        default_config_path = self.root / "config.example.yaml"
        default_config_path.write_text("llm:\n  api_key: default-key\n", encoding="utf-8")
        run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(
            create_app(run_service=run_service, default_config_path=default_config_path)
        )
        self.database = WebDatabase.for_workspace(self.root)
        self.user_id = login_test_user(self.client, self.database)

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _post_upload(self, path: str, archive: BytesIO, filename: str = "upload.zip") -> Any:
        assert self.client is not None
        return self.client.post(
            path,
            files={"file": (filename, archive, "application/octet-stream")},
        )

    def _upload_row(self, upload_id: str) -> sqlite3.Row:
        assert self.database is not None
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM uploads WHERE id = ?",
                (upload_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return row

    def test_project_upload_accepts_valid_zip_and_persists_user_scoped_metadata(self) -> None:
        assert self.root is not None
        assert self.user_id is not None
        archive = _zip_bytes(
            {
                "sample-project/pom.xml": "<project/>",
                "sample-project/src/main/java/App.java": "class App {}",
            }
        )

        response = self._post_upload("/api/uploads/project", archive, "project.zip")

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["kind"], "project")
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["originalFilename"], "project.zip")
        self.assertEqual(payload["extractedRoot"], "sample-project")
        self.assertNotIn(str(self.root), response.text)

        upload_root = (
            self.root / "sandbox" / "users" / str(self.user_id) / "uploads" / payload["uploadId"]
        )
        self.assertTrue((upload_root / "raw" / "project.zip").is_file())
        self.assertTrue((upload_root / "extracted" / "sample-project" / "pom.xml").is_file())
        row = self._upload_row(payload["uploadId"])
        self.assertEqual(row["user_id"], self.user_id)
        self.assertEqual(row["kind"], "project")
        self.assertEqual(row["status"], "ready")
        metadata = json.loads(row["path_metadata_json"])
        self.assertEqual(metadata["original_filename"], "project.zip")
        self.assertGreater(metadata["size_bytes"], 0)

    def test_bug_reports_upload_accepts_allowed_report_extensions(self) -> None:
        archive = _zip_bytes(
            {
                "reports/bug.md": "# Bug",
                "reports/fix.patch": "diff --git a/A.java b/A.java",
                "reports/notes.txt": "plain text",
            }
        )

        response = self._post_upload("/api/uploads/bug-reports", archive, "reports.zip")

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["kind"], "bug_reports")
        self.assertEqual(payload["status"], "ready")
        row = self._upload_row(payload["uploadId"])
        self.assertEqual(row["kind"], "bug_reports")

    def test_project_upload_rejects_zip_traversal_without_writing_escape(self) -> None:
        assert self.root is not None
        archive = _zip_bytes({"../outside.txt": "escape", "pom.xml": "<project/>"})

        response = self._post_upload("/api/uploads/project", archive, "evil.zip")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsafe_zip_entry")
        self.assertFalse((self.root / "outside.txt").exists())

    def test_bug_reports_upload_rejects_unsupported_extension(self) -> None:
        archive = _zip_bytes({"reports/exploit.sh": "#!/bin/sh"})

        response = self._post_upload("/api/uploads/bug-reports", archive, "reports.zip")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsupported_bug_report_file")

    def test_project_upload_rejects_non_zip_content(self) -> None:
        response = self._post_upload("/api/uploads/project", BytesIO(b"not a zip"), "project.zip")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_zip")

    def test_project_upload_rejects_symlink_entries(self) -> None:
        archive = _zip_bytes({"pom.xml": "<project/>"}, symlink_name="linked.txt")

        response = self._post_upload("/api/uploads/project", archive, "symlink.zip")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsafe_zip_entry")

    def test_project_upload_rejects_duplicate_normalized_paths(self) -> None:
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("sample-project/pom.xml", "<project/>")
            zip_file.writestr("sample-project/src/main/java/App.java", "class App {}")
            zip_file.writestr("sample-project/./src/main/java/App.java", "class App2 {}")
        archive.seek(0)

        response = self._post_upload("/api/uploads/project", archive, "duplicate.zip")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsafe_zip_entry")


class UploadRunApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    bug_reports_path: Path | None = None
    default_config_path: Path | None = None
    release_run: threading.Event | None = None
    run_started: threading.Event | None = None
    run_service: RunLifecycleService | None = None
    database: WebDatabase | None = None
    client: TestClient | None = None
    user_id: int | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "allowlisted-project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.bug_reports_path = self.root / "allowlisted-reports"
        self.bug_reports_path.mkdir()
        (self.bug_reports_path / "bug.md").write_text("# Bug", encoding="utf-8")
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text("llm:\n  api_key: default-key\n", encoding="utf-8")
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
            del project_path, components, resume_state
            assert self.run_started is not None
            assert self.release_run is not None
            self.run_started.set()
            if not self.release_run.wait(timeout=5):
                raise TimeoutError("run release timeout")

        self.client = TestClient(
            create_app(
                run_service=self.run_service,
                default_config_path=self.default_config_path,
                system_initializer=fake_initialize,
                evolution_runner=fake_run,
            )
        )
        self.database = WebDatabase.for_workspace(self.root)
        self.user_id = login_test_user(self.client, self.database)

    def tearDown(self) -> None:
        if self.release_run is not None:
            self.release_run.set()
        if self.run_service is not None:
            joined: set[str] = set()
            while True:
                pending_threads = [
                    (run_id, thread)
                    for run_id, thread in self.run_service._threads.items()
                    if run_id not in joined
                ]
                if not pending_threads:
                    break
                for run_id, thread in pending_threads:
                    thread.join(timeout=5)
                    joined.add(run_id)
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _upload_project(self, filename: str = "project.zip") -> str:
        assert self.client is not None
        response = self.client.post(
            "/api/uploads/project",
            files={
                "file": (
                    filename,
                    _zip_bytes(
                        {
                            "sample-project/pom.xml": "<project/>",
                            "sample-project/src/main/java/App.java": "class App {}",
                        }
                    ),
                    "application/octet-stream",
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return str(response.json()["uploadId"])

    def _upload_bug_reports(self) -> str:
        assert self.client is not None
        response = self.client.post(
            "/api/uploads/bug-reports",
            files={
                "file": (
                    "reports.zip",
                    _zip_bytes({"reports/bug.md": "# Bug"}),
                    "application/octet-stream",
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return str(response.json()["uploadId"])

    def _upload_row(self, upload_id: str) -> sqlite3.Row:
        assert self.database is not None
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM uploads WHERE id = ?",
                (upload_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return row

    def _run_count(self) -> int:
        assert self.database is not None
        with self.database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM runs").fetchone()
        return int(row["count"])

    def test_run_from_upload_id_marks_uploads_used_and_records_source_metadata(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        project_upload_id = self._upload_project()
        bug_reports_upload_id = self._upload_bug_reports()

        response = self.client.post(
            "/api/runs",
            data={
                "projectUploadId": project_upload_id,
                "bugReportsUploadId": bug_reports_upload_id,
                "budget": "42",
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertIn(payload["status"], {"pending", "starting", "running"})
        self.assertIsNotNone(payload["queuePosition"])
        self.assertIn("configPolicy", payload)
        self.assertEqual(payload["uploadSource"]["projectUploadId"], project_upload_id)
        self.assertEqual(payload["uploadSource"]["bugReportsUploadId"], bug_reports_upload_id)
        self.assertNotIn(str(self.root), response.text)

        project_row = self._upload_row(project_upload_id)
        reports_row = self._upload_row(bug_reports_upload_id)
        self.assertIsNotNone(project_row["used_at"])
        self.assertIsNotNone(reports_row["used_at"])
        run_id = payload["runId"]
        run_request = self.run_service.get_run_request(run_id)
        self.assertEqual(run_request.project_path, project_row["extracted_path"])
        self.assertEqual(run_request.bug_reports_dir, reports_row["extracted_path"])
        assert self.database is not None
        record = self.database.get_run_record(run_id)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.project_source_type, "upload")
        self.assertEqual(record.path_metadata["uploadSource"]["projectUploadId"], project_upload_id)

    def test_local_path_forbidden_for_ordinary_user_creates_no_run(self) -> None:
        assert self.client is not None
        assert self.project_path is not None
        assert self.bug_reports_path is not None

        response = self.client.post(
            "/api/runs",
            data={
                "projectPath": str(self.project_path),
                "bugReportsDir": str(self.bug_reports_path),
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "local_path_forbidden")
        self.assertEqual(self._run_count(), 0)
        self.assertNotIn(str(self.project_path), response.text)
        self.assertNotIn(str(self.bug_reports_path), response.text)

    def test_upload_id_owned_by_alice_is_hidden_from_bob(self) -> None:
        assert self.client is not None
        assert self.database is not None
        project_upload_id = self._upload_project()
        login_test_user(self.client, self.database, username="bob")

        response = self.client.post(
            "/api/runs",
            data={"projectUploadId": project_upload_id},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "upload_not_found")
        self.assertEqual(self._run_count(), 0)
        self.assertNotIn("alice", response.text)
        self.assertNotIn("sandbox", response.text)

    def test_invalid_optional_bug_report_upload_does_not_consume_project_upload(self) -> None:
        assert self.client is not None
        assert self.database is not None
        project_upload_id = self._upload_project()
        wrong_kind_bug_reports_upload_id = self._upload_project("wrong-kind-bug-reports.zip")

        response = self.client.post(
            "/api/runs",
            data={
                "projectUploadId": project_upload_id,
                "bugReportsUploadId": wrong_kind_bug_reports_upload_id,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "upload_not_found")
        self.assertEqual(self._run_count(), 0)
        self.assertIsNone(self._upload_row(project_upload_id)["used_at"])

    def test_queue_limit_failure_does_not_consume_project_upload(self) -> None:
        assert self.client is not None
        assert self.default_config_path is not None
        self.default_config_path.write_text(
            (
                "llm:\n"
                "  api_key: default-key\n"
                "deployment:\n"
                "  global_max_running_tasks: 1\n"
                "  per_user_max_running_tasks: 1\n"
                "  global_max_pending_tasks: 1\n"
                "  per_user_max_pending_tasks: 1\n"
            ),
            encoding="utf-8",
        )
        first_upload_id = self._upload_project("first-queue.zip")
        second_upload_id = self._upload_project("second-queue.zip")
        third_upload_id = self._upload_project("third-queue.zip")
        first = self.client.post("/api/runs", data={"projectUploadId": first_upload_id})
        self.assertEqual(first.status_code, 201, first.text)
        assert self.run_started is not None
        self.assertTrue(self.run_started.wait(timeout=5))
        second = self.client.post("/api/runs", data={"projectUploadId": second_upload_id})
        self.assertEqual(second.status_code, 201, second.text)

        third = self.client.post("/api/runs", data={"projectUploadId": third_upload_id})

        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.json()["error"]["code"], "queue_limit_exceeded")
        self.assertEqual(self._run_count(), 2)
        self.assertIsNotNone(self._upload_row(first_upload_id)["used_at"])
        self.assertIsNotNone(self._upload_row(second_upload_id)["used_at"])
        self.assertIsNone(self._upload_row(third_upload_id)["used_at"])

    def test_upload_id_reuse_is_rejected_without_creating_second_run(self) -> None:
        assert self.client is not None
        project_upload_id = self._upload_project()
        first = self.client.post("/api/runs", data={"projectUploadId": project_upload_id})
        self.assertEqual(first.status_code, 201, first.text)

        second = self.client.post("/api/runs", data={"projectUploadId": project_upload_id})

        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"]["code"], "upload_already_used")
        self.assertEqual(self._run_count(), 1)

    def test_upload_consumption_race_failure_leaves_no_orphan_pending_run(self) -> None:
        assert self.client is not None
        assert self.database is not None
        project_upload_id = self._upload_project("race-lost.zip")

        with patch.object(
            WebDatabase,
            "mark_upload_used_once",
            return_value=None,
        ):
            response = self.client.post("/api/runs", data={"projectUploadId": project_upload_id})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "upload_already_used")
        self.assertEqual(self._run_count(), 0)
        self.assertIsNone(self._upload_row(project_upload_id)["used_at"])

    def test_create_run_failure_rolls_back_upload_consumption(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        project_upload_id = self._upload_project("create-run-fails.zip")

        with patch.object(
            self.run_service,
            "create_run",
            side_effect=InvalidJavaVersionError("internal selected-java-version detail"),
        ):
            response = self.client.post("/api/runs", data={"projectUploadId": project_upload_id})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_java_version")
        self.assertEqual(self._run_count(), 0)
        self.assertIsNone(self._upload_row(project_upload_id)["used_at"])
        self.assertNotIn("internal selected-java-version detail", response.text)

    def test_second_upload_consumption_failure_rolls_back_first_upload(self) -> None:
        assert self.client is not None
        assert self.database is not None
        project_upload_id = self._upload_project("partial-project.zip")
        bug_reports_upload_id = self._upload_bug_reports()
        original_mark_upload_used_once = self.database.mark_upload_used_once

        def fail_second_mark(
            *,
            upload_id: str,
            user_id: int,
            kind: str,
            status: str,
        ) -> Any:
            if upload_id == bug_reports_upload_id:
                return None
            return original_mark_upload_used_once(
                upload_id=upload_id,
                user_id=user_id,
                kind=kind,
                status=status,
            )

        with patch.object(WebDatabase, "mark_upload_used_once", side_effect=fail_second_mark):
            response = self.client.post(
                "/api/runs",
                data={
                    "projectUploadId": project_upload_id,
                    "bugReportsUploadId": bug_reports_upload_id,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "upload_already_used")
        self.assertEqual(self._run_count(), 0)
        self.assertIsNone(self._upload_row(project_upload_id)["used_at"])
        self.assertIsNone(self._upload_row(bug_reports_upload_id)["used_at"])

    def test_admin_local_path_mode_disabled_returns_403_and_creates_no_run(self) -> None:
        assert self.client is not None
        assert self.database is not None
        assert self.project_path is not None
        login_test_user(self.client, self.database, username="admin", role="admin")

        response = self.client.post("/api/runs", data={"projectPath": str(self.project_path)})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "local_path_forbidden")
        self.assertEqual(self._run_count(), 0)
        self.assertNotIn(str(self.project_path), response.text)

    def test_admin_local_path_enabled_and_allowlisted_succeeds(self) -> None:
        assert self.client is not None
        assert self.database is not None
        assert self.default_config_path is not None
        assert self.project_path is not None
        assert self.bug_reports_path is not None
        assert self.root is not None
        self.default_config_path.write_text(
            (
                "llm:\n"
                "  api_key: default-key\n"
                "deployment:\n"
                "  allow_local_path_mode: true\n"
                f"  local_path_allowlist:\n    - {self.root.as_posix()}\n"
            ),
            encoding="utf-8",
        )
        login_test_user(self.client, self.database, username="admin-allow", role="admin")

        response = self.client.post(
            "/api/runs",
            data={
                "projectPath": str(self.project_path),
                "bugReportsDir": str(self.bug_reports_path),
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["uploadSource"], {"mode": "local_path"})
        assert self.run_service is not None
        run_request = self.run_service.get_run_request(payload["runId"])
        self.assertEqual(run_request.project_path, str(self.project_path.resolve()))
        self.assertEqual(run_request.bug_reports_dir, str(self.bug_reports_path.resolve()))


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

            if request.url.path.endswith("/user/repos"):
                authorization = request.headers.get("Authorization", "")
                if authorization != "Bearer gho-test-valid":
                    return httpx.Response(401, json={"message": "Bad credentials"})
                return httpx.Response(
                    200,
                    json=[
                        {
                            "name": "beta-repo",
                            "full_name": "testuser/beta-repo",
                            "html_url": "https://github.com/testuser/beta-repo",
                            "description": "Beta repository",
                            "private": True,
                            "updated_at": "2026-04-12T09:30:00Z",
                        },
                        {
                            "name": "alpha-repo",
                            "full_name": "testuser/alpha-repo",
                            "html_url": "https://github.com/testuser/alpha-repo",
                            "description": None,
                            "private": False,
                            "updated_at": "2026-04-11T10:00:00Z",
                        },
                    ],
                )

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
        login_test_user(self.client, WebDatabase.for_workspace(self.root), role="admin")

    def tearDown(self) -> None:
        if self.run_service is not None:
            for thread in list(self.run_service._threads.values()):
                thread.join(timeout=5)
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

    def test_github_auth_and_repository_routes_require_admin_for_ordinary_users(self) -> None:
        assert self.client is not None
        assert self.root is not None
        login_test_user(
            self.client,
            WebDatabase.for_workspace(self.root),
            username="github-ordinary",
            role="user",
        )

        responses = [
            self.client.get("/api/github/auth/status"),
            self.client.get("/api/github/auth/connect-url"),
            self.client.get("/api/github/auth/callback?code=ok-code&state=missing-state"),
            self.client.get("/api/github/repositories"),
            self.client.post(
                "/api/github/auth/disconnect",
                headers={"origin": "http://testserver"},
            ),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(response.json()["error"]["code"], "admin_required")

    def test_github_repositories_requires_authorization(self) -> None:
        assert self.client is not None

        response = self.client.get("/api/github/repositories")

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "github_auth_required")

    def test_github_repositories_returns_authorized_repository_list(self) -> None:
        assert self.client is not None

        connect_response = self.client.get("/api/github/auth/connect-url")
        state = parse_qs(urlparse(connect_response.json()["connectUrl"]).query)["state"][0]
        callback_response = self.client.get(f"/api/github/auth/callback?code=ok-code&state={state}")
        self.assertEqual(callback_response.status_code, 200)

        response = self.client.get("/api/github/repositories")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["repositories"]), 2)
        self.assertEqual(payload["repositories"][0]["fullName"], "testuser/beta-repo")
        self.assertTrue(payload["repositories"][0]["private"])
        self.assertEqual(
            payload["repositories"][1]["url"],
            "https://github.com/testuser/alpha-repo",
        )

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
            "/?github_oauth=error&message=GitHub+%E6%8E%88%E6%9D%83%E5%A4%B1%E8%B4%A5%EF%BC%8C%E8%AF%B7%E9%87%8D%E6%96%B0%E5%8F%91%E8%B5%B7%E6%8E%88%E6%9D%83%E3%80%82",
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
        restarted_client.cookies.update(self.client.cookies)

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

        disconnect_response = self.client.post(
            "/api/github/auth/disconnect",
            headers={"origin": "http://testserver"},
        )
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
        login_test_user(self.client, WebDatabase.for_workspace(self.root), role="admin")

    def tearDown(self) -> None:
        if self.run_service is not None:
            for thread in list(self.run_service._threads.values()):
                thread.join(timeout=5)
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

    def test_post_runs_github_repo_requires_admin_for_ordinary_users(self) -> None:
        assert self.client is not None
        assert self.root is not None
        database = WebDatabase.for_workspace(self.root)
        login_test_user(
            self.client,
            database,
            username="github-run-ordinary",
            role="user",
        )

        response = self.client.post(
            "/api/runs",
            data={"githubRepoUrl": "https://github.com/openai/example-repo.git"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "admin_required")
        with database.connect() as connection:
            run_count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.assertEqual(run_count, 0)

    def test_post_runs_imports_github_repo_without_project_path_field(self) -> None:
        assert self.client is not None
        assert self.run_service is not None

        with patch(
            "comet.web.repo_import_service.subprocess.run",
            side_effect=self._mock_clone_success,
        ):
            response = self.client.post(
                "/api/runs",
                data={
                    "githubRepoUrl": "https://github.com/openai/example-repo.git",
                },
            )

        self.assertEqual(response.status_code, 201)
        run_id = response.json()["runId"]
        run_request = self.run_service.get_run_request(run_id)
        self.assertTrue(run_request.project_path)

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

    def test_post_runs_reports_missing_git_binary_during_import(self) -> None:
        assert self.client is not None

        with patch(
            "comet.web.repo_import_service.subprocess.run",
            side_effect=FileNotFoundError(2, "No such file or directory", "git"),
        ):
            response = self.client.post(
                "/api/runs",
                data={
                    "projectPath": "/not-used-in-github-mode",
                    "githubRepoUrl": "https://github.com/openai/example-repo.git",
                },
            )

        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "git_clone_failed")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "git_clone_failed")
        self.assertEqual(payload["error"]["fieldErrors"][0]["message"], "仓库克隆失败。")


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
    user_id: int | None = None

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
        self.user_id = login_test_user(self.client, WebDatabase.for_workspace(self.root))

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _create_run(self) -> str:
        assert self.run_service is not None
        assert self.project_path is not None
        session = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            user_id=self.user_id,
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

    def test_restarted_log_and_event_endpoints_tolerate_missing_runtime_routers(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        run_id = self._create_run()
        self.run_service.mark_completed(run_id)
        restarted_service = RunLifecycleService(workspace_root=self.root)
        restarted_service._event_buses.pop(run_id, None)
        restarted_service._log_routers.pop(run_id, None)
        restarted_client = TestClient(create_app(run_service=restarted_service))
        restarted_client.cookies.update(self.client.cookies)

        logs_response = restarted_client.get(f"/api/runs/{run_id}/logs")
        main_log_response = restarted_client.get(f"/api/runs/{run_id}/logs/main")
        events_response = restarted_client.get(f"/api/runs/{run_id}/events")

        self.assertEqual(logs_response.status_code, 200)
        self.assertEqual(logs_response.json()["streams"]["taskIds"], ["main"])
        self.assertEqual(main_log_response.status_code, 200)
        self.assertEqual(main_log_response.json()["entries"], [])
        self.assertEqual(events_response.status_code, 200)
        events = self._parse_sse(events_response.text)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "run.snapshot")
        self.assertEqual(events[0]["data"]["snapshot"]["status"], "completed")


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
    user_id: int | None = None

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
            (
                "llm:\n"
                "  api_key: default-key\n"
                "  model: gpt-4\n"
                "deployment:\n"
                "  max_budget: 500\n"
                "  max_run_timeout_seconds: 7200\n"
            ),
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
        self.user_id = login_test_user(self.client, WebDatabase.for_workspace(self.root))

    def tearDown(self) -> None:
        if self.release_run is not None:
            self.release_run.set()
        if self.run_service is not None:
            joined: set[str] = set()
            while True:
                pending_threads = [
                    (run_id, thread)
                    for run_id, thread in self.run_service._threads.items()
                    if run_id not in joined
                ]
                if not pending_threads:
                    break
                for run_id, thread in pending_threads:
                    thread.join(timeout=5)
                    joined.add(run_id)
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

    def _create_owned_run(self, user_id: int) -> str:
        assert self.run_service is not None
        assert self.project_path is not None
        session = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            user_id=user_id,
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        )
        self.run_service.mark_completed(session.run_id)
        return session.run_id

    def _upload_project_for_run(self, filename: str = "project.zip") -> str:
        assert self.client is not None
        response = self.client.post(
            "/api/uploads/project",
            files={
                "file": (
                    filename,
                    _zip_bytes(
                        {
                            "sample-project/pom.xml": "<project/>",
                            "sample-project/src/main/java/App.java": "class App {}",
                        }
                    ),
                    "application/octet-stream",
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return str(response.json()["uploadId"])

    def test_run_routes_require_authentication_by_default(self) -> None:
        assert self.client is not None
        self.client.cookies.clear()

        response = self.client.get("/api/runs/history")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "auth_required")

    def test_cross_user_run_access_is_hidden_without_paths_or_usernames(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        database = WebDatabase.for_workspace(self.root)
        other_user_id = database.create_user(
            username="bob",
            password_hash=TEST_PASSWORD_HASHER.hash(TEST_PASSWORD),
        )
        run_id = self._create_owned_run(other_user_id)

        response = self.client.get(f"/api/runs/{run_id}/results")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "run_not_found")
        body = response.text
        for forbidden in [
            "/home",
            "state/runs",
            "sandbox",
            "output",
            "config.yaml",
            "bob",
        ]:
            self.assertNotIn(forbidden, body)

    def test_cross_user_logs_events_results_and_artifacts_use_persisted_owner(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        database = WebDatabase.for_workspace(self.root)
        other_user_id = database.create_user(
            username="bob-task8",
            password_hash=TEST_PASSWORD_HASHER.hash(TEST_PASSWORD),
        )
        run_id = self._create_owned_run(other_user_id)
        session = self.run_service.get_session(run_id)
        Path(session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["final_state"]).write_text(
            '{"secret": "other-user-file"}',
            encoding="utf-8",
        )
        Path(session.paths["log"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["log"]).write_text("other-user-log\n", encoding="utf-8")
        session.user_id = self.user_id

        responses = [
            self.client.get(f"/api/runs/{run_id}/logs"),
            self.client.get(f"/api/runs/{run_id}/logs/main"),
            self.client.get(f"/api/runs/{run_id}/results"),
            self.client.get(f"/api/runs/{run_id}/artifacts/final-state"),
            self.client.get(f"/api/runs/{run_id}/events"),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "run_not_found")
            body = response.text
            self.assertNotIn("other-user-file", body)
            self.assertNotIn("other-user-log", body)
            self.assertNotIn("bob-task8", body)
            self.assertNotIn(f'"userId":{other_user_id}', body)
            self.assertNotIn("owner", body.lower())

    def test_run_cancel_is_hidden_from_cross_user_and_visible_to_admin(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        database = WebDatabase.for_workspace(self.root)
        other_user_id = database.create_user(
            username="bob-cancel",
            password_hash=TEST_PASSWORD_HASHER.hash(TEST_PASSWORD),
        )
        run_id = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            user_id=other_user_id,
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        ).run_id

        hidden_response = self.client.post(f"/api/runs/{run_id}/cancel")
        self.assertEqual(hidden_response.status_code, 404)
        self.assertEqual(hidden_response.json()["error"]["code"], "run_not_found")

        login_test_user(self.client, database, username="admin-cancel", role="admin")
        allowed_response = self.client.post(f"/api/runs/{run_id}/cancel")

        self.assertEqual(allowed_response.status_code, 200)
        self.assertEqual(allowed_response.json()["status"], "cancelled")

    def test_pending_cancel_prevents_later_dispatch(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        assert self.default_config_path is not None
        self.default_config_path.write_text(
            (
                "llm:\n"
                "  api_key: default-key\n"
                "deployment:\n"
                "  global_max_running_tasks: 1\n"
                "  per_user_max_running_tasks: 1\n"
            ),
            encoding="utf-8",
        )
        first_run_id = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            user_id=self.user_id,
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        ).run_id
        second_run_id = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            user_id=self.user_id,
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        ).run_id

        response = self.client.post(f"/api/runs/{second_run_id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertEqual(self.run_service.get_session(second_run_id).status, "cancelled")
        self.assertIsNone(self.run_service.get_session(second_run_id).queue_position)
        self.assertEqual(self.run_service.get_session(first_run_id).status, "pending")

    def test_admin_can_read_user_run_where_supported(self) -> None:
        assert self.client is not None
        assert self.root is not None
        database = WebDatabase.for_workspace(self.root)
        user_id = database.create_user(
            username="charlie",
            password_hash=TEST_PASSWORD_HASHER.hash(TEST_PASSWORD),
        )
        run_id = self._create_owned_run(user_id)
        admin_client = TestClient(
            create_app(
                run_service=self.run_service,
                default_config_path=self.default_config_path,
            )
        )
        login_test_user(admin_client, database, username="admin", role="admin")

        response = admin_client.get(f"/api/runs/{run_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runId"], run_id)

    def test_admin_only_dependency_returns_stable_403_for_regular_users(self) -> None:
        with self.assertRaises(ApiErrorException) as raised:
            require_admin(user=AuthenticatedUser(id=1, username="regular", role="user"))

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.code, "admin_required")

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
                    "projectUploadId": self._upload_project_for_run(),
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
        self.assertIn(created["status"], {"pending", "starting", "running"})
        self.assertIsNotNone(created["queuePosition"])
        self.assertEqual(created["mode"], "parallel")
        self.assertIn("evolution.mutation_enabled", created["configPolicy"]["overriddenFields"])
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
        self.assertTrue(current_payload["mutationEnabled"])
        self.assertEqual(current_payload["phase"]["key"], "preprocessing")
        self.assertEqual(current_payload["phase"]["label"], "Preprocessing")
        self.assertEqual(current_payload["iteration"], 1)
        self.assertIn("metrics", current_payload)
        self.assertIn("mutationScore", current_payload["metrics"])
        self.assertEqual(current_payload["metrics"]["mutationScore"], 0.5)
        self.assertEqual(current_payload["metrics"]["totalMutants"], 2)
        self.assertEqual(current_payload["metrics"]["lineCoverage"], 0.25)
        self.assertTrue(current_payload["artifacts"]["resolvedConfig"]["exists"])
        self.assertEqual(current_payload["logStreams"]["taskIds"], ["main"])
        self.assertEqual(current_payload["logStreams"]["byTaskId"]["main"]["status"], "running")
        self.assertIsNotNone(current_payload["logStreams"]["byTaskId"]["main"]["startedAt"])

        history_response = self.client.get("/api/runs/history")
        self.assertEqual(history_response.status_code, 200)
        history_item = next(
            item for item in history_response.json()["items"] if item["runId"] == run_id
        )
        self.assertEqual(history_item["mode"], "parallel")
        self.assertEqual(history_item["projectSourceType"], "upload")

        session = self.run_service.get_session(run_id)
        user_segment = str(self.user_id)
        self.assertIn(f"/state/users/{user_segment}/runs/{run_id}", session.paths["state"])
        self.assertIn(f"/output/users/{user_segment}/runs/{run_id}", session.paths["output"])
        self.assertIn(f"/sandbox/users/{user_segment}/runs/{run_id}", session.paths["sandbox"])
        self.assertIn(f"/logs/users/{user_segment}/runs/{run_id}/run.log", session.paths["log"])
        root = self.root
        assert root is not None
        self.assertFalse((root / "state" / "runs" / run_id).exists())

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
        self.assertEqual(resolved_config["llm"]["api_key"], "[REDACTED]")
        self.assertEqual(resolved_config["evolution"]["max_iterations"], 7)
        self.assertEqual(resolved_config["evolution"]["budget_llm_calls"], 42)
        self.assertEqual(resolved_config["execution"]["selected_java_version"], "17")
        self.assertTrue(resolved_config["evolution"]["mutation_enabled"])
        self.assertTrue(resolved_config["agent"]["parallel"]["enabled"])
        self.assertEqual(resolved_config["github"]["oauth_client_id"], "env-client-id")
        self.assertEqual(resolved_config["github"]["oauth_client_secret"], "[REDACTED]")
        self.assertEqual(
            resolved_config["github"]["managed_clone_root"],
            "./sandbox/github-managed",
        )
        self.assertTrue(session.config_snapshot["evolution"]["mutation_enabled"])

        second_response = self.client.post(
            "/api/runs", data={"projectUploadId": self._upload_project_for_run("second.zip")}
        )
        self.assertEqual(second_response.status_code, 201)
        second_run_id = second_response.json()["runId"]
        self.assertNotEqual(second_run_id, run_id)
        self.assertEqual(self.run_service.get_session(second_run_id).user_id, self.user_id)
        self.assertIn(self.run_service.get_session(second_run_id).status, {"pending", "starting"})

        self.release_run.set()
        self._wait_for_status(run_id, "completed")
        self._wait_for_status(second_run_id, "completed")

        completed_response = self.client.get(f"/api/runs/{run_id}")
        self.assertEqual(completed_response.status_code, 200)
        completed_payload = completed_response.json()
        self.assertEqual(completed_payload["status"], "completed")
        self.assertEqual(completed_payload["selectedJavaVersion"], "17")
        self.assertTrue(completed_payload["mutationEnabled"])
        self.assertEqual(completed_payload["phase"]["key"], "completed")
        self.assertEqual(completed_payload["iteration"], 2)
        self.assertEqual(completed_payload["metrics"]["totalTests"], 4)
        self.assertEqual(completed_payload["metrics"]["mutationScore"], 5 / 6)
        self.assertEqual(completed_payload["metrics"]["globalMutationScore"], 0.0)
        self.assertEqual(
            completed_payload["logStreams"]["byTaskId"]["main"]["status"],
            "completed",
        )
        self.assertIsNotNone(completed_payload["logStreams"]["byTaskId"]["main"]["completedAt"])
        self.assertIsNotNone(completed_payload["logStreams"]["byTaskId"]["main"]["durationSeconds"])

        no_current = self.client.get("/api/runs/current")
        self.assertEqual(no_current.status_code, 404)
        self.assertEqual(no_current.json()["error"]["code"], "no_active_run")

    def test_post_runs_persists_multiple_active_run_requests_when_simultaneous(
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
        upload_ids = [
            self._upload_project_for_run("concurrent-a.zip"),
            self._upload_project_for_run("concurrent-b.zip"),
        ]
        status_codes: list[int] = []
        run_ids: list[str] = []

        def post_run(index: int) -> None:
            barrier.wait(timeout=5)
            response = client.post(
                "/api/runs",
                data={"projectUploadId": upload_ids[index]},
            )
            status_codes.append(response.status_code)
            if response.status_code == 201:
                run_ids.append(str(response.json()["runId"]))

        first = threading.Thread(target=post_run, args=(0,))
        second = threading.Thread(target=post_run, args=(1,))
        first.start()
        second.start()
        first.join(timeout=5)
        second.join(timeout=5)

        self.assertCountEqual(status_codes, [201, 201])
        self.assertEqual(len(run_ids), 2)
        self.assertEqual(len(set(run_ids)), 2)
        self.assertEqual(len(self.run_service._sessions), 2)
        active_run_id = self.run_service.active_run_id()
        self.assertIn(active_run_id, set(run_ids))
        assert active_run_id is not None
        self.assertIn(self.run_service.get_session(active_run_id).status, {"starting", "running"})
        assert self.release_run is not None
        self.release_run.set()
        for run_id in run_ids:
            self._wait_for_status(run_id, "completed")

    def test_post_runs_rejects_local_path_for_ordinary_user_before_path_validation(self) -> None:
        assert self.client is not None
        assert self.root is not None
        response = self.client.post(
            "/api/runs",
            data={"projectPath": str(self.root / "missing-project")},
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "local_path_forbidden")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "local_path_forbidden")

    def test_post_runs_rejects_omitted_project_path_in_local_mode(self) -> None:
        assert self.client is not None

        response = self.client.post("/api/runs", data={})

        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "upload_not_found")
        self.assertEqual(payload["error"]["fieldErrors"][0]["path"], ["projectUploadId"])

    def test_post_runs_rejects_non_maven_project(self) -> None:
        assert self.client is not None
        assert self.non_maven_path is not None
        response = self.client.post(
            "/api/runs",
            data={"projectPath": str(self.non_maven_path)},
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "local_path_forbidden")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "local_path_forbidden")

    def test_post_runs_rejects_invalid_github_repo_url(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.project_path is not None
        database = WebDatabase.for_workspace(self.root)
        login_test_user(self.client, database, username="github-invalid-admin", role="admin")
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
        assert self.root is not None
        assert self.project_path is not None
        database = WebDatabase.for_workspace(self.root)
        login_test_user(self.client, database, username="github-unauth-admin", role="admin")
        response = self.client.post(
            "/api/runs",
            headers={"origin": "http://testserver"},
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

    def test_post_runs_rejects_unknown_uploaded_config_field(self) -> None:
        assert self.client is not None
        assert self.project_path is not None

        response = self.client.post(
            "/api/runs",
            data={"projectPath": str(self.project_path)},
            files={
                "configFile": (
                    "config.yaml",
                    BytesIO(
                        ("llm:\n  api_key: uploaded-key\nevolution:\n  made_up_field: 1\n").encode(
                            "utf-8"
                        )
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "unknown_config_field")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "unknown_config_field")
        self.assertEqual(payload["error"]["fieldErrors"][0]["path"], ["evolution", "made_up_field"])

    def test_post_runs_fixed_concurrency_fields_are_overridden_by_policy(self) -> None:
        assert self.client is not None
        assert self.project_path is not None
        assert self.run_started is not None
        assert self.release_run is not None
        assert self.run_service is not None

        response = self.client.post(
            "/api/runs",
            data={"projectUploadId": self._upload_project_for_run(), "parallelTargets": "64"},
            files={
                "configFile": (
                    "config.yaml",
                    BytesIO(
                        (
                            "preprocessing:\n"
                            "  max_workers: 64\n"
                            "agent:\n"
                            "  parallel:\n"
                            "    enabled: true\n"
                            "    max_parallel_targets: 64\n"
                            "    max_eval_workers: 64\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertIn("preprocessing.max_workers", created["configPolicy"]["overriddenFields"])
        self.assertIn(
            "agent.parallel.max_parallel_targets",
            created["configPolicy"]["overriddenFields"],
        )
        run_id = created["runId"]
        self.assertTrue(self.run_started.wait(timeout=5))
        session = self.run_service.get_session(run_id)
        resolved_config = json.loads(
            Path(session.paths["resolved_config"]).read_text(encoding="utf-8")
        )
        database = self.run_service._web_database
        assert database is not None
        record = database.get_run_record(run_id)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.config_snapshot, resolved_config)
        self.assertIsNone(record.config_snapshot["preprocessing"]["max_workers"])
        self.assertEqual(record.config_snapshot["agent"]["parallel"]["max_parallel_targets"], 4)
        self.assertEqual(record.config_path, session.paths["resolved_config"])
        self.assertIsNone(resolved_config["preprocessing"]["max_workers"])
        self.assertEqual(resolved_config["agent"]["parallel"]["max_parallel_targets"], 4)
        self.assertEqual(resolved_config["agent"]["parallel"]["max_eval_workers"], 4)
        self.release_run.set()
        self._wait_for_status(run_id, "completed")

    def test_post_runs_clamps_over_max_budget_to_deployment_limit(self) -> None:
        assert self.client is not None
        assert self.project_path is not None
        assert self.run_started is not None
        assert self.release_run is not None
        assert self.run_service is not None

        response = self.client.post(
            "/api/runs",
            data={"projectUploadId": self._upload_project_for_run(), "budget": "999"},
        )

        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertIn("evolution.budget", created["configPolicy"]["clampedFields"])
        run_id = created["runId"]
        self.assertTrue(self.run_started.wait(timeout=5))
        session = self.run_service.get_session(run_id)
        resolved_config = json.loads(
            Path(session.paths["resolved_config"]).read_text(encoding="utf-8")
        )
        database = self.run_service._web_database
        assert database is not None
        record = database.get_run_record(run_id)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.config_snapshot, resolved_config)
        self.assertEqual(record.config_snapshot["evolution"]["budget_llm_calls"], 500)
        self.assertEqual(resolved_config["evolution"]["budget_llm_calls"], 500)
        self.release_run.set()
        self._wait_for_status(run_id, "completed")

    def test_policy_immutability_keeps_old_config_snapshot_after_deployment_change(
        self,
    ) -> None:
        assert self.client is not None
        assert self.default_config_path is not None
        assert self.run_started is not None
        assert self.release_run is not None
        assert self.run_service is not None

        response = self.client.post(
            "/api/runs",
            data={"projectUploadId": self._upload_project_for_run(), "budget": "999"},
        )

        self.assertEqual(response.status_code, 201)
        run_id = response.json()["runId"]
        self.assertTrue(self.run_started.wait(timeout=5))
        session = self.run_service.get_session(run_id)
        resolved_config_path = Path(session.paths["resolved_config"])
        original_file_snapshot = json.loads(resolved_config_path.read_text(encoding="utf-8"))
        database = self.run_service._web_database
        assert database is not None
        original_record = database.get_run_record(run_id)
        self.assertIsNotNone(original_record)
        assert original_record is not None
        self.assertEqual(original_record.config_snapshot["evolution"]["budget_llm_calls"], 500)

        self.default_config_path.write_text(
            (
                "llm:\n"
                "  api_key: changed-key\n"
                "  model: gpt-4\n"
                "deployment:\n"
                "  max_budget: 17\n"
                "  max_run_timeout_seconds: 7200\n"
            ),
            encoding="utf-8",
        )

        current_record = database.get_run_record(run_id)
        self.assertIsNotNone(current_record)
        assert current_record is not None
        current_file_snapshot = json.loads(resolved_config_path.read_text(encoding="utf-8"))
        self.assertEqual(current_record.config_snapshot, original_record.config_snapshot)
        self.assertEqual(current_file_snapshot, original_file_snapshot)
        self.assertEqual(current_record.config_snapshot["evolution"]["budget_llm_calls"], 500)
        self.assertEqual(current_file_snapshot["evolution"]["budget_llm_calls"], 500)
        serialized = json.dumps(current_record.config_snapshot, ensure_ascii=False)
        self.assertNotIn("default-key", serialized)
        self.assertNotIn("changed-key", serialized)
        self.assertIn("[REDACTED]", serialized)

        self.release_run.set()
        self._wait_for_status(run_id, "completed")

    def test_post_runs_returns_pending_queue_position_and_429_when_queue_quota_exceeded(
        self,
    ) -> None:
        assert self.client is not None
        assert self.default_config_path is not None
        assert self.project_path is not None
        self.default_config_path.write_text(
            (
                "llm:\n"
                "  api_key: default-key\n"
                "deployment:\n"
                "  global_max_running_tasks: 1\n"
                "  per_user_max_running_tasks: 1\n"
                "  global_max_pending_tasks: 2\n"
                "  per_user_max_pending_tasks: 1\n"
            ),
            encoding="utf-8",
        )

        first = self.client.post(
            "/api/runs", data={"projectUploadId": self._upload_project_for_run("queue-1.zip")}
        )
        second = self.client.post(
            "/api/runs", data={"projectUploadId": self._upload_project_for_run("queue-2.zip")}
        )
        third = self.client.post(
            "/api/runs", data={"projectUploadId": self._upload_project_for_run("queue-3.zip")}
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["status"], "pending")
        self.assertEqual(second.json()["queuePosition"], 1)
        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.json()["error"]["code"], "queue_limit_exceeded")

    def test_uploaded_config_cannot_raise_server_queue_quota(self) -> None:
        assert self.client is not None
        assert self.default_config_path is not None
        assert self.project_path is not None
        self.default_config_path.write_text(
            (
                "llm:\n"
                "  api_key: default-key\n"
                "deployment:\n"
                "  global_max_running_tasks: 1\n"
                "  per_user_max_running_tasks: 1\n"
                "  global_max_pending_tasks: 2\n"
                "  per_user_max_pending_tasks: 1\n"
            ),
            encoding="utf-8",
        )
        uploaded = (
            "llm:\n"
            "  api_key: uploaded-key\n"
            "deployment:\n"
            "  global_max_pending_tasks: 99\n"
            "  per_user_max_pending_tasks: 99\n"
        ).encode("utf-8")

        first = self.client.post(
            "/api/runs",
            data={"projectUploadId": self._upload_project_for_run("quota-1.zip")},
            files={"configFile": ("config.yaml", BytesIO(uploaded), "application/x-yaml")},
        )
        second = self.client.post(
            "/api/runs",
            data={"projectUploadId": self._upload_project_for_run("quota-2.zip")},
            files={"configFile": ("config.yaml", BytesIO(uploaded), "application/x-yaml")},
        )
        third = self.client.post(
            "/api/runs",
            data={"projectUploadId": self._upload_project_for_run("quota-3.zip")},
            files={"configFile": ("config.yaml", BytesIO(uploaded), "application/x-yaml")},
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.json()["error"]["code"], "queue_limit_exceeded")


class ResultsApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    run_service: RunLifecycleService | None = None
    client: TestClient | None = None
    user_id: int | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(create_app(run_service=self.run_service))
        self.user_id = login_test_user(self.client, WebDatabase.for_workspace(self.root))

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
            user_id=self.user_id,
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

    def test_artifact_downloads_allow_resolved_config_and_reject_traversal(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        self._write_completed_run_artifacts(run_id)

        resolved_config_response = self.client.get(f"/api/runs/{run_id}/artifacts/resolved-config")
        traversal_response = self.client.get(
            f"/api/runs/{run_id}/artifacts/%2e%2e%2f%2e%2e%2fconfig.yaml"
        )
        unknown_response = self.client.get(f"/api/runs/{run_id}/artifacts/config.yaml")

        self.assertEqual(resolved_config_response.status_code, 200)
        self.assertEqual(resolved_config_response.headers["content-type"], "application/json")
        self.assertIn('"api_key": "[REDACTED]"', resolved_config_response.text)
        self.assertIn(traversal_response.status_code, {400, 404})
        self.assertIn(unknown_response.status_code, {400, 404})
        for response in [traversal_response, unknown_response]:
            body = response.text
            self.assertNotIn("default-key", body)
            self.assertNotIn("config.example.yaml", body)
            self.assertNotIn(str(self.run_service.workspace_root), body)

    def test_artifact_download_uses_persisted_snapshot_when_memory_paths_are_tampered(
        self,
    ) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        run_id = self._create_run()
        self._write_completed_run_artifacts(run_id)
        session = self.run_service.get_session(run_id)
        safe_final_state = Path(session.path_snapshot["output"]) / "final_state.json"
        safe_final_state.write_text(
            '{"source": "persisted-snapshot"}',
            encoding="utf-8",
        )
        outside_file = self.root / "config.yaml"
        outside_file.write_text("outside-secret", encoding="utf-8")
        session.paths["final_state"] = str(outside_file)

        response = self.client.get(f"/api/runs/{run_id}/artifacts/final-state")

        self.assertEqual(response.status_code, 200)
        self.assertIn("persisted-snapshot", response.text)
        self.assertNotIn("outside-secret", response.text)

    def test_artifact_download_rejects_polluted_persisted_path_snapshot(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        run_id = self._create_run()
        self._write_completed_run_artifacts(run_id)
        outside_output = self.root / "outside-output"
        outside_output.mkdir()
        (outside_output / "final_state.json").write_text("outside-secret", encoding="utf-8")
        session = self.run_service.get_session(run_id)
        polluted_snapshot = dict(session.path_snapshot)
        polluted_snapshot["output"] = str(outside_output)
        database = self.run_service._web_database
        assert database is not None
        database.update_run_record(run_id, path_snapshot=polluted_snapshot)

        response = self.client.get(f"/api/runs/{run_id}/artifacts/final-state")

        self.assertEqual(response.status_code, 404)
        self.assertIn(response.json()["error"]["code"], {"artifact_not_found", "run_not_found"})
        self.assertNotIn("outside-secret", response.text)

    def test_run_log_artifact_rejects_symlink_escape(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        run_id = self._create_run()
        self._write_completed_run_artifacts(run_id)
        session = self.run_service.get_session(run_id)
        outside_file = self.root / "outside-run.log"
        outside_file.write_text("outside-log-secret", encoding="utf-8")
        log_path = Path(session.path_snapshot["log"])
        log_path.unlink()
        log_path.symlink_to(outside_file)

        response = self.client.get(f"/api/runs/{run_id}/artifacts/run-log")

        self.assertEqual(response.status_code, 404)
        self.assertIn(response.json()["error"]["code"], {"artifact_not_found", "run_not_found"})
        self.assertNotIn("outside-log-secret", response.text)

    def test_restarted_results_endpoint_tolerates_missing_log_router(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        run_id = self._create_run()
        self._write_completed_run_artifacts(run_id)
        restarted_service = RunLifecycleService(workspace_root=self.root)
        restarted_service._log_routers.pop(run_id, None)
        restarted_client = TestClient(create_app(run_service=restarted_service))
        restarted_client.cookies.update(self.client.cookies)

        response = restarted_client.get(f"/api/runs/{run_id}/results")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["runId"], run_id)
        self.assertEqual(payload["status"], "completed")


class RunHistoryApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    run_service: RunLifecycleService | None = None
    client: TestClient | None = None
    user_id: int | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(create_app(run_service=self.run_service))
        self.user_id = login_test_user(self.client, WebDatabase.for_workspace(self.root))

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _create_run(self) -> str:
        assert self.run_service is not None
        assert self.project_path is not None
        session = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            user_id=self.user_id,
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

    def test_history_endpoint_filters_runs_by_user_and_admin_can_see_all(self) -> None:
        assert self.client is not None
        assert self.root is not None
        assert self.run_service is not None
        database = WebDatabase.for_workspace(self.root)
        other_user_id = database.create_user(
            username="bob-history",
            password_hash=TEST_PASSWORD_HASHER.hash(TEST_PASSWORD),
        )
        own_run_id = self._create_run()
        self.run_service.mark_completed(own_run_id)
        other_session = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            user_id=other_user_id,
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        )
        self.run_service.mark_completed(other_session.run_id)

        user_response = self.client.get("/api/runs/history")
        self.assertEqual(user_response.status_code, 200)
        self.assertEqual([item["runId"] for item in user_response.json()["items"]], [own_run_id])

        admin_client = TestClient(create_app(run_service=self.run_service))
        login_test_user(admin_client, database, username="admin-history", role="admin")
        admin_response = admin_client.get("/api/runs/history")
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(
            {item["runId"] for item in admin_response.json()["items"]},
            {own_run_id, other_session.run_id},
        )

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

        legacy_manifest_dir = self.root / "state" / "runs" / "run-legacy"
        legacy_manifest_dir.mkdir(parents=True, exist_ok=True)
        (legacy_manifest_dir / "session.json").write_text("{broken", encoding="utf-8")

        broken_state_run_id = self._create_run()
        broken_state_session = self.run_service.get_session(broken_state_run_id)
        Path(broken_state_session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
        Path(broken_state_session.paths["final_state"]).write_text("{broken", encoding="utf-8")
        self.run_service.mark_completed(broken_state_run_id)

        restored_service = RunLifecycleService(workspace_root=self.root)
        restored_client = TestClient(create_app(run_service=restored_service))
        if self.client is not None:
            restored_client.cookies.update(self.client.cookies)

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
