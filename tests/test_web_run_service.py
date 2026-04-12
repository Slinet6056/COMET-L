import argparse
import json
import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import main
from comet.config.settings import LLMConfig, LoggingConfig, Settings
from comet.web.git_pr_service import GitPullRequestError
from comet.web.run_service import (
    ActiveRunConflictError,
    RunLifecycleService,
    RunRequest,
    reset_managed_logging,
    run_request,
)


class RunServiceIsolationTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_managed_logging()

    def test_run_uses_scoped_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            scoped_log_path = root / "runs" / "run-001" / "run.log"
            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )
            captured: dict[str, object] = {}
            events: list[dict[str, object]] = []

            def fake_initialize(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
            ) -> dict[str, object]:
                captured["config_log_file"] = config.logging.file
                captured["parallel_mode"] = parallel_mode
                return {"config": config}

            def fake_run(
                project_path: str,
                components: dict[str, object],
                resume_state: str | None = None,
            ) -> None:
                _ = components
                captured["project_path"] = project_path
                captured["resume_state"] = resume_state
                logging.getLogger("test.run_service").info("scoped log message")

            exit_code = run_request(
                RunRequest(
                    project_path=str(project_path),
                    config_path="config.yaml",
                    log_file=str(scoped_log_path),
                ),
                settings_loader=lambda _: settings,
                system_initializer=fake_initialize,
                evolution_runner=fake_run,
                observer=events.append,
            )

            managed_file_handlers = [
                handler
                for handler in logging.getLogger().handlers
                if isinstance(handler, logging.FileHandler)
                and getattr(handler, "_comet_managed", False)
            ]

            self.assertEqual(exit_code, 0)
            self.assertEqual(captured["config_log_file"], str(scoped_log_path))
            self.assertEqual(captured["project_path"], str(project_path))
            self.assertEqual(len(managed_file_handlers), 1)
            self.assertEqual(Path(managed_file_handlers[0].baseFilename), scoped_log_path.resolve())
            self.assertTrue(scoped_log_path.exists())
            self.assertNotEqual(scoped_log_path.resolve(), (root / "comet.log").resolve())
            self.assertEqual([event["type"] for event in events], ["run.started", "run.completed"])
            self.assertEqual(events[0]["log_file"], str(scoped_log_path.resolve()))
            self.assertEqual(events[0]["project_path"], str(project_path))

    def test_run_emits_failed_event_when_runner_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            _ = (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            events: list[dict[str, object]] = []
            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            def fake_initialize(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
            ) -> dict[str, object]:
                _ = (config, bug_reports_dir, parallel_mode)
                return {"config": settings}

            def fake_run(
                project_path: str,
                components: dict[str, object],
                resume_state: str | None = None,
            ) -> None:
                _ = (project_path, components, resume_state)
                raise RuntimeError("boom")

            with self.assertRaisesRegex(RuntimeError, "boom"):
                run_request(
                    RunRequest(
                        project_path=str(project_path),
                        config_path="config.yaml",
                        log_file=str(root / "runs" / "run-002" / "run.log"),
                    ),
                    settings_loader=lambda _: settings,
                    system_initializer=fake_initialize,
                    evolution_runner=fake_run,
                    observer=events.append,
                )

            self.assertEqual([event["type"] for event in events], ["run.started", "run.failed"])
            self.assertEqual(events[1]["error"], "boom")

    def test_cli_entry_uses_shared_runner(self) -> None:
        args = argparse.Namespace(
            project_path="/tmp/project",
            config="config.yaml",
            max_iterations=5,
            budget=12,
            resume=None,
            debug=True,
            bug_reports_dir=None,
            parallel=False,
            parallel_targets=None,
        )

        with (
            patch.object(main, "parse_args", return_value=args),
            patch.object(main, "run_cli", return_value=0) as run_cli_mock,
        ):
            main.main()

        run_cli_mock.assert_called_once_with(
            args,
            system_initializer=main.initialize_system,
            evolution_runner=main.run_evolution,
        )

    def test_run_request_lifecycle_entry_imports_github_source_and_cleans_old_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            imported_project = root / "managed" / "openai" / "demo" / "run-001"
            (imported_project / "pom.xml").parent.mkdir(parents=True, exist_ok=True)
            (imported_project / "pom.xml").write_text("<project/>", encoding="utf-8")
            (imported_project / "src" / "main" / "java").mkdir(parents=True, exist_ok=True)
            old_test_file = imported_project / "src" / "test" / "java" / "LegacyTest.java"
            old_test_file.parent.mkdir(parents=True, exist_ok=True)
            old_test_file.write_text("class LegacyTest {}", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )
            settings.github.managed_clone_root = str(root / "managed")

            events: list[dict[str, object]] = []
            captured: dict[str, object] = {}

            class _FakeRepoImportService:
                def __init__(self) -> None:
                    self.called = False

                def import_repository(self, **kwargs):
                    self.called = True
                    captured["import_kwargs"] = kwargs
                    return type(
                        "Imported",
                        (),
                        {
                            "project_path": str(imported_project),
                            "base_branch": "develop",
                        },
                    )()

            fake_import_service = _FakeRepoImportService()

            def fake_initialize(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
            ) -> dict[str, object]:
                _ = (bug_reports_dir, parallel_mode)
                captured["repo_url"] = config.github.repo_url
                captured["base_branch"] = config.github.base_branch
                return {"config": config}

            def fake_run(
                project_path: str,
                components: dict[str, object],
                resume_state: str | None = None,
            ) -> None:
                _ = (components, resume_state)
                captured["project_path"] = project_path
                captured["old_test_exists"] = old_test_file.exists()

            exit_code = run_request(
                RunRequest(
                    project_path=str(root / "placeholder"),
                    config_path="config.yaml",
                    github_repo_url="https://github.com/openai/demo",
                ),
                settings_loader=lambda _: settings,
                system_initializer=fake_initialize,
                evolution_runner=fake_run,
                observer=events.append,
                repo_import_service=cast(Any, fake_import_service),
                source_run_id="run-001",
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(fake_import_service.called)
            self.assertEqual(captured["project_path"], str(imported_project.resolve()))
            self.assertFalse(bool(captured["old_test_exists"]))
            self.assertEqual(captured["repo_url"], "https://github.com/openai/demo")
            self.assertEqual(captured["base_branch"], "develop")
            self.assertEqual([event["type"] for event in events], ["run.started", "run.completed"])

    def test_run_request_aborts_before_runner_when_cleanup_old_tests_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            called = {"run": False}

            def fake_initialize(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
            ) -> dict[str, object]:
                _ = (config, bug_reports_dir, parallel_mode)
                return {"config": settings}

            def fake_run(
                project_path: str,
                components: dict[str, object],
                resume_state: str | None = None,
            ) -> None:
                _ = (project_path, components, resume_state)
                called["run"] = True

            with patch(
                "comet.web.run_service._clear_project_test_directories",
                side_effect=RuntimeError("清理旧测试目录失败: denied"),
            ):
                with self.assertRaisesRegex(RuntimeError, "清理旧测试目录失败"):
                    run_request(
                        RunRequest(
                            project_path=str(project_path),
                            config_path="config.yaml",
                        ),
                        settings_loader=lambda _: settings,
                        system_initializer=fake_initialize,
                        evolution_runner=fake_run,
                    )

            self.assertFalse(called["run"])


class RunLifecycleTests(unittest.TestCase):
    def test_create_run_allocates_scoped_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            service = RunLifecycleService(workspace_root=root)
            session = service.create_run(
                RunRequest(project_path=str(project_path), config_path="config.yaml"),
                settings_loader=lambda _: settings,
            )

            run_id = session.run_id
            self.assertTrue(run_id.startswith("run-"))
            self.assertEqual(session.status, "created")
            self.assertEqual(service.active_run_id(), run_id)

            self.assertEqual(session.paths["state"], str(root / "state" / "runs" / run_id))
            self.assertEqual(session.paths["output"], str(root / "output" / "runs" / run_id))
            self.assertEqual(session.paths["sandbox"], str(root / "sandbox" / "runs" / run_id))
            self.assertEqual(session.paths["log"], str(root / "logs" / "runs" / run_id / "run.log"))
            self.assertEqual(
                session.paths["database"],
                str(root / "state" / "runs" / run_id / "comet.db"),
            )

            self.assertEqual(session.path_snapshot["state"], session.paths["state"])
            self.assertEqual(session.path_snapshot["output"], session.paths["output"])
            self.assertEqual(session.path_snapshot["sandbox"], session.paths["sandbox"])
            self.assertEqual(session.path_snapshot["log"], session.paths["log"])

            resolved_config_path = Path(session.paths["resolved_config"])
            self.assertTrue(resolved_config_path.exists())
            resolved_snapshot = json.loads(resolved_config_path.read_text(encoding="utf-8"))
            self.assertEqual(resolved_snapshot["logging"]["file"], session.paths["log"])
            self.assertFalse(resolved_snapshot["preprocessing"]["exit_after_preprocessing"])
            self.assertNotIn("vector_db", resolved_snapshot["knowledge"])
            self.assertNotIn("paths", resolved_snapshot)

    def test_create_run_persists_and_restores_mutation_enabled_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            service = RunLifecycleService(workspace_root=root)
            session = service.create_run(
                RunRequest(
                    project_path=str(project_path),
                    config_path="config.yaml",
                    mutation_enabled=False,
                ),
                settings_loader=lambda _: settings,
            )

            self.assertFalse(session.config_snapshot["evolution"]["mutation_enabled"])
            resolved_config_path = Path(session.paths["resolved_config"])
            resolved_snapshot = json.loads(resolved_config_path.read_text(encoding="utf-8"))
            self.assertFalse(resolved_snapshot["evolution"]["mutation_enabled"])

            service.mark_completed(session.run_id)

            restored_service = RunLifecycleService(workspace_root=root)
            restored_session = restored_service.get_session(session.run_id)
            restored_request = restored_service.get_run_request(session.run_id)

            self.assertFalse(restored_session.config_snapshot["evolution"]["mutation_enabled"])
            self.assertFalse(restored_request.mutation_enabled)

    def test_restored_request_keeps_missing_mutation_enabled_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            service = RunLifecycleService(workspace_root=root)
            session = service.create_run(
                RunRequest(project_path=str(project_path), config_path="config.yaml"),
                settings_loader=lambda _: settings,
            )

            legacy_state_payload = {
                "iteration": 3,
                "llm_calls": 7,
                "budget": 21,
                "total_tests": 5,
                "total_mutants": 8,
                "killed_mutants": 6,
                "survived_mutants": 2,
                "mutation_score": 0.75,
                "line_coverage": 0.8,
                "branch_coverage": 0.6,
            }
            final_state_path = Path(session.paths["final_state"])
            final_state_path.parent.mkdir(parents=True, exist_ok=True)
            final_state_path.write_text(json.dumps(legacy_state_payload), encoding="utf-8")
            service.mark_completed(session.run_id)

            manifest_path = service._session_manifest_path(session.run_id)
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_payload.setdefault("config_snapshot", {}).setdefault("evolution", {}).pop(
                "mutation_enabled", None
            )
            manifest_path.write_text(
                json.dumps(manifest_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            restored_service = RunLifecycleService(workspace_root=root)

            restored_request = restored_service.get_run_request(session.run_id)
            restored_snapshot = restored_service.build_snapshot(session.run_id)
            restored_results = restored_service.build_results(session.run_id)

            self.assertIsNone(restored_request.mutation_enabled)
            self.assertTrue(restored_snapshot["mutationEnabled"])
            self.assertEqual(restored_snapshot["metrics"]["mutationScore"], 0.75)
            self.assertEqual(restored_snapshot["metrics"]["totalMutants"], 8)
            self.assertEqual(restored_results["summary"]["metrics"]["mutationScore"], 0.75)
            self.assertEqual(restored_service.list_runs()[0]["mutationEnabled"], True)

    def test_second_active_run_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            service = RunLifecycleService(workspace_root=root)
            first = service.create_run(
                RunRequest(project_path=str(project_path), config_path="config.yaml"),
                settings_loader=lambda _: settings,
            )

            with self.assertRaisesRegex(ActiveRunConflictError, first.run_id):
                service.create_run(
                    RunRequest(project_path=str(project_path), config_path="config.yaml"),
                    settings_loader=lambda _: settings,
                )

    def test_start_run_replays_scoped_runtime_roots_with_fresh_settings_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            config_path = root / "config.yaml"
            config_path.write_text("llm:\n  api_key: test-key\n", encoding="utf-8")

            service = RunLifecycleService(workspace_root=root)
            session = service.create_run(
                RunRequest(project_path=str(project_path), config_path=str(config_path)),
                settings_loader=Settings.from_yaml_or_default,
            )

            def fake_initialize(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
            ) -> dict[str, object]:
                self.assertEqual(config.resolve_state_root(), Path(session.paths["state"]))
                self.assertEqual(config.resolve_output_root(), Path(session.paths["output"]))
                self.assertEqual(config.resolve_sandbox_root(), Path(session.paths["sandbox"]))
                return {"config": config}

            def fake_run(
                project_path: str,
                components: dict[str, object],
                resume_state: str | None = None,
            ) -> None:
                del project_path, resume_state
                config = components["config"]
                assert isinstance(config, Settings)
                output_path = config.resolve_output_root()
                output_path.mkdir(parents=True, exist_ok=True)
                (output_path / "final_state.json").write_text("{}", encoding="utf-8")

            service.start_run(
                session.run_id,
                settings_loader=Settings.from_yaml_or_default,
                system_initializer=fake_initialize,
                evolution_runner=fake_run,
            )
            service._threads[session.run_id].join(timeout=5)

            restored_final_state = Path(session.paths["final_state"])
            self.assertTrue(restored_final_state.exists())
            self.assertEqual(service.get_session(session.run_id).status, "completed")

    def test_start_run_keeps_completed_status_when_push_or_pr_step_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            class _FailingPullRequestService:
                def commit_generated_tests_and_create_pr(self, **kwargs):
                    del kwargs
                    raise GitPullRequestError("推送提交到远端失败，请检查 GitHub 权限或网络连接。")

            service = RunLifecycleService(workspace_root=root)
            service.set_pull_request_service(cast(Any, _FailingPullRequestService()))
            session = service.create_run(
                RunRequest(project_path=str(project_path), config_path="config.yaml"),
                settings_loader=lambda _: settings,
            )
            session.config_snapshot.setdefault("github", {})["repo_url"] = (
                "https://github.com/openai/example-repo"
            )
            session.config_snapshot.setdefault("github", {})["base_branch"] = "main"

            def fake_initialize(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
            ) -> dict[str, object]:
                del bug_reports_dir, parallel_mode
                return {"config": config}

            def fake_run(
                project_path: str,
                components: dict[str, object],
                resume_state: str | None = None,
            ) -> None:
                del project_path, components, resume_state

            service.start_run(
                session.run_id,
                settings_loader=lambda _: settings,
                system_initializer=fake_initialize,
                evolution_runner=fake_run,
            )
            service._threads[session.run_id].join(timeout=5)

            completed_session = service.get_session(session.run_id)
            self.assertEqual(completed_session.status, "completed")
            self.assertIsNotNone(completed_session.completed_at)
            self.assertIsNone(completed_session.failed_at)
            self.assertIsNotNone(completed_session.error)
            assert completed_session.error is not None
            self.assertIn("推送提交到远端失败", completed_session.error)

            results = service.build_results(session.run_id)
            self.assertEqual(results["status"], "completed")
            self.assertIsNone(results["pullRequestUrl"])
            self.assertIn("推送提交到远端失败", results["pullRequestError"])

    def test_persisted_sessions_are_restored_after_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            service = RunLifecycleService(workspace_root=root)
            session = service.create_run(
                RunRequest(project_path=str(project_path), config_path="config.yaml"),
                settings_loader=lambda _: settings,
            )
            run_id = session.run_id

            state_payload = {
                "iteration": 3,
                "llm_calls": 7,
                "budget": 21,
                "total_tests": 5,
                "total_mutants": 8,
                "killed_mutants": 6,
                "survived_mutants": 2,
                "mutation_score": 0.75,
                "line_coverage": 0.8,
                "branch_coverage": 0.6,
            }
            final_state_path = Path(session.paths["final_state"])
            final_state_path.parent.mkdir(parents=True, exist_ok=True)
            final_state_path.write_text(json.dumps(state_payload), encoding="utf-8")
            service.mark_completed(run_id)

            restored_service = RunLifecycleService(workspace_root=root)

            restored_session = restored_service.get_session(run_id)
            restored_snapshot = restored_service.build_snapshot(run_id)
            self.assertEqual(restored_session.status, "completed")
            self.assertEqual(restored_service.active_run_id(), None)
            self.assertEqual(restored_snapshot["status"], "completed")
            self.assertEqual(restored_snapshot["iteration"], 3)
            self.assertEqual(restored_snapshot["metrics"]["totalTests"], 5)
            self.assertEqual(restored_service.list_runs()[0]["runId"], run_id)

    def test_legacy_manifest_without_report_artifact_is_backfilled_and_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            service = RunLifecycleService(workspace_root=root)
            session = service.create_run(
                RunRequest(project_path=str(project_path), config_path="config.yaml"),
                settings_loader=lambda _: settings,
            )
            run_id = session.run_id

            final_state_path = Path(session.paths["final_state"])
            final_state_path.parent.mkdir(parents=True, exist_ok=True)
            final_state_path.write_text(
                json.dumps({"iteration": 1, "total_tests": 2}),
                encoding="utf-8",
            )
            service.mark_completed(run_id)

            manifest_path = service._session_manifest_path(run_id)
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_payload["paths"].pop("report_artifact", None)
            manifest_path.write_text(
                json.dumps(manifest_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch("comet.web.run_service.logger.warning") as warning_mock:
                restored_service = RunLifecycleService(workspace_root=root)

            restored_session = restored_service.get_session(run_id)
            self.assertEqual(restored_session.status, "completed")
            self.assertEqual(
                restored_session.paths["report_artifact"],
                str(root / "output" / "runs" / run_id / "report.md"),
            )
            self.assertEqual(restored_service.list_runs()[0]["runId"], run_id)
            warning_messages = [
                str(call.args[0]) for call in warning_mock.call_args_list if call.args
            ]
            self.assertFalse(
                any("missing paths" in message for message in warning_messages),
            )

    def test_restored_non_terminal_run_is_marked_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            service = RunLifecycleService(workspace_root=root)
            session = service.create_run(
                RunRequest(project_path=str(project_path), config_path="config.yaml"),
                settings_loader=lambda _: settings,
            )

            restored_service = RunLifecycleService(workspace_root=root)

            restored_session = restored_service.get_session(session.run_id)
            self.assertEqual(restored_session.status, "failed")
            self.assertEqual(
                restored_session.error,
                "运行在 Web 服务重启后无法恢复，已标记为失败。",
            )

    def test_corrupted_state_file_does_not_break_restored_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
            )

            service = RunLifecycleService(workspace_root=root)
            session = service.create_run(
                RunRequest(project_path=str(project_path), config_path="config.yaml"),
                settings_loader=lambda _: settings,
            )
            Path(session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
            Path(session.paths["final_state"]).write_text("{broken json", encoding="utf-8")
            service.mark_completed(session.run_id)

            restored_service = RunLifecycleService(workspace_root=root)

            restored_snapshot = restored_service.build_snapshot(session.run_id)
            self.assertEqual(restored_snapshot["status"], "completed")
            self.assertEqual(restored_snapshot["iteration"], 0)
            self.assertTrue(restored_snapshot["isHistorical"])

    def test_invalid_manifest_paths_are_ignored_during_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            run_dir = root / "state" / "runs" / "run-bad"
            run_dir.mkdir(parents=True)
            (run_dir / "session.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-bad",
                        "status": "completed",
                        "created_at": "2026-03-13T00:00:00+00:00",
                        "project_path": str(root / "project"),
                        "config_path": "config.yaml",
                        "paths": {
                            "state": str(run_dir),
                            "output": "/tmp/outside-output",
                            "sandbox": str(root / "sandbox" / "runs" / "run-bad"),
                            "log": str(root / "logs" / "runs" / "run-bad" / "run.log"),
                            "database": str(root / "state" / "runs" / "run-bad" / "comet.db"),
                            "resolved_config": str(run_dir / "resolved_config.json"),
                            "final_state": str(
                                root / "output" / "runs" / "run-bad" / "final_state.json"
                            ),
                            "interrupted_state": str(
                                root / "output" / "runs" / "run-bad" / "interrupted_state.json"
                            ),
                        },
                        "path_snapshot": {
                            "state": str(run_dir),
                            "output": str(root / "output" / "runs" / "run-bad"),
                            "sandbox": str(root / "sandbox" / "runs" / "run-bad"),
                            "log": str(root / "logs" / "runs" / "run-bad" / "run.log"),
                            "database": str(root / "state" / "runs" / "run-bad" / "comet.db"),
                        },
                        "config_snapshot": {"agent": {"parallel": {"enabled": False}}},
                    }
                ),
                encoding="utf-8",
            )

            restored_service = RunLifecycleService(workspace_root=root)

            self.assertEqual(restored_service.list_runs(), [])


if __name__ == "__main__":
    unittest.main()
