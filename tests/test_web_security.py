from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Callable
from unittest.mock import patch

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from comet.config.settings import Settings
from comet.web.app import create_app
from comet.web.run_service import RunLifecycleService, RunRequest
from comet.web.storage import WebDatabase

TEST_PASSWORD_HASHER = PasswordHasher()
TEST_PASSWORD = "correct-password"


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


class WebSecurityRegressionTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    default_config_path: Path | None = None
    allowed_project_path: Path | None = None
    allowed_reports_path: Path | None = None
    run_service: RunLifecycleService | None = None
    database: WebDatabase | None = None
    release_run: threading.Event | None = None
    alice_client: TestClient | None = None
    bob_client: TestClient | None = None
    admin_client: TestClient | None = None
    alice_id: int | None = None
    bob_id: int | None = None
    admin_id: int | None = None
    _fake_initialize: Callable[..., dict[str, object]] | None = None
    _fake_run: Callable[..., None] | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.allowed_project_path = self.root / "allowed-project"
        self.allowed_project_path.mkdir()
        (self.allowed_project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.allowed_reports_path = self.root / "allowed-reports"
        self.allowed_reports_path.mkdir()
        (self.allowed_reports_path / "bug.md").write_text("# Bug", encoding="utf-8")
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text(
            (
                "llm:\n"
                "  api_key: default-key\n"
                "  model: gpt-4\n"
                "deployment:\n"
                f"  allow_local_path_mode: true\n"
                f"  local_path_allowlist:\n"
                f"    - {self.allowed_project_path}\n"
                f"    - {self.allowed_reports_path}\n"
                "  global_max_running_tasks: 1\n"
                "  per_user_max_running_tasks: 1\n"
                "  global_max_pending_tasks: 1\n"
                "  per_user_max_pending_tasks: 1\n"
            ),
            encoding="utf-8",
        )
        self.release_run = threading.Event()
        self.run_service = RunLifecycleService(workspace_root=self.root)
        self.database = WebDatabase.for_workspace(self.root)

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
            assert self.release_run is not None
            if not self.release_run.wait(timeout=5):
                raise TimeoutError("run release timeout")

        self._fake_initialize = fake_initialize
        self._fake_run = fake_run
        self.alice_client = self._make_client()
        self.bob_client = self._make_client()
        self.admin_client = self._make_client()
        self.alice_id = self._create_and_login_user(self.alice_client, "alice", role="user")
        self.bob_id = self._create_and_login_user(self.bob_client, "bob", role="user")
        self.admin_id = self._create_and_login_user(
            self.admin_client,
            "admin",
            role="admin",
        )

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

    def _make_client(self) -> TestClient:
        assert self.run_service is not None
        assert self.default_config_path is not None
        assert self._fake_initialize is not None
        assert self._fake_run is not None
        return TestClient(
            create_app(
                run_service=self.run_service,
                default_config_path=self.default_config_path,
                system_initializer=self._fake_initialize,
                evolution_runner=self._fake_run,
            )
        )

    def _create_and_login_user(self, client: TestClient, username: str, *, role: str) -> int:
        assert self.database is not None
        user_id = self.database.create_user(
            username=username,
            password_hash=TEST_PASSWORD_HASHER.hash(TEST_PASSWORD),
            role=role,
        )
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": TEST_PASSWORD},
        )
        self.assertEqual(response.status_code, 200, response.text)
        client.headers.update({"origin": "http://testserver"})
        return user_id

    def _upload_project(
        self, client: TestClient, archive: BytesIO, filename: str = "project.zip"
    ) -> str:
        response = client.post(
            "/api/uploads/project",
            files={"file": (filename, archive, "application/octet-stream")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return str(response.json()["uploadId"])

    def _upload_row(self, upload_id: str) -> dict[str, object]:
        assert self.database is not None
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT user_id, kind, status FROM uploads WHERE id = ?",
                (upload_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return dict(row)

    def _make_run(self, *, user_id: int, project_path: str) -> str:
        assert self.run_service is not None
        session = self.run_service.create_run(
            RunRequest(project_path=project_path),
            user_id=user_id,
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        )
        return session.run_id

    def _write_completed_artifacts(self, run_id: str, *, log_text: str = "run completed\n") -> None:
        assert self.run_service is not None
        session = self.run_service.get_session(run_id)
        Path(session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["final_state"]).write_text('{"ok":true}', encoding="utf-8")
        Path(session.paths["log"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["log"]).write_text(log_text, encoding="utf-8")
        Path(session.paths["report_artifact"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["report_artifact"]).write_text("# report\n", encoding="utf-8")
        self.run_service.mark_completed(run_id)

    def _assert_no_path_leaks(
        self, response_text: str, extra_forbidden: list[str] | None = None
    ) -> None:
        assert self.root is not None
        forbidden = [
            str(self.root),
            "/home/",
            "state/users",
            "sandbox/users",
            "output/users",
            "logs/users",
            "config.yaml",
            "alice",
            "bob",
            "admin",
        ]
        if extra_forbidden:
            forbidden.extend(extra_forbidden)
        for item in forbidden:
            self.assertNotIn(item, response_text)

    def test_valid_project_upload_is_user_scoped(self) -> None:
        assert self.alice_client is not None
        assert self.alice_id is not None
        assert self.root is not None
        archive = _zip_bytes(
            {
                "sample-project/pom.xml": "<project/>",
                "sample-project/src/main/java/App.java": "class App {}",
            }
        )

        response = self._upload_project(self.alice_client, archive, "project.zip")

        row = self._upload_row(response)
        self.assertEqual(row["user_id"], self.alice_id)
        self.assertEqual(row["kind"], "project")
        self.assertEqual(row["status"], "ready")
        upload_root = self.root / "sandbox" / "users" / str(self.alice_id) / "uploads" / response
        self.assertTrue((upload_root / "raw" / "project.zip").is_file())
        self.assertTrue((upload_root / "extracted" / "sample-project" / "pom.xml").is_file())

    def test_project_upload_rejects_traversal_paths(self) -> None:
        assert self.alice_client is not None

        response = self.alice_client.post(
            "/api/uploads/project",
            files={
                "file": (
                    "traversal.zip",
                    _zip_bytes(
                        {"../outside.txt": "escape", "sample-project/pom.xml": "<project/>"}
                    ),
                    "application/octet-stream",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsafe_zip_entry")
        self._assert_no_path_leaks(response.text)

    def test_spa_fallback_does_not_serve_symlink_escape(self) -> None:
        assert self.root is not None
        dist_path = self.root / "web-dist"
        dist_path.mkdir()
        (dist_path / "index.html").write_text("<main>safe app</main>", encoding="utf-8")
        outside_secret = self.root / "outside-secret.txt"
        outside_secret.write_text("outside-secret-token", encoding="utf-8")
        (dist_path / "linked-secret.txt").symlink_to(outside_secret)

        client = TestClient(
            create_app(
                run_service=RunLifecycleService(workspace_root=self.root),
                default_config_path=self.default_config_path,
                frontend_dist_path=dist_path,
            )
        )

        response = client.get("/linked-secret.txt")

        self.assertEqual(response.status_code, 200)
        self.assertIn("safe app", response.text)
        self.assertNotIn("outside-secret-token", response.text)

    def test_project_upload_rejects_absolute_paths(self) -> None:
        assert self.alice_client is not None
        response = self.alice_client.post(
            "/api/uploads/project",
            files={
                "file": (
                    "absolute.zip",
                    _zip_bytes({"/abs.txt": "escape", "sample-project/pom.xml": "<project/>"}),
                    "application/octet-stream",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsafe_zip_entry")
        self._assert_no_path_leaks(response.text)

    def test_project_upload_rejects_symlink_entries_when_supported(self) -> None:
        assert self.alice_client is not None
        if os.name != "posix":
            self.skipTest("symlink ZIP attributes are not reliable on this platform")

        response = self.alice_client.post(
            "/api/uploads/project",
            files={
                "file": (
                    "symlink.zip",
                    _zip_bytes({"sample-project/pom.xml": "<project/>"}, symlink_name="linked.txt"),
                    "application/octet-stream",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsafe_zip_entry")

    def test_upload_rejects_duplicate_normalized_paths(self) -> None:
        assert self.alice_client is not None
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("sample-project/pom.xml", "<project/>")
            zip_file.writestr("sample-project/src/main/java/App.java", "class App {}")
            zip_file.writestr("sample-project/./src/main/java/App.java", "class App2 {}")
        archive.seek(0)

        response = self.alice_client.post(
            "/api/uploads/project",
            files={"file": ("duplicate.zip", archive, "application/octet-stream")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsafe_zip_entry")

    def test_upload_rejects_file_count_and_size_limits(self) -> None:
        assert self.alice_client is not None
        with patch("comet.web.routes.MAX_ZIP_FILE_COUNT", 1):
            count_response = self.alice_client.post(
                "/api/uploads/project",
                files={
                    "file": (
                        "too-many.zip",
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

        with patch("comet.web.routes.MAX_ZIP_FILE_BYTES", 1):
            size_response = self.alice_client.post(
                "/api/uploads/project",
                files={
                    "file": (
                        "too-large.zip",
                        _zip_bytes(
                            {
                                "sample-project/pom.xml": "<project/>",
                                "sample-project/src/main/java/App.java": "ab",
                            }
                        ),
                        "application/octet-stream",
                    )
                },
            )

        self.assertEqual(count_response.status_code, 422)
        self.assertEqual(count_response.json()["error"]["code"], "zip_too_many_files")
        self.assertEqual(size_response.status_code, 422)
        self.assertEqual(size_response.json()["error"]["code"], "zip_file_too_large")

    def test_cross_user_runs_logs_results_and_artifacts_are_hidden_without_leaking_paths(
        self,
    ) -> None:
        assert self.alice_client is not None
        assert self.bob_id is not None
        assert self.root is not None

        run_id = self._make_run(user_id=self.bob_id, project_path=str(self.allowed_project_path))
        self._write_completed_artifacts(run_id, log_text="bob-only log\n")

        responses = {
            "results": self.alice_client.get(f"/api/runs/{run_id}/results"),
            "logs": self.alice_client.get(f"/api/runs/{run_id}/logs"),
            "events": self.alice_client.get(f"/api/runs/{run_id}/events"),
            "artifact": self.alice_client.get(f"/api/runs/{run_id}/artifacts/final-state"),
        }

        representative_bodies: dict[str, object] = {}
        for label, response in responses.items():
            self.assertEqual(response.status_code, 404, label)
            self.assertEqual(response.json()["error"]["code"], "run_not_found")
            self._assert_no_path_leaks(response.text, ["bob-only log", "other-user-file"])
            representative_bodies[label] = response.json()

        evidence_path = (
            Path(__file__).resolve().parents[1]
            / ".sisyphus"
            / "evidence"
            / "task-16-no-path-leaks.json"
        )
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            json.dumps(
                {
                    "runId": run_id,
                    "bodies": representative_bodies,
                    "noPathLeaks": True,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def test_run_local_path_forbidden_for_user_and_gate_for_admin(self) -> None:
        assert self.alice_client is not None
        assert self.admin_client is not None
        assert self.allowed_project_path is not None
        assert self.allowed_reports_path is not None

        forbidden = self.alice_client.post(
            "/api/runs",
            data={
                "projectPath": str(self.allowed_project_path),
                "bugReportsDir": str(self.allowed_reports_path),
            },
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["error"]["code"], "local_path_forbidden")
        self._assert_no_path_leaks(forbidden.text)

        allowed = self.admin_client.post(
            "/api/runs",
            data={
                "projectPath": str(self.allowed_project_path),
                "bugReportsDir": str(self.allowed_reports_path),
            },
        )

        self.assertEqual(allowed.status_code, 201, allowed.text)
        payload = allowed.json()
        self.assertEqual(payload["uploadSource"], {"mode": "local_path"})
        self.assertIn(payload["status"], {"pending", "starting", "running"})

    def test_run_config_upload_rejects_unknown_fields(self) -> None:
        assert self.alice_client is not None
        archive = _zip_bytes({"sample-project/pom.xml": "<project/>"})
        project_upload_id = self._upload_project(self.alice_client, archive, "project.zip")

        response = self.alice_client.post(
            "/api/runs",
            data={"projectUploadId": project_upload_id},
            files={
                "configFile": (
                    "config.yaml",
                    BytesIO(
                        (
                            "llm:\n  api_key: uploaded-key\npaths:\n  output: ./custom-output\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "unknown_config_field")

    def test_run_config_upload_clamps_policy_and_preserves_fixed_fields(self) -> None:
        assert self.alice_client is not None
        assert self.root is not None
        project_upload_id = self._upload_project(
            self.alice_client,
            _zip_bytes({"sample-project/pom.xml": "<project/>"}),
            "project.zip",
        )

        response = self.alice_client.post(
            "/api/runs",
            data={"projectUploadId": project_upload_id, "budget": "999999"},
            files={
                "configFile": (
                    "config.yaml",
                    BytesIO(
                        (
                            "llm:\n"
                            "  api_key: uploaded-key\n"
                            "preprocessing:\n"
                            "  max_workers: 99\n"
                            "evolution:\n"
                            "  max_iterations: 9999\n"
                            "execution:\n"
                            "  timeout: 99999\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertIn("configPolicy", payload)
        self.assertIn("evolution.max_iterations", payload["configPolicy"]["clampedFields"])
        self.assertIn("evolution.budget", payload["configPolicy"]["clampedFields"])
        self.assertIn("execution.timeout", payload["configPolicy"]["clampedFields"])
        self.assertIn("preprocessing.max_workers", payload["configPolicy"]["overriddenFields"])
        self.assertNotEqual(payload["effectiveConfig"]["preprocessing"]["max_workers"], 99)
        self.assertLessEqual(
            payload["effectiveConfig"]["evolution"]["max_iterations"],
            10,
        )
        self.assertLessEqual(payload["effectiveConfig"]["evolution"]["budget_llm_calls"], 500)
        self.assertLessEqual(payload["effectiveConfig"]["execution"]["timeout"], 7200)
        self.assertEqual(payload["effectiveConfig"]["llm"]["api_key"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
