import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import main
from comet.config.settings import LLMConfig, Settings
from comet.models import Mutant, MutationPatch, TestCase, TestMethod
from comet.utils.method_keys import build_method_key
from comet.utils.sandbox import SandboxManager
from comet.web.study_runner import StudyArmContext, StudyArmPaths, StudyRunner
from tests.test_study_runner import _build_pit_mutations_xml, _FakeCoverage, _FakeDatabase


class _NoopThread:
    def __init__(self, target=None, daemon=None, name=None) -> None:
        self.target = target
        self.daemon = daemon
        self.name = name

    def start(self) -> None:
        return None

    def join(self, timeout=None) -> None:
        _ = timeout


class _StudyCliFakeDatabase(_FakeDatabase):
    def __init__(self, calculator_source: Path) -> None:
        super().__init__()
        self.calculator_source = calculator_source.resolve()
        self.saved_coverages: list[tuple[object, int]] = []
        self.closed = False

    def get_all_class_mappings(self) -> list[dict[str, str]]:
        return [{"simple_name": "Calculator", "file_path": str(self.calculator_source)}]

    def get_class_file_path(self, class_name: str) -> str | None:
        if class_name in {"Calculator", "com.example.Calculator"}:
            return str(self.calculator_source)
        return None

    def save_method_coverage(self, coverage: _FakeCoverage, iteration: int) -> None:
        self.saved_coverages.append((coverage, iteration))

    def close(self) -> None:
        self.closed = True

    def save_mutant(self, mutant: Mutant) -> None:
        key = (str(mutant.class_name), str(mutant.method_name), mutant.method_signature)
        existing = self.mutants.get(key, [])
        filtered = [item for item in existing if item.id != mutant.id]
        filtered.append(mutant)
        self.mutants[key] = filtered


class _StudyCliFakeJavaExecutor:
    def __init__(self, calculator_source: Path) -> None:
        self.calculator_source = calculator_source.resolve()
        self.public_method_files: list[Path] = []

    def get_public_methods(self, file_path: str) -> list[dict[str, object]]:
        resolved_path = Path(file_path).resolve()
        self.public_method_files.append(resolved_path)
        if resolved_path != self.calculator_source:
            return []
        return [
            {
                "className": "Calculator",
                "name": "add",
                "signature": "int add(int, int)",
                "range": {"begin": 17, "end": 19},
            }
        ]


class _StudyCliFakeTools:
    def __init__(
        self,
        db: _StudyCliFakeDatabase,
        sandbox_manager: SandboxManager,
        phase_name: str,
    ) -> None:
        self.db = db
        self.sandbox_manager = sandbox_manager
        self.project_path = ""
        self.original_project_path = ""
        self.phase_name = phase_name
        self.state = None
        self.knowledge_base = None
        self.test_generator = None

    def _write_test_case(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None,
        suffix: str,
        method_body: str,
    ) -> TestCase:
        package_name = "com.example"
        method_title = method_name[0].upper() + method_name[1:]
        test_class_name = f"{class_name}{method_title}{suffix}Test"
        full_code = (
            f"package {package_name};\n\npublic class {test_class_name} {{\n  {method_body}\n}}\n"
        )
        test_case = TestCase(
            id=f"{suffix}-{build_method_key(class_name, method_name, method_signature)}",
            class_name=test_class_name,
            target_class=class_name,
            package_name=package_name,
            imports=[],
            methods=[
                TestMethod(
                    method_name=suffix[0].lower() + suffix[1:],
                    code=method_body,
                    target_method=method_name,
                    target_method_signature=method_signature,
                )
            ],
            full_code=full_code,
            compile_success=True,
        )
        self.db.save_test_case(test_case)
        target_dir = Path(self.project_path) / "src" / "test" / "java" / "com" / "example"
        target_dir.mkdir(parents=True, exist_ok=True)
        _ = (target_dir / f"{test_class_name}.java").write_text(full_code, encoding="utf-8")
        return test_case

    def generate_tests(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> dict[str, object]:
        test_case = self._write_test_case(
            class_name,
            method_name,
            method_signature,
            "Baseline",
            "@org.junit.jupiter.api.Test void baselineSeed() {}",
        )
        return {"generated": 1, "compile_success": True, "test_id": test_case.id}

    def generate_mutants(
        self,
        class_name: str,
        method_name: str | None = None,
        method_signature: str | None = None,
    ) -> dict[str, object]:
        target_id = build_method_key(class_name, method_name, method_signature)
        mutants = [
            Mutant(
                id=f"{target_id}-killed",
                class_name=class_name,
                method_name=method_name,
                method_signature=method_signature,
                patch=MutationPatch(
                    file_path="src/main/java/com/example/Calculator.java",
                    line_start=17,
                    line_end=17,
                    original_code="return a + b;",
                    mutated_code="return a - b;",
                    mutator="MathMutator",
                    operator="MathMutator",
                ),
                status="valid",
                survived=False,
            ),
            Mutant(
                id=f"{target_id}-survived",
                class_name=class_name,
                method_name=method_name,
                method_signature=method_signature,
                patch=MutationPatch(
                    file_path="src/main/java/com/example/Calculator.java",
                    line_start=18,
                    line_end=18,
                    original_code="return a + b;",
                    mutated_code="return a + 1;",
                    mutator="NegateConditionalsMutator",
                    operator="NegateConditionalsMutator",
                ),
                status="valid",
                survived=True,
            ),
        ]
        for mutant in mutants:
            self.db.save_mutant(mutant)

        return {
            "generated": len(mutants),
            "mutant_ids": [mutant.id for mutant in mutants],
            "status": "completed",
        }

    def refine_tests(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> dict[str, object]:
        suffix = self.phase_name if self.phase_name != "baseline" else "Refined"
        self._write_test_case(
            class_name,
            method_name,
            method_signature,
            suffix,
            f"@org.junit.jupiter.api.Test void {suffix.lower()}ImprovesCoverage() {{}}",
        )
        return {"refined": 1}

    def run_evaluation(self) -> dict[str, object]:
        if self.state is None:
            raise AssertionError("state 必须在评估前设置")

        current_target = self.state.current_target
        class_name = str(current_target["class_name"])
        method_name = str(current_target["method_name"])
        method_signature = current_target.get("method_signature")
        target_id = build_method_key(class_name, method_name, method_signature)
        coverage_rate = 0.5 if self.phase_name == "baseline" else 0.9
        key = (class_name, method_name, method_signature)
        self.db.coverages[key] = _FakeCoverage(line_coverage_rate=coverage_rate)

        survived = self.phase_name == "baseline"
        self.db.mutants[key] = [
            Mutant(
                id=f"{target_id}-killed",
                class_name=class_name,
                method_name=method_name,
                method_signature=method_signature,
                patch=MutationPatch(
                    file_path="src/main/java/com/example/Calculator.java",
                    line_start=17,
                    line_end=17,
                    original_code="return a + b;",
                    mutated_code="return a - b;",
                    mutator="MathMutator",
                    operator="MathMutator",
                ),
                status="valid",
                survived=False,
                evaluated_at=datetime.now(),
            ),
            Mutant(
                id=f"{target_id}-survived",
                class_name=class_name,
                method_name=method_name,
                method_signature=method_signature,
                patch=MutationPatch(
                    file_path="src/main/java/com/example/Calculator.java",
                    line_start=18,
                    line_end=18,
                    original_code="return a + b;",
                    mutated_code="return a + 1;",
                    mutator="NegateConditionalsMutator",
                    operator="NegateConditionalsMutator",
                ),
                status="valid",
                survived=survived,
                evaluated_at=datetime.now(),
            ),
        ]
        return {
            "evaluated": 2,
            "killed": 1 if survived else 2,
            "survived": 1 if survived else 0,
            "status": "completed",
        }


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


class StudyCliTests(unittest.TestCase):
    def test_parse_args_supports_study_defaults(self) -> None:
        args = main.parse_args(
            [
                "study",
                "--project-path",
                "examples/calculator-demo",
                "--output-dir",
                ".artifacts/study-demo",
            ]
        )

        self.assertEqual(args.command, "study")
        self.assertEqual(args.project_path, "examples/calculator-demo")
        self.assertEqual(args.sample_size, 12)
        self.assertEqual(args.seed, 42)
        self.assertEqual(args.output_dir, ".artifacts/study-demo")
        self.assertIsNone(args.bug_reports_dir)

    def test_parse_args_supports_study_bug_reports_dir(self) -> None:
        args = main.parse_args(
            [
                "study",
                "--project-path",
                "examples/calculator-demo",
                "--output-dir",
                ".artifacts/study-demo",
                "--bug-reports-dir",
                "examples/bug-reports",
            ]
        )

        self.assertEqual(args.bug_reports_dir, "examples/bug-reports")

    def test_parse_args_reports_missing_study_project_path(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as exc_info:
            main.parse_args(["study", "--output-dir", ".artifacts/study-demo"])

        self.assertEqual(exc_info.exception.code, 2)
        self.assertIn("--project-path", stderr.getvalue())

    def test_run_study_command_passes_bug_reports_dir_to_study_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            bug_reports_dir = root / "bug-reports"
            bug_reports_dir.mkdir()
            (bug_reports_dir / "bug.md").write_text("# bug", encoding="utf-8")

            args = SimpleNamespace(
                project_path=str(project_path),
                config="config.yaml",
                sample_size=3,
                seed=123,
                output_dir=str(root / "study-output"),
                bug_reports_dir=str(bug_reports_dir),
                debug=True,
            )
            settings = Settings(llm=LLMConfig(api_key="test-key"))
            components = {"tools": object(), "db": object(), "sandbox_manager": object()}
            artifacts = SimpleNamespace(
                summary_path=root / "study-output" / "summary.json",
                per_method_path=root / "study-output" / "per_method.csv",
                per_mutant_path=root / "study-output" / "per_mutant.jsonl",
                sampled_methods_path=root / "study-output" / "sampled_methods.json",
            )
            initializer_calls: list[tuple[Path | None, str | None, bool, bool]] = []

            def fake_initializer(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
                *,
                skip_bug_report_index: bool = False,
            ):
                initializer_calls.append(
                    (
                        config.resolve_bug_reports_dir(),
                        bug_reports_dir,
                        parallel_mode,
                        skip_bug_report_index,
                    )
                )
                return components

            def fake_study_runner(**kwargs):
                self.assertIs(kwargs["settings"], settings)
                self.assertEqual(
                    kwargs["settings"].resolve_bug_reports_dir(), bug_reports_dir.resolve()
                )
                kwargs["system_initializer"](kwargs["settings"], parallel_mode=False)
                return artifacts

            with (
                patch.object(main, "configure_logging") as configure_logging_mock,
                patch.object(main, "initialize_system", side_effect=fake_initializer) as init_mock,
                patch.object(
                    main, "run_default_study", side_effect=fake_study_runner
                ) as study_mock,
            ):
                exit_code = main.run_study_command(
                    args,
                    settings_loader=lambda _: settings,
                    system_initializer=main.initialize_system,
                    study_runner=main.run_default_study,
                )

        self.assertEqual(exit_code, 0)
        configure_logging_mock.assert_called_once()
        self.assertEqual(
            initializer_calls,
            [
                (bug_reports_dir.resolve(), str(bug_reports_dir.resolve()), False, True),
                (bug_reports_dir.resolve(), str(bug_reports_dir.resolve()), False, True),
            ],
        )
        study_mock.assert_called_once_with(
            project_path=str(project_path.resolve()),
            output_dir=(root / "study-output").resolve(),
            sample_size=3,
            seed=123,
            components=components,
            settings=settings,
            system_initializer=ANY,
        )
        self.assertEqual(init_mock.call_count, 2)

    def test_run_study_command_prepares_sampling_coverage_store_when_project_has_tests(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            test_root = project_path / "src" / "test" / "java" / "pkg"
            test_root.mkdir(parents=True)
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            (test_root / "ExistingTest.java").write_text(
                "package pkg;\n"
                "import org.junit.jupiter.api.Test;\n"
                "class ExistingTest {\n"
                "  @Test\n"
                "  void testWarmup() {}\n"
                "}\n",
                encoding="utf-8",
            )

            args = SimpleNamespace(
                project_path=str(project_path),
                config="config.yaml",
                sample_size=2,
                seed=11,
                output_dir=str(root / "study-output"),
                bug_reports_dir=None,
                debug=False,
            )
            settings = Settings(llm=LLMConfig(api_key="test-key"))
            initializer_outputs: list[dict[str, object]] = []
            runner_components: dict[str, object] = {}

            def fake_initializer(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
                *,
                skip_bug_report_index: bool = False,
            ) -> dict[str, object]:
                self.assertIsNone(bug_reports_dir)
                self.assertFalse(parallel_mode)
                self.assertTrue(skip_bug_report_index)
                db = _StudyCliFakeDatabase(project_path / "src" / "main" / "java" / "Demo.java")
                sandbox_manager = SandboxManager(str(config.resolve_sandbox_root()))
                java_executor = MagicMock()

                def warmup(workspace: str) -> dict[str, object]:
                    jacoco_dir = Path(workspace) / "target" / "site" / "jacoco"
                    jacoco_dir.mkdir(parents=True, exist_ok=True)
                    (jacoco_dir / "jacoco.xml").write_text("<report/>", encoding="utf-8")
                    return {"success": True}

                java_executor.run_tests_with_coverage.side_effect = warmup
                components = {
                    "tools": object(),
                    "db": db,
                    "sandbox_manager": sandbox_manager,
                    "java_executor": java_executor,
                }
                initializer_outputs.append(components)
                return components

            artifacts = SimpleNamespace(
                summary_path=root / "study-output" / "summary.json",
                per_method_path=root / "study-output" / "per_method.csv",
                per_mutant_path=root / "study-output" / "per_mutant.jsonl",
                sampled_methods_path=root / "study-output" / "sampled_methods.json",
            )

            def fake_study_runner(**kwargs):
                runner_components.update(kwargs["components"])
                return artifacts

            with (
                patch.object(main, "configure_logging"),
                patch.object(main, "initialize_system", side_effect=fake_initializer),
                patch.object(main, "run_default_study", side_effect=fake_study_runner),
                patch.object(
                    main.CoverageParser,
                    "parse_jacoco_xml_with_lines",
                    return_value=[_FakeCoverage(line_coverage_rate=0.5)],
                ),
            ):
                exit_code = main.run_study_command(
                    args,
                    settings_loader=lambda _: settings,
                    system_initializer=main.initialize_system,
                    study_runner=main.run_default_study,
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("sampling_coverage_store", runner_components)
        self.assertIsNot(runner_components["sampling_coverage_store"], runner_components["db"])
        warmup_db = initializer_outputs[1]["db"]
        assert isinstance(warmup_db, _StudyCliFakeDatabase)
        self.assertEqual(len(warmup_db.saved_coverages), 1)
        self.assertTrue(warmup_db.closed)

    def test_run_study_command_skips_sampling_coverage_warmup_without_project_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            args = SimpleNamespace(
                project_path=str(project_path),
                config="config.yaml",
                sample_size=2,
                seed=11,
                output_dir=str(root / "study-output"),
                bug_reports_dir=None,
                debug=False,
            )
            settings = Settings(llm=LLMConfig(api_key="test-key"))
            runner_components: dict[str, object] = {}
            init_count = 0

            def fake_initializer(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
                *,
                skip_bug_report_index: bool = False,
            ) -> dict[str, object]:
                nonlocal init_count
                init_count += 1
                _ = (config, bug_reports_dir, parallel_mode, skip_bug_report_index)
                return {
                    "tools": object(),
                    "db": object(),
                    "sandbox_manager": object(),
                    "java_executor": object(),
                }

            artifacts = SimpleNamespace(
                summary_path=root / "study-output" / "summary.json",
                per_method_path=root / "study-output" / "per_method.csv",
                per_mutant_path=root / "study-output" / "per_mutant.jsonl",
                sampled_methods_path=root / "study-output" / "sampled_methods.json",
            )

            def fake_study_runner(**kwargs):
                runner_components.update(kwargs["components"])
                return artifacts

            with (
                patch.object(main, "configure_logging"),
                patch.object(main, "initialize_system", side_effect=fake_initializer),
                patch.object(main, "run_default_study", side_effect=fake_study_runner),
            ):
                exit_code = main.run_study_command(
                    args,
                    settings_loader=lambda _: settings,
                    system_initializer=main.initialize_system,
                    study_runner=main.run_default_study,
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(init_count, 1)
        self.assertNotIn("sampling_coverage_store", runner_components)

    def test_run_study_command_falls_back_when_sampling_coverage_warmup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            test_root = project_path / "src" / "test" / "java" / "pkg"
            test_root.mkdir(parents=True)
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            (test_root / "ExistingTest.java").write_text(
                "package pkg;\n"
                "import org.junit.jupiter.api.Test;\n"
                "class ExistingTest {\n"
                "  @Test\n"
                "  void testWarmup() {}\n"
                "}\n",
                encoding="utf-8",
            )

            args = SimpleNamespace(
                project_path=str(project_path),
                config="config.yaml",
                sample_size=2,
                seed=11,
                output_dir=str(root / "study-output"),
                bug_reports_dir=None,
                debug=False,
            )
            settings = Settings(llm=LLMConfig(api_key="test-key"))
            runner_components: dict[str, object] = {}
            initializer_outputs: list[dict[str, object]] = []

            def fake_initializer(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
                *,
                skip_bug_report_index: bool = False,
            ) -> dict[str, object]:
                _ = (bug_reports_dir, parallel_mode, skip_bug_report_index)
                db = _StudyCliFakeDatabase(project_path / "src" / "main" / "java" / "Demo.java")
                sandbox_manager = SandboxManager(str(config.resolve_sandbox_root()))
                java_executor = MagicMock()
                java_executor.run_tests_with_coverage.return_value = {
                    "success": False,
                    "error": "warmup failed",
                }
                components = {
                    "tools": object(),
                    "db": db,
                    "sandbox_manager": sandbox_manager,
                    "java_executor": java_executor,
                }
                initializer_outputs.append(components)
                return components

            artifacts = SimpleNamespace(
                summary_path=root / "study-output" / "summary.json",
                per_method_path=root / "study-output" / "per_method.csv",
                per_mutant_path=root / "study-output" / "per_mutant.jsonl",
                sampled_methods_path=root / "study-output" / "sampled_methods.json",
            )

            def fake_study_runner(**kwargs):
                runner_components.update(kwargs["components"])
                return artifacts

            with (
                patch.object(main, "configure_logging"),
                patch.object(main, "initialize_system", side_effect=fake_initializer),
                patch.object(main, "run_default_study", side_effect=fake_study_runner),
            ):
                exit_code = main.run_study_command(
                    args,
                    settings_loader=lambda _: settings,
                    system_initializer=main.initialize_system,
                    study_runner=main.run_default_study,
                )

        self.assertEqual(exit_code, 0)
        self.assertNotIn("sampling_coverage_store", runner_components)
        warmup_db = initializer_outputs[1]["db"]
        assert isinstance(warmup_db, _StudyCliFakeDatabase)
        self.assertTrue(warmup_db.closed)

    def test_main_routes_study_command_through_real_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            bug_reports_dir = root / "bug-reports"
            bug_reports_dir.mkdir()
            output_dir = root / "study-output"

            args = SimpleNamespace(
                command="study",
                project_path=str(project_path),
                config="config.yaml",
                sample_size=12,
                seed=42,
                output_dir=str(output_dir),
                bug_reports_dir=str(bug_reports_dir),
                debug=False,
            )
            artifacts = SimpleNamespace(
                summary_path=output_dir / "summary.json",
                per_method_path=output_dir / "per_method.csv",
                per_mutant_path=output_dir / "per_mutant.jsonl",
                sampled_methods_path=output_dir / "sampled_methods.json",
            )
            init_calls: list[tuple[str | None, bool]] = []
            settings = Settings(llm=LLMConfig(api_key="test-key"))

            def fake_initializer(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
                *,
                skip_bug_report_index: bool = False,
            ):
                _ = (config, parallel_mode)
                init_calls.append((bug_reports_dir, skip_bug_report_index))
                return {
                    "tools": object(),
                    "db": object(),
                    "sandbox_manager": object(),
                    "java_executor": object(),
                }

            def fake_study_runner(**kwargs):
                self.assertEqual(
                    kwargs["settings"].resolve_bug_reports_dir(),
                    bug_reports_dir.resolve(),
                )
                return artifacts

            with (
                patch.object(main, "parse_args", return_value=args),
                patch.object(main, "configure_logging"),
                patch.object(main, "run_cli") as run_cli_mock,
                patch.dict(
                    main.run_study_command.__kwdefaults__,
                    {
                        "settings_loader": lambda _: settings,
                        "system_initializer": fake_initializer,
                        "study_runner": fake_study_runner,
                    },
                ),
            ):
                main.main()

        self.assertEqual(init_calls, [(str(bug_reports_dir.resolve()), True)])
        run_cli_mock.assert_not_called()

    def test_main_runs_study_e2e_on_calculator_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "study-output"
            calculator_demo = Path("examples/calculator-demo")
            calculator_source = (
                calculator_demo / "src" / "main" / "java" / "com" / "example" / "Calculator.java"
            ).resolve()
            settings = Settings(llm=LLMConfig(api_key="test-key"))
            java_executor = _StudyCliFakeJavaExecutor(calculator_source)
            initializer_output_roots: list[Path] = []

            def fake_initializer(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
                *,
                skip_bug_report_index: bool = False,
            ) -> dict[str, object]:
                self.assertIsNone(bug_reports_dir)
                self.assertFalse(parallel_mode)
                self.assertTrue(skip_bug_report_index)
                initializer_output_roots.append(config.resolve_output_root())
                db = _StudyCliFakeDatabase(calculator_source)
                sandbox_manager = SandboxManager(str(config.resolve_sandbox_root()))
                phase_name = (
                    config.resolve_output_root().name
                    if config.resolve_output_root().name in {"M0", "M2", "M3"}
                    else "baseline"
                )
                return {
                    "tools": _StudyCliFakeTools(db, sandbox_manager, phase_name),
                    "db": db,
                    "sandbox_manager": sandbox_manager,
                    "java_executor": java_executor,
                }

            def fake_pit_run(self: StudyRunner, workspace: str) -> dict[str, object]:
                report_dir = Path(workspace) / "target" / "pit-reports"
                report_dir.mkdir(parents=True, exist_ok=True)
                _ = (report_dir / "mutations.xml").write_text(
                    _build_pit_mutations_xml(),
                    encoding="utf-8",
                )
                return {"success": True}

            args = SimpleNamespace(
                command="study",
                project_path=str(calculator_demo),
                config="config.yaml",
                sample_size=1,
                seed=7,
                output_dir=str(output_dir),
                bug_reports_dir=None,
                debug=False,
            )

            with (
                patch.object(main, "parse_args", return_value=args),
                patch.object(main, "configure_logging"),
                patch.object(main, "run_cli") as run_cli_mock,
                patch.dict(
                    main.run_study_command.__kwdefaults__,
                    {
                        "settings_loader": lambda _config_path: settings,
                        "system_initializer": fake_initializer,
                    },
                ),
                patch("comet.web.study_runner.create_knowledge_base", return_value=MagicMock()),
                patch.object(StudyRunner, "_run_pit_mutation_coverage", fake_pit_run),
            ):
                main.main()

            run_cli_mock.assert_not_called()
            self.assertEqual(len(initializer_output_roots), 6)
            self.assertEqual(
                java_executor.public_method_files,
                [calculator_source],
            )

            summary_path = output_dir / "summary.json"
            per_method_path = output_dir / "per_method.csv"
            per_mutant_path = output_dir / "per_mutant.jsonl"
            sampled_methods_path = output_dir / "sampled_methods.json"
            self.assertTrue(summary_path.exists())
            self.assertTrue(per_method_path.exists())
            self.assertTrue(per_mutant_path.exists())
            self.assertTrue(sampled_methods_path.exists())

            sampled_methods = json.loads(sampled_methods_path.read_text(encoding="utf-8"))
            self.assertEqual(len(sampled_methods), 1)
            self.assertEqual(sampled_methods[0]["method_name"], "add")
            target_id = sampled_methods[0]["target_id"]
            for arm in ("baseline", "M0", "M2", "M3"):
                archived_files = list((output_dir / "artifacts" / target_id / arm).rglob("*.java"))
                self.assertTrue(archived_files, msg=f"{arm} 应导出测试工件")

    def test_study_runner_indexes_bug_reports_for_m3_arm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            bug_reports_dir = root / "bug-reports"
            bug_reports_dir.mkdir()
            (bug_reports_dir / "bug.md").write_text("# bug", encoding="utf-8")

            settings = Settings(llm=LLMConfig(api_key="test-key"))
            settings.set_bug_reports_dir(bug_reports_dir)
            runner = StudyRunner(
                workspace_project_path=str(root),
                artifacts_root=str(root / "artifacts"),
                settings=settings,
            )
            context = StudyArmContext(
                arm="M3",
                target_id="demo#sum",
                config=settings,
                paths=StudyArmPaths(
                    target_id="demo#sum",
                    arm="M3",
                    state_root=root / "state",
                    output_root=root / "output",
                    sandbox_root=root / "sandbox",
                    workspace_root=root / "sandbox" / "workspace",
                    artifacts_root=root / "artifacts",
                ),
                sandbox_manager=MagicMock(),
            )
            knowledge_base = MagicMock()

            with patch("comet.web.study_runner.create_knowledge_base", return_value=knowledge_base):
                result = runner.create_arm_knowledge_base(context)

        self.assertIs(result, knowledge_base)
        knowledge_base.index_bug_reports.assert_called_once_with(str(bug_reports_dir.resolve()))

    def test_run_study_command_uses_skip_bug_report_index_path_for_study_initializers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            bug_reports_dir = root / "bug-reports"
            bug_reports_dir.mkdir()
            (bug_reports_dir / "bug.md").write_text("# bug", encoding="utf-8")

            args = SimpleNamespace(
                project_path=str(project_path),
                config="config.yaml",
                sample_size=2,
                seed=11,
                output_dir=str(root / "study-output"),
                bug_reports_dir=str(bug_reports_dir),
                debug=False,
            )
            settings = Settings(llm=LLMConfig(api_key="test-key"))
            components = {"tools": object(), "db": object(), "sandbox_manager": object()}
            artifacts = SimpleNamespace(
                summary_path=root / "study-output" / "summary.json",
                per_method_path=root / "study-output" / "per_method.csv",
                per_mutant_path=root / "study-output" / "per_mutant.jsonl",
                sampled_methods_path=root / "study-output" / "sampled_methods.json",
            )
            initializer_calls: list[tuple[str | None, bool]] = []
            indexing_calls: list[str | None] = []

            def fake_initializer(
                config: Settings,
                bug_reports_dir: str | None = None,
                parallel_mode: bool = False,
                *,
                skip_bug_report_index: bool = False,
            ) -> dict[str, object]:
                self.assertFalse(parallel_mode)
                _ = config
                initializer_calls.append((bug_reports_dir, skip_bug_report_index))
                if bug_reports_dir is not None and not skip_bug_report_index:
                    indexing_calls.append(bug_reports_dir)
                return components

            def fake_study_runner(**kwargs):
                kwargs["system_initializer"](kwargs["settings"], parallel_mode=False)
                return artifacts

            with (
                patch.object(main, "configure_logging"),
                patch.object(main, "initialize_system", side_effect=fake_initializer),
                patch.object(main, "run_default_study", side_effect=fake_study_runner),
            ):
                exit_code = main.run_study_command(
                    args,
                    settings_loader=lambda _: settings,
                    system_initializer=main.initialize_system,
                    study_runner=main.run_default_study,
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            initializer_calls,
            [
                (str(bug_reports_dir.resolve()), True),
                (str(bug_reports_dir.resolve()), True),
            ],
        )
        self.assertEqual(
            indexing_calls,
            [],
            msg="study CLI 应统一走 skip-index 初始化分支，避免任何 study initializer 再次承担 bug report 索引职责",
        )


if __name__ == "__main__":
    unittest.main()
