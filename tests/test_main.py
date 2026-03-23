import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main
from comet.config.settings import LLMConfig, Settings


class _NoopThread:
    def __init__(self, target=None, daemon=None, name=None) -> None:
        self.target = target
        self.daemon = daemon
        self.name = name

    def start(self) -> None:
        return None

    def join(self, timeout=None) -> None:
        _ = timeout


class RunEvolutionPreprocessingExitTests(unittest.TestCase):
    def _build_components(self, config: Settings):
        planner = MagicMock()
        planner.state = SimpleNamespace(iteration=0)

        sandbox_manager = MagicMock()
        sandbox_manager.create_workspace_sandbox.return_value = "/tmp/workspace"

        project_scanner = MagicMock()
        project_scanner.scan_project.return_value = {"total_classes": 1, "total_files": 1}

        java_executor = MagicMock()
        java_executor.run_tests_with_coverage.return_value = {"success": True}

        phases: list[str] = []

        def publish_snapshot(*, state, phase) -> None:
            _ = state
            phases.append(phase["key"])

        components = {
            "config": config,
            "sandbox_manager": sandbox_manager,
            "project_scanner": project_scanner,
            "planner": planner,
            "planner_type": "standard",
            "java_executor": java_executor,
            "runtime_snapshot_publisher": publish_snapshot,
        }
        return components, planner, sandbox_manager, java_executor, phases

    @patch("main.threading.Thread", _NoopThread)
    @patch("main.time.sleep")
    @patch("comet.parallel_preprocessing.ParallelPreprocessor")
    def test_run_evolution_exits_after_preprocessing_when_configured(
        self,
        preprocessor_cls: MagicMock,
        sleep_mock: MagicMock,
    ) -> None:
        _ = sleep_mock
        config = Settings(llm=LLMConfig(api_key="test-key"))
        config.preprocessing.enabled = True
        config.preprocessing.exit_after_preprocessing = True
        components, planner, sandbox_manager, java_executor, phases = self._build_components(config)

        preprocessor_cls.return_value.run.return_value = {
            "total_methods": 2,
            "success": 2,
            "failed": 0,
            "total_tests": 4,
            "total_mutants": 1,
        }

        main.run_evolution("/tmp/project", components)

        preprocessor_cls.assert_called_once_with(config, components)
        preprocessor_cls.return_value.run.assert_called_once_with("/tmp/project", "/tmp/workspace")
        java_executor.run_tests_with_coverage.assert_called_once_with("/tmp/workspace")
        planner.run.assert_not_called()
        planner.save_state.assert_called_once_with(
            str(config.resolve_output_root() / "final_state.json")
        )
        sandbox_manager.export_test_files.assert_called_once_with("workspace", "/tmp/project")
        self.assertEqual(phases, ["preprocessing", "completed"])

    @patch("main.threading.Thread", _NoopThread)
    @patch("main.time.sleep")
    @patch("comet.parallel_preprocessing.ParallelPreprocessor")
    def test_run_evolution_keeps_main_loop_when_exit_flag_is_disabled(
        self,
        preprocessor_cls: MagicMock,
        sleep_mock: MagicMock,
    ) -> None:
        _ = sleep_mock
        config = Settings(llm=LLMConfig(api_key="test-key"))
        config.preprocessing.enabled = True
        config.preprocessing.exit_after_preprocessing = False
        components, planner, sandbox_manager, java_executor, phases = self._build_components(config)

        preprocessor_cls.return_value.run.return_value = {
            "total_methods": 1,
            "success": 1,
            "failed": 0,
            "total_tests": 2,
            "total_mutants": 0,
        }

        main.run_evolution("/tmp/project", components)

        preprocessor_cls.assert_called_once_with(config, components)
        java_executor.run_tests_with_coverage.assert_called_once_with("/tmp/workspace")
        planner.run.assert_called_once_with(
            stop_on_no_improvement_rounds=config.evolution.stop_on_no_improvement_rounds,
            min_improvement_threshold=config.evolution.min_improvement_threshold,
        )
        planner.save_state.assert_called_once_with(
            str(config.resolve_output_root() / "final_state.json")
        )
        sandbox_manager.export_test_files.assert_called_once_with("workspace", "/tmp/project")
        self.assertEqual(phases, ["preprocessing", "running", "completed"])


if __name__ == "__main__":
    unittest.main()
