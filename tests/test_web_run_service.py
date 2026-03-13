import argparse
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from comet.config.settings import LLMConfig, LoggingConfig, PathsConfig, Settings
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
                paths=PathsConfig(
                    state=str(root / "state"),
                    output=str(root / "output"),
                    sandbox=str(root / "sandbox"),
                ),
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
                    path_overrides={"output": str(root / "run-output")},
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
            self.assertEqual(settings.paths.output, str(root / "run-output"))
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
                paths=PathsConfig(
                    state=str(root / "state"),
                    output=str(root / "output"),
                    sandbox=str(root / "sandbox"),
                ),
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
            output_dir="/tmp/output",
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
                paths=PathsConfig(
                    state=str(root / "state"),
                    output=str(root / "output"),
                    sandbox=str(root / "sandbox"),
                ),
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
            self.assertEqual(resolved_snapshot["paths"]["state"], session.paths["state"])
            self.assertEqual(resolved_snapshot["paths"]["output"], session.paths["output"])
            self.assertEqual(resolved_snapshot["paths"]["sandbox"], session.paths["sandbox"])
            self.assertEqual(resolved_snapshot["logging"]["file"], session.paths["log"])
            self.assertNotIn("vector_db", resolved_snapshot["knowledge"])

    def test_second_active_run_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                logging=LoggingConfig(file=str(root / "default.log")),
                paths=PathsConfig(
                    state=str(root / "state"),
                    output=str(root / "output"),
                    sandbox=str(root / "sandbox"),
                ),
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


if __name__ == "__main__":
    unittest.main()
