import csv
import json
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from comet.config.settings import LLMConfig, Settings
from comet.knowledge.knowledge_base import RAGKnowledgeBase
from comet.models import Mutant, MutationPatch, TestCase, TestMethod
from comet.utils.method_keys import build_method_key
from comet.utils.sandbox import SandboxManager
from comet.web.study_protocol import BASELINE_ARCHIVE_DIR
from comet.web.study_runner import (
    FrozenStudyMethod,
    StudyArmContext,
    StudyRunner,
    _build_guidance_mutants,
    _StudyBaselineState,
)


@dataclass(slots=True)
class _FakeCoverage:
    line_coverage_rate: float


class _FakeDatabase:
    def __init__(self) -> None:
        self.tests: dict[tuple[str, str, str | None], list[TestCase]] = {}
        self.coverages: dict[tuple[str, str, str | None], _FakeCoverage] = {}
        self.mutants: dict[tuple[str, str, str | None], list[Mutant]] = {}

    def save_mutant(self, mutant: Mutant) -> None:
        key = (mutant.class_name, mutant.method_name or "", mutant.method_signature)
        existing = self.mutants.get(key, [])
        filtered = [item for item in existing if item.id != mutant.id]
        filtered.append(mutant)
        self.mutants[key] = filtered

    def save_test_case(self, test_case: TestCase) -> None:
        if not test_case.methods:
            return

        for method in test_case.methods:
            key = (
                test_case.target_class,
                method.target_method,
                method.target_method_signature,
            )
            existing = self.tests.get(key, [])
            filtered = [item for item in existing if item.id != test_case.id]
            filtered.append(test_case)
            self.tests[key] = filtered

    def get_tests_by_target_method(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> list[TestCase]:
        return list(self.tests.get((class_name, method_name, method_signature), []))

    def get_method_coverage(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> _FakeCoverage | None:
        return self.coverages.get((class_name, method_name, method_signature))

    def get_mutants_by_method(
        self,
        class_name: str,
        method_name: str,
        status: str | None = "valid",
        method_signature: str | None = None,
    ) -> list[Mutant]:
        mutants = list(self.mutants.get((class_name, method_name, method_signature), []))
        if status is None:
            return mutants
        return [mutant for mutant in mutants if mutant.status == status]


class _FakeTools:
    def __init__(
        self,
        db: _FakeDatabase,
        sandbox_manager: SandboxManager,
        failing_targets: set[str] | None = None,
        no_mutant_targets: set[str] | None = None,
    ) -> None:
        self.db = db
        self.sandbox_manager = sandbox_manager
        self.project_path = ""
        self.original_project_path = ""
        self.state: Any = None
        self.generate_calls: dict[str, int] = {}
        self.generate_mutant_calls: dict[str, int] = {}
        self.evaluate_calls: dict[str, int] = {}
        self.failing_targets = failing_targets or set()
        self.no_mutant_targets = no_mutant_targets or set()

    def generate_tests(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> dict[str, Any]:
        target_id = build_method_key(class_name, method_name, method_signature)
        self.generate_calls[target_id] = self.generate_calls.get(target_id, 0) + 1

        if target_id in self.failing_targets:
            return {"generated": 0, "compile_success": False, "error": "baseline bootstrap failed"}

        package_name = "pkg"
        test_class_name = f"{class_name}_{method_name}BaselineTest"
        method_code = "@org.junit.jupiter.api.Test void baselineSeed() {}"
        full_code = f"package {package_name};\nclass {test_class_name} {{\n    {method_code}\n}}\n"
        test_case = TestCase(
            id=f"test-{target_id}",
            class_name=test_class_name,
            target_class=class_name,
            package_name=package_name,
            imports=[],
            methods=[
                TestMethod(
                    method_name="baselineSeed",
                    code=method_code,
                    target_method=method_name,
                    target_method_signature=method_signature,
                )
            ],
            full_code=full_code,
            compile_success=True,
        )
        self.db.save_test_case(test_case)

        target_dir = Path(self.project_path) / "src" / "test" / "java" / package_name
        target_dir.mkdir(parents=True, exist_ok=True)
        _ = (target_dir / f"{test_class_name}.java").write_text(full_code, encoding="utf-8")
        return {"generated": 1, "compile_success": True, "test_id": test_case.id}

    def generate_mutants(
        self,
        class_name: str,
        method_name: str | None = None,
        method_signature: str | None = None,
    ) -> dict[str, Any]:
        target_id = build_method_key(class_name, method_name, method_signature)
        self.generate_mutant_calls[target_id] = self.generate_mutant_calls.get(target_id, 0) + 1

        if target_id in self.no_mutant_targets:
            return {
                "generated": 0,
                "status": "empty",
                "reason": "no_mutants",
                "message": f"未生成任何变异体: {class_name}",
                "mutant_ids": [],
            }

        mutants = [
            Mutant(
                id=f"{target_id}-killed",
                class_name=class_name,
                method_name=method_name,
                method_signature=method_signature,
                patch=MutationPatch(
                    file_path="src/main/java/pkg/Alpha.java",
                    line_start=1,
                    line_end=1,
                    original_code="return 1;",
                    mutated_code="return 2;",
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
                    file_path="src/main/java/pkg/Alpha.java",
                    line_start=2,
                    line_end=2,
                    original_code="return 3;",
                    mutated_code="return 4;",
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

    def run_evaluation(self) -> dict[str, Any]:
        current_target = self.state.current_target
        class_name = str(current_target["class_name"])
        method_name = str(current_target["method_name"])
        method_signature = current_target.get("method_signature")
        target_id = build_method_key(class_name, method_name, method_signature)
        self.evaluate_calls[target_id] = self.evaluate_calls.get(target_id, 0) + 1

        if target_id in self.failing_targets:
            return {
                "evaluated": 0,
                "status": "blocked",
                "reason": "no_tests",
                "message": "baseline bootstrap failed",
            }

        key = (class_name, method_name, method_signature)
        mutants = self.db.get_mutants_by_method(
            class_name, method_name, method_signature=method_signature
        )
        if not mutants:
            return {
                "evaluated": 0,
                "status": "empty",
                "reason": "no_mutants",
                "message": "没有变异体需要评估",
            }

        self.db.coverages[key] = _FakeCoverage(line_coverage_rate=0.5)
        for mutant in mutants:
            mutant.evaluated_at = datetime.now()

        killed_count = sum(1 for mutant in mutants if not mutant.survived)
        survived_count = sum(1 for mutant in mutants if mutant.survived)
        return {
            "evaluated": len(mutants),
            "killed": killed_count,
            "survived": survived_count,
            "status": "completed",
        }


def _build_settings(tmp_dir: str) -> Settings:
    settings = Settings(llm=LLMConfig(api_key="test-key"))
    settings.set_runtime_roots(
        state=Path(tmp_dir) / "state",
        output=Path(tmp_dir) / "output",
        sandbox=Path(tmp_dir) / "sandbox",
    )
    return settings


def _create_isolated_project(root: Path) -> Path:
    project_path = root / "isolated-project"
    (project_path / "src" / "main" / "java" / "pkg").mkdir(parents=True, exist_ok=True)
    (project_path / "src" / "test" / "java").mkdir(parents=True, exist_ok=True)
    _ = (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    _ = (project_path / "src" / "main" / "java" / "pkg" / "Demo.java").write_text(
        "class Demo {}\n",
        encoding="utf-8",
    )
    return project_path


def _write_final_arm_test(context: StudyArmContext, suffix: str) -> Path:
    test_file = (
        context.workspace_path / "src" / "test" / "java" / "pkg" / f"{context.arm}{suffix}Test.java"
    )
    test_file.parent.mkdir(parents=True, exist_ok=True)
    _ = test_file.write_text(f"class {context.arm}{suffix}Test {{}}\n", encoding="utf-8")
    return test_file


def _build_pit_mutations_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<mutations>
  <mutation detected=\"false\" status=\"SURVIVED\" numberOfTestsRun=\"0\">
    <sourceFile>Calculator.java</sourceFile>
    <mutatedClass>com.example.Calculator</mutatedClass>
    <mutatedMethod>add</mutatedMethod>
    <methodDescription>(II)I</methodDescription>
    <lineNumber>42</lineNumber>
    <mutator>org.pitest.mutationtest.engine.gregor.mutators.MathMutator</mutator>
    <killingTest />
  </mutation>
  <mutation detected=\"true\" status=\"KILLED\" numberOfTestsRun=\"1\">
    <sourceFile>Calculator.java</sourceFile>
    <mutatedClass>com.example.Calculator</mutatedClass>
    <mutatedMethod>add</mutatedMethod>
    <methodDescription>(II)I</methodDescription>
    <lineNumber>43</lineNumber>
    <mutator>org.pitest.mutationtest.engine.gregor.mutators.ConditionalsBoundaryMutator</mutator>
    <killingTest>com.example.CalculatorTest.testAdd(com.example.CalculatorTest)</killingTest>
  </mutation>
  <mutation detected=\"false\" status=\"NO_COVERAGE\" numberOfTestsRun=\"0\">
    <sourceFile>Calculator.java</sourceFile>
    <mutatedClass>com.example.Calculator</mutatedClass>
    <mutatedMethod>add</mutatedMethod>
    <methodDescription>(II)I</methodDescription>
    <lineNumber>44</lineNumber>
    <mutator>org.pitest.mutationtest.engine.gregor.mutators.NegateConditionalsMutator</mutator>
    <killingTest />
  </mutation>
  <mutation detected=\"false\" status=\"SURVIVED\" numberOfTestsRun=\"0\">
    <sourceFile>Calculator.java</sourceFile>
    <mutatedClass>com.example.Calculator</mutatedClass>
    <mutatedMethod>subtract</mutatedMethod>
    <methodDescription>(II)I</methodDescription>
    <lineNumber>60</lineNumber>
    <mutator>org.pitest.mutationtest.engine.gregor.mutators.MathMutator</mutator>
    <killingTest />
  </mutation>
</mutations>
"""


def _build_post_mutant(
    method: Mapping[str, object], mutant_suffix: str, mutator: str, status: str
) -> Mutant:
    target_id = str(method["target_id"])
    class_name = str(method["class_name"])
    method_name = str(method["method_name"])
    method_signature = str(method["method_signature"])
    return Mutant(
        id=f"{target_id}-{mutant_suffix}",
        class_name=class_name,
        method_name=method_name,
        method_signature=method_signature,
        patch=MutationPatch(
            file_path=f"src/main/java/{class_name.replace('.', '/')}.java",
            line_start=1,
            line_end=1,
            original_code="",
            mutated_code=f"// PIT operator: {mutator}\n// mutator: {mutator}",
            mutator=mutator,
            operator=mutator,
        ),
        status="valid",
        survived=(status == "SURVIVED"),
        evaluated_at=datetime.now(),
    )


def _write_study_manifest(root: Path, methods: Sequence[Mapping[str, object]]) -> Path:
    manifest_path = root / "sampled_methods.json"
    _ = manifest_path.write_text(
        json.dumps(methods, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


class StudyRunnerTest(unittest.TestCase):
    def test_study_baseline_state_records_failed_targets(self) -> None:
        state = _StudyBaselineState(
            {
                "class_name": "com.example.Calculator",
                "method_name": "add",
                "method_signature": "int add(int, int)",
            },
            iteration=2,
        )

        state.add_failed_target(
            "com.example.Calculator",
            "add",
            "compile failed",
            "int add(int, int)",
        )

        self.assertEqual(len(state.failed_targets), 1)
        self.assertEqual(
            state.failed_targets[0]["target"],
            build_method_key("com.example.Calculator", "add", "int add(int, int)"),
        )
        self.assertEqual(state.failed_targets[0]["reason"], "compile failed")
        self.assertEqual(state.failed_targets[0]["iteration"], 2)

    def test_runner_exports_expected_artifacts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings = _build_settings(tmp_dir)
            project_path = _create_isolated_project(root)
            output_root = root / "study-output"

            methods = [
                {
                    "target_id": build_method_key(
                        "com.example.Calculator",
                        "add",
                        "int add(int, int)",
                    ),
                    "class_name": "com.example.Calculator",
                    "method_name": "add",
                    "method_signature": "int add(int, int)",
                    "order": 0,
                },
                {
                    "target_id": build_method_key(
                        "com.example.Calculator",
                        "subtract",
                        "int subtract(int, int)",
                    ),
                    "class_name": "com.example.Calculator",
                    "method_name": "subtract",
                    "method_signature": "int subtract(int, int)",
                    "order": 1,
                },
            ]
            manifest_path = _write_study_manifest(root, methods)

            db = _FakeDatabase()
            sandbox_manager = SandboxManager(str(root / "sandbox"))
            tools = _FakeTools(db, sandbox_manager)

            def fake_pit_runner(workspace: str) -> dict[str, object]:
                report_path = Path(workspace) / "target" / "pit-reports"
                report_path.mkdir(parents=True, exist_ok=True)
                _ = (report_path / "mutations.xml").write_text(
                    _build_pit_mutations_xml(),
                    encoding="utf-8",
                )
                return {"success": True}

            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(output_root),
                tools=tools,
                database=db,
                sandbox_manager=sandbox_manager,
                settings=settings,
                pit_runner=fake_pit_runner,
            )

            post_results = {
                (methods[0]["target_id"], "M0"): {
                    "line_coverage_rate": 0.8,
                    "mutants": [
                        _build_post_mutant(methods[0], "killed", "MathMutator", "KILLED"),
                        _build_post_mutant(
                            methods[0], "survived", "NegateConditionalsMutator", "KILLED"
                        ),
                    ],
                },
                (methods[0]["target_id"], "M2"): {
                    "line_coverage_rate": 0.75,
                    "mutants": [
                        _build_post_mutant(methods[0], "killed", "MathMutator", "KILLED"),
                        _build_post_mutant(
                            methods[0], "survived", "NegateConditionalsMutator", "SURVIVED"
                        ),
                    ],
                },
                (methods[0]["target_id"], "M3"): {
                    "line_coverage_rate": 0.9,
                    "mutants": [
                        _build_post_mutant(methods[0], "killed", "MathMutator", "KILLED"),
                        _build_post_mutant(
                            methods[0], "survived", "NegateConditionalsMutator", "KILLED"
                        ),
                        _build_post_mutant(methods[0], "extra", "VoidMethodCallMutator", "KILLED"),
                    ],
                },
                (methods[1]["target_id"], "M0"): {
                    "line_coverage_rate": 0.7,
                    "mutants": [
                        _build_post_mutant(methods[1], "killed", "MathMutator", "KILLED"),
                        _build_post_mutant(
                            methods[1], "survived", "NegateConditionalsMutator", "SURVIVED"
                        ),
                    ],
                },
                (methods[1]["target_id"], "M2"): {
                    "line_coverage_rate": 0.72,
                    "mutants": [
                        _build_post_mutant(methods[1], "killed", "MathMutator", "KILLED"),
                        _build_post_mutant(
                            methods[1], "survived", "NegateConditionalsMutator", "KILLED"
                        ),
                    ],
                },
                (methods[1]["target_id"], "M3"): {
                    "line_coverage_rate": 0.74,
                    "mutants": [
                        _build_post_mutant(methods[1], "killed", "MathMutator", "KILLED"),
                        _build_post_mutant(
                            methods[1], "survived", "NegateConditionalsMutator", "KILLED"
                        ),
                    ],
                },
            }

            def execute_arm(
                context: StudyArmContext,
                _method: object,
                _guidance: object,
                _knowledge_base: object,
            ) -> None:
                _ = _write_final_arm_test(context, "Study")

            def post_evaluator(context: StudyArmContext, method: object) -> dict[str, object]:
                target_id = getattr(method, "target_id")
                return dict(post_results[(target_id, context.arm)])

            artifacts = runner.run_study(
                manifest_path,
                arm_executor=execute_arm,
                post_evaluator=post_evaluator,
            )

            self.assertTrue(artifacts.summary_path.exists())
            self.assertTrue(artifacts.per_method_path.exists())
            self.assertTrue(artifacts.per_mutant_path.exists())
            self.assertTrue(artifacts.sampled_methods_path.exists())

            summary = json.loads(artifacts.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["arms"], ["M0", "M2", "M3"])
            self.assertEqual(summary["method_count"], 2)
            self.assertEqual(summary["successful_method_count"], 2)
            self.assertEqual(set(summary["project_averages"].keys()), {"M0", "M2", "M3"})

            with artifacts.per_method_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 6)
            self.assertTrue(
                {
                    "target_id",
                    "arm",
                    "class_name",
                    "method_name",
                    "method_signature",
                    "pre_line_coverage",
                    "post_line_coverage",
                    "pre_killed",
                    "post_killed",
                    "fixed_mutant_count",
                    "delta_mutation_score",
                    "delta_coverage",
                    "final_kill_rate",
                    "effective_operator_ratio",
                }.issubset(rows[0].keys())
            )

            first_m0_row = next(
                row
                for row in rows
                if row["target_id"] == methods[0]["target_id"] and row["arm"] == "M0"
            )
            self.assertEqual(first_m0_row["pre_killed"], "1")
            self.assertEqual(first_m0_row["post_killed"], "2")
            self.assertEqual(first_m0_row["fixed_mutant_count"], "2")
            self.assertEqual(first_m0_row["delta_mutation_score"], "0.5")
            self.assertEqual(first_m0_row["delta_coverage"], "0.30000000000000004")
            self.assertEqual(first_m0_row["effective_operator_ratio"], "1.0")
            first_target_id = str(methods[0]["target_id"])
            second_target_id = str(methods[1]["target_id"])
            self.assertEqual(tools.generate_mutant_calls[first_target_id], 1)
            self.assertEqual(tools.generate_mutant_calls[second_target_id], 1)

            per_mutant_lines = [
                json.loads(line)
                for line in artifacts.per_mutant_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(per_mutant_lines)
            extra_mutant = next(
                item
                for item in per_mutant_lines
                if item["mutant_id"] == f"{methods[0]['target_id']}-extra"
            )
            self.assertFalse(extra_mutant["counts_in_fixed_denominator"])
            first_killed_mutant = next(
                item
                for item in per_mutant_lines
                if item["mutant_id"] == f"{methods[0]['target_id']}-killed" and item["arm"] == "M0"
            )
            self.assertEqual(first_killed_mutant["mutator"], "MathMutator")

            exported_manifest = json.loads(
                artifacts.sampled_methods_path.read_text(encoding="utf-8")
            )
            self.assertEqual(exported_manifest, methods)

    def test_run_pit_mutation_coverage_uses_target_java_environment(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings = _build_settings(tmp_dir)

            runtime_home = root / "runtime-java"
            runtime_bin = runtime_home / "bin"
            runtime_bin.mkdir(parents=True)
            (runtime_bin / "java").write_text("", encoding="utf-8")

            target_home = root / "target-java"
            target_bin = target_home / "bin"
            target_bin.mkdir(parents=True)
            (target_bin / "java").write_text("", encoding="utf-8")
            (target_bin / "javac").write_text("", encoding="utf-8")

            maven_home = root / "maven-home"
            maven_bin = maven_home / "bin"
            maven_bin.mkdir(parents=True)
            (maven_bin / "mvn").write_text("", encoding="utf-8")

            settings.execution.runtime_java_home = str(runtime_home)
            settings.execution.target_java_home = str(target_home)
            settings.execution.maven_home = str(maven_home)

            project_path = _create_isolated_project(root)
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(root / "artifacts"),
                settings=settings,
            )

            with patch("comet.web.study_runner.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "pit ok"
                mock_run.return_value.stderr = ""

                result = runner._run_pit_mutation_coverage(str(project_path))

            self.assertTrue(result["success"])
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            self.assertEqual(
                call_args.args[0][0],
                str((maven_bin / "mvn").resolve()),
            )
            self.assertEqual(
                call_args.kwargs["env"]["JAVA_HOME"],
                str(target_home.resolve()),
            )
            self.assertTrue(
                call_args.kwargs["env"]["PATH"].startswith(str(target_bin.resolve())),
            )

    def test_runner_exports_partial_results_with_failures(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings = _build_settings(tmp_dir)
            project_path = _create_isolated_project(root)
            output_root = root / "study-output"

            failing_target = build_method_key("com.example.Calculator", "add", "int add(int, int)")
            passing_target = build_method_key(
                "com.example.Calculator",
                "subtract",
                "int subtract(int, int)",
            )
            methods = [
                {
                    "target_id": failing_target,
                    "class_name": "com.example.Calculator",
                    "method_name": "add",
                    "method_signature": "int add(int, int)",
                    "order": 0,
                },
                {
                    "target_id": passing_target,
                    "class_name": "com.example.Calculator",
                    "method_name": "subtract",
                    "method_signature": "int subtract(int, int)",
                    "order": 1,
                },
            ]
            manifest_path = _write_study_manifest(root, methods)

            db = _FakeDatabase()
            sandbox_manager = SandboxManager(str(root / "sandbox"))
            tools = _FakeTools(db, sandbox_manager, failing_targets={failing_target})

            def fake_pit_runner(workspace: str) -> dict[str, object]:
                report_path = Path(workspace) / "target" / "pit-reports"
                report_path.mkdir(parents=True, exist_ok=True)
                _ = (report_path / "mutations.xml").write_text(
                    _build_pit_mutations_xml(),
                    encoding="utf-8",
                )
                return {"success": True}

            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(output_root),
                tools=tools,
                database=db,
                sandbox_manager=sandbox_manager,
                settings=settings,
                pit_runner=fake_pit_runner,
            )

            post_results = {
                (passing_target, "M0"): {
                    "line_coverage_rate": 0.68,
                    "mutants": [
                        _build_post_mutant(methods[1], "killed", "MathMutator", "KILLED"),
                        _build_post_mutant(
                            methods[1], "survived", "NegateConditionalsMutator", "SURVIVED"
                        ),
                    ],
                },
                (passing_target, "M3"): {
                    "line_coverage_rate": 0.82,
                    "mutants": [
                        _build_post_mutant(methods[1], "killed", "MathMutator", "KILLED"),
                        _build_post_mutant(
                            methods[1], "survived", "NegateConditionalsMutator", "KILLED"
                        ),
                    ],
                },
            }

            def execute_arm(
                context: StudyArmContext,
                method: object,
                _guidance: object,
                _knowledge_base: object,
            ) -> None:
                _ = _write_final_arm_test(context, "StudyFailure")
                if getattr(method, "target_id") == passing_target and context.arm == "M2":
                    raise RuntimeError("M2 arm failed")

            def post_evaluator(context: StudyArmContext, method: object) -> dict[str, object]:
                target_id = getattr(method, "target_id")
                return dict(post_results[(target_id, context.arm)])

            artifacts = runner.run_study(
                manifest_path,
                arm_executor=execute_arm,
                post_evaluator=post_evaluator,
            )

            summary = json.loads(artifacts.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["arms"], ["M0", "M2", "M3"])
            self.assertEqual(summary["failed_method_count"], 1)
            self.assertEqual(summary["partial_failure_method_count"], 1)
            self.assertEqual(summary["successful_method_count"], 0)
            self.assertEqual(summary["failed_arm_count"], 1)
            self.assertEqual(summary["skipped_arm_count"], 3)
            self.assertEqual(summary["successful_arm_count"], 2)

            method_map = {item["target_id"]: item for item in summary["methods"]}
            self.assertEqual(method_map[failing_target]["baseline_status"], "failed")
            self.assertEqual(method_map[failing_target]["status"], "failed")
            self.assertEqual(method_map[passing_target]["status"], "partial_failed")
            self.assertEqual(method_map[passing_target]["arm_statuses"]["M2"], "failed")
            self.assertEqual(method_map[passing_target]["arm_errors"]["M2"], "M2 arm failed")

            with artifacts.per_method_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["arm"] for row in rows}, {"M0", "M3"})
            self.assertTrue(all(row["target_id"] == passing_target for row in rows))

            per_mutant_lines = [
                json.loads(line)
                for line in artifacts.per_mutant_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(per_mutant_lines)
            self.assertTrue(all(line["target_id"] == passing_target for line in per_mutant_lines))

            self.assertTrue(artifacts.sampled_methods_path.exists())

    def test_m0_uses_surviving_pit_mutants_as_guidance(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = _create_isolated_project(root)
            artifacts_root = root / "artifacts"

            method_signature = "int add(int, int)"
            method = {
                "target_id": build_method_key("com.example.Calculator", "add", method_signature),
                "class_name": "com.example.Calculator",
                "method_name": "add",
                "method_signature": method_signature,
            }

            def fake_pit_runner(workspace: str) -> dict[str, object]:
                report_path = Path(workspace) / "target" / "pit-reports"
                report_path.mkdir(parents=True, exist_ok=True)
                _ = (report_path / "mutations.xml").write_text(
                    _build_pit_mutations_xml(),
                    encoding="utf-8",
                )
                return {"success": True}

            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(artifacts_root),
                settings=_build_settings(tmp_dir),
                pit_runner=fake_pit_runner,
            )

            guidance = runner.build_m0_pit_guidance_from_baseline(method, str(project_path))

            self.assertEqual(len(guidance), 1)
            survived = guidance[0]
            self.assertEqual(survived["status"], "SURVIVED")
            self.assertEqual(survived["operator"], "MathMutator")
            patch = survived["patch"]
            self.assertIsInstance(patch, dict)
            if not isinstance(patch, dict):
                raise AssertionError("patch 必须是字典")
            self.assertIn("mutated_code", patch)
            self.assertIn("PIT operator: MathMutator", str(patch["mutated_code"]))

    def test_guidance_metadata_is_preserved_on_real_mutant(self) -> None:
        method = FrozenStudyMethod(
            target_id="com.example.Calculator#add#int add(int, int)",
            class_name="com.example.Calculator",
            method_name="add",
            method_signature="int add(int, int)",
        )
        guidance = [
            {
                "id": "pit-1",
                "mutator": "org.pitest.mutationtest.engine.gregor.mutators.MathMutator",
                "operator": "MathMutator",
                "patch": {
                    "file_path": "com/example/Calculator.java",
                    "line_start": 42,
                    "line_end": 42,
                    "original_code": "",
                    "mutated_code": "// PIT operator: MathMutator",
                },
            }
        ]

        mutants = _build_guidance_mutants(method, guidance)
        self.assertEqual(len(mutants), 1)
        self.assertEqual(
            mutants[0].patch.mutator,
            "org.pitest.mutationtest.engine.gregor.mutators.MathMutator",
        )
        self.assertEqual(mutants[0].patch.operator, "MathMutator")

        runner = StudyRunner(workspace_project_path=".", artifacts_root=".")
        snapshot = runner._normalize_mutant_snapshot(mutants[0])
        self.assertEqual(
            snapshot.mutator,
            "org.pitest.mutationtest.engine.gregor.mutators.MathMutator",
        )

    def test_m0_reports_missing_pit_xml(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings = _build_settings(tmp_dir)
            project_path = _create_isolated_project(root)
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(settings.resolve_output_root() / "artifacts"),
                settings=settings,
                pit_runner=lambda _workspace: {"success": True},
            )

            method_signature = "int add(int, int)"
            target_id = build_method_key("com.example.Calculator", "add", method_signature)
            method = {
                "target_id": target_id,
                "class_name": "com.example.Calculator",
                "method_name": "add",
                "method_signature": method_signature,
            }

            def execute_arm(context: StudyArmContext) -> str:
                if context.arm == "M0":
                    _ = runner.build_m0_pit_guidance_from_baseline(
                        method, str(context.workspace_path)
                    )
                    return "M0-ok"
                return f"{context.arm}-ok"

            results = runner.run_target_arms(target_id, execute_arm)

            self.assertFalse(results["M0"].succeeded)
            self.assertTrue(results["M2"].succeeded)
            self.assertTrue(results["M3"].succeeded)
            self.assertIn("M0 PIT 报告缺失", str(results["M0"].error))

    def test_m2_disables_rag_and_m3_enables_rag(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings = _build_settings(tmp_dir)
            settings.knowledge.enabled = False
            project_path = _create_isolated_project(root)

            method_signature = "public int run()"
            target_id = build_method_key("Alpha", "run", method_signature)
            db = _FakeDatabase()
            sandbox_manager = SandboxManager(str(root / "sandbox"))
            tools = _FakeTools(db, sandbox_manager)
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(settings.resolve_output_root() / "artifacts"),
                tools=tools,
                database=db,
                sandbox_manager=sandbox_manager,
                settings=settings,
            )

            rag_enabled: dict[str, bool] = {}
            guidance_ids: dict[str, tuple[str, ...]] = {}

            def execute_arm(
                context: StudyArmContext, knowledge_base: Any, guidance: tuple[Any, ...]
            ) -> str:
                rag_enabled[context.arm] = isinstance(knowledge_base, RAGKnowledgeBase)
                guidance_ids[context.arm] = tuple(str(mutant.id) for mutant in guidance)
                _ = _write_final_arm_test(context, "Guidance")
                return context.arm

            results = runner.run_guided_m2_m3_arms(
                {
                    "target_id": target_id,
                    "class_name": "Alpha",
                    "method_name": "run",
                    "method_signature": method_signature,
                },
                execute_arm,
            )

            self.assertEqual(tuple(results.keys()), ("M2", "M3"))
            self.assertTrue(results["M2"].succeeded)
            self.assertTrue(results["M3"].succeeded)
            self.assertFalse(rag_enabled["M2"])
            self.assertTrue(rag_enabled["M3"])
            self.assertEqual(tools.generate_calls[target_id], 1)
            self.assertEqual(tools.evaluate_calls[target_id], 1)
            self.assertEqual(guidance_ids["M2"], guidance_ids["M3"])
            self.assertEqual(guidance_ids["M2"], (f"{target_id}-survived",))

    def test_semantic_mutation_failure_is_scoped(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings = _build_settings(tmp_dir)
            project_path = _create_isolated_project(root)

            method_signature = "public int run()"
            target_id = build_method_key("Alpha", "run", method_signature)
            db = _FakeDatabase()
            sandbox_manager = SandboxManager(str(root / "sandbox"))
            tools = _FakeTools(db, sandbox_manager)
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(settings.resolve_output_root() / "artifacts"),
                tools=tools,
                database=db,
                sandbox_manager=sandbox_manager,
                settings=settings,
            )

            guidance_sizes: dict[str, int] = {}

            def execute_arm(
                context: StudyArmContext, _knowledge_base: Any, guidance: tuple[Any, ...]
            ) -> str:
                guidance_sizes[context.arm] = len(guidance)
                if context.arm == "M2":
                    raise RuntimeError("semantic mutation failed")
                _ = _write_final_arm_test(context, "Semantic")
                return f"{context.arm}-ok"

            results = runner.run_guided_m2_m3_arms(
                {
                    "target_id": target_id,
                    "class_name": "Alpha",
                    "method_name": "run",
                    "method_signature": method_signature,
                },
                execute_arm,
            )

            self.assertFalse(results["M2"].succeeded)
            self.assertTrue(results["M3"].succeeded)
            self.assertIsInstance(results["M2"].error, RuntimeError)
            self.assertEqual(str(results["M2"].error), "semantic mutation failed")
            self.assertEqual(results["M3"].value, "M3-ok")
            self.assertEqual(guidance_sizes["M2"], 1)
            self.assertEqual(guidance_sizes["M3"], 1)

            m3_archived_test = (
                settings.resolve_output_root()
                / "artifacts"
                / target_id
                / "M3"
                / "pkg"
                / "M3SemanticTest.java"
            )
            self.assertTrue(m3_archived_test.exists())

    def test_zero_test_project_uses_shared_baseline_once(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            artifacts_root = root / "artifacts"
            project_path.mkdir(parents=True, exist_ok=True)
            _ = (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            db = _FakeDatabase()
            sandbox_manager = SandboxManager(str(root / "sandbox"))
            tools = _FakeTools(db, sandbox_manager)
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(artifacts_root),
                tools=tools,
                database=db,
                sandbox_manager=sandbox_manager,
            )

            method_signature = "public int run()"
            target_id = build_method_key("Alpha", "run", method_signature)
            method = {
                "target_id": target_id,
                "class_name": "Alpha",
                "method_name": "run",
                "method_signature": method_signature,
            }

            first = runner.ensure_shared_baseline(method)
            reused_results = [runner.ensure_shared_baseline(method) for _ in range(3)]

            self.assertTrue(first.success)
            self.assertTrue(all(result is first for result in reused_results))
            self.assertEqual(tools.generate_calls[target_id], 1)
            self.assertEqual(tools.generate_mutant_calls[target_id], 1)
            self.assertEqual(tools.evaluate_calls[target_id], 1)
            self.assertEqual(first.metrics.pre_test_count, 1)
            self.assertEqual(first.metrics.pre_killed, 1)
            self.assertEqual(first.metrics.baseline_total_mutants, 2)
            self.assertAlmostEqual(first.metrics.pre_line_coverage, 0.5)
            self.assertEqual(first.archive_dirs[BASELINE_ARCHIVE_DIR], f"{target_id}/baseline")

            baseline_files = [Path(path) for path in first.metrics.archived_test_files]
            self.assertEqual(len(baseline_files), 1)
            self.assertTrue(baseline_files[0].is_file())
            self.assertEqual(
                baseline_files[0].parent, artifacts_root / target_id / BASELINE_ARCHIVE_DIR / "pkg"
            )

            runner.cleanup_shared_baselines()

    def test_existing_project_tests_are_used_as_baseline(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            artifacts_root = root / "artifacts"
            test_root = project_path / "src" / "test" / "java" / "pkg"
            test_root.mkdir(parents=True, exist_ok=True)
            _ = (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            _ = (test_root / "ExistingBaselineTest.java").write_text(
                "package pkg;\n"
                "import org.junit.jupiter.api.Test;\n"
                "class ExistingBaselineTest {\n"
                "    @Test\n"
                "    void usesProjectSeed() {}\n"
                "}\n",
                encoding="utf-8",
            )

            db = _FakeDatabase()
            sandbox_manager = SandboxManager(str(root / "sandbox"))
            tools = _FakeTools(db, sandbox_manager)
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(artifacts_root),
                tools=tools,
                database=db,
                sandbox_manager=sandbox_manager,
            )

            method_signature = "public int run()"
            target_id = build_method_key("Alpha", "run", method_signature)
            result = runner.ensure_shared_baseline(
                {
                    "target_id": target_id,
                    "class_name": "Alpha",
                    "method_name": "run",
                    "method_signature": method_signature,
                }
            )

            self.assertTrue(result.success)
            self.assertEqual(tools.generate_calls.get(target_id, 0), 0)
            self.assertEqual(tools.generate_mutant_calls[target_id], 1)
            self.assertEqual(tools.evaluate_calls[target_id], 1)
            self.assertEqual(result.metrics.pre_test_count, 1)

            baseline_files = [Path(path) for path in result.metrics.archived_test_files]
            self.assertEqual(len(baseline_files), 1)
            self.assertTrue(baseline_files[0].is_file())
            self.assertEqual(baseline_files[0].name, "ExistingBaselineTest.java")

            imported_tests = db.get_tests_by_target_method("Alpha", "run", method_signature)
            self.assertEqual(len(imported_tests), 1)
            self.assertEqual(imported_tests[0].class_name, "ExistingBaselineTest")

            runner.cleanup_shared_baselines()

    def test_baseline_failure_is_scoped_to_method(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            artifacts_root = root / "artifacts"
            project_path.mkdir(parents=True, exist_ok=True)
            _ = (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            failing_signature = "public int fail()"
            passing_signature = "public int pass()"
            failing_target = build_method_key("Alpha", "fail", failing_signature)
            passing_target = build_method_key("Beta", "pass", passing_signature)

            db = _FakeDatabase()
            sandbox_manager = SandboxManager(str(root / "sandbox"))
            tools = _FakeTools(db, sandbox_manager, failing_targets={failing_target})
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(artifacts_root),
                tools=tools,
                database=db,
                sandbox_manager=sandbox_manager,
            )

            results = runner.bootstrap_shared_baselines(
                [
                    {
                        "target_id": failing_target,
                        "class_name": "Alpha",
                        "method_name": "fail",
                        "method_signature": failing_signature,
                    },
                    {
                        "target_id": passing_target,
                        "class_name": "Beta",
                        "method_name": "pass",
                        "method_signature": passing_signature,
                    },
                ]
            )

            self.assertFalse(results[failing_target].success)
            self.assertEqual(results[failing_target].status, "failed")
            self.assertIn("baseline bootstrap failed", results[failing_target].error or "")
            self.assertTrue(results[passing_target].success)
            self.assertEqual(tools.generate_calls[failing_target], 1)
            self.assertEqual(tools.generate_calls[passing_target], 1)
            self.assertEqual(tools.generate_mutant_calls.get(failing_target, 0), 0)
            self.assertEqual(tools.generate_mutant_calls[passing_target], 1)
            self.assertEqual(tools.evaluate_calls.get(failing_target, 0), 0)
            self.assertEqual(tools.evaluate_calls[passing_target], 1)

            passing_files = [
                Path(path) for path in results[passing_target].metrics.archived_test_files
            ]
            self.assertEqual(len(passing_files), 1)
            self.assertTrue(passing_files[0].exists())
            self.assertFalse(
                (artifacts_root / failing_target / BASELINE_ARCHIVE_DIR / "pkg").exists()
            )

            runner.cleanup_shared_baselines()

    def test_baseline_reports_no_mutants_without_running_evaluation(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            artifacts_root = root / "artifacts"
            project_path.mkdir(parents=True, exist_ok=True)
            _ = (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            method_signature = "public int zero()"
            target_id = build_method_key("Alpha", "zero", method_signature)

            db = _FakeDatabase()
            sandbox_manager = SandboxManager(str(root / "sandbox"))
            tools = _FakeTools(db, sandbox_manager, no_mutant_targets={target_id})
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(artifacts_root),
                tools=tools,
                database=db,
                sandbox_manager=sandbox_manager,
            )

            result = runner.ensure_shared_baseline(
                {
                    "target_id": target_id,
                    "class_name": "Alpha",
                    "method_name": "zero",
                    "method_signature": method_signature,
                }
            )

            self.assertFalse(result.success)
            self.assertEqual(result.status, "failed")
            self.assertIn("未生成任何变异体", result.error or "")
            self.assertEqual(tools.generate_calls[target_id], 1)
            self.assertEqual(tools.generate_mutant_calls[target_id], 1)
            self.assertEqual(tools.evaluate_calls.get(target_id, 0), 0)
            self.assertEqual(result.metrics.baseline_total_mutants, 0)

    def test_arms_use_isolated_state_roots(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings = _build_settings(tmp_dir)
            project_path = _create_isolated_project(root)
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(settings.resolve_output_root() / "artifacts"),
                settings=settings,
            )
            target_id = "pkg.Demo.run#abc123"

            def execute_arm(context: StudyArmContext) -> dict[str, str]:
                _ = context.paths.database_path.write_text(context.arm, encoding="utf-8")
                _ = context.paths.knowledge_database_path.write_text(context.arm, encoding="utf-8")
                context.paths.vector_store_root.mkdir(parents=True, exist_ok=True)
                _ = (context.paths.vector_store_root / "marker.txt").write_text(
                    context.arm,
                    encoding="utf-8",
                )
                _ = (context.paths.output_root / "coverage.txt").write_text(
                    context.arm,
                    encoding="utf-8",
                )
                (context.workspace_path / "target" / "pit-reports").mkdir(
                    parents=True,
                    exist_ok=True,
                )
                _ = (
                    context.workspace_path / "target" / "pit-reports" / "mutations.xml"
                ).write_text(
                    f'<mutations arm="{context.arm}" />\n',
                    encoding="utf-8",
                )
                final_test = _write_final_arm_test(context, "Isolation")
                return {
                    "state": str(context.paths.state_root),
                    "output": str(context.paths.output_root),
                    "sandbox": str(context.paths.sandbox_root),
                    "workspace": str(context.workspace_path),
                    "database": str(context.paths.database_path),
                    "knowledge": str(context.paths.knowledge_database_path),
                    "vector": str(context.paths.vector_store_root),
                    "test": str(final_test),
                }

            results = runner.run_target_arms(target_id, execute_arm)

            self.assertEqual(tuple(results.keys()), ("M0", "M2", "M3"))
            self.assertTrue(all(result.succeeded for result in results.values()))
            self.assertEqual(
                len({result.context.paths.state_root for result in results.values()}), 3
            )
            self.assertEqual(
                len({result.context.paths.output_root for result in results.values()}), 3
            )
            self.assertEqual(
                len({result.context.paths.sandbox_root for result in results.values()}), 3
            )
            self.assertEqual(len({result.context.workspace_path for result in results.values()}), 3)
            self.assertEqual(
                len({result.context.paths.database_path for result in results.values()}), 3
            )
            self.assertEqual(
                len({result.context.paths.knowledge_database_path for result in results.values()}),
                3,
            )
            self.assertEqual(
                len({result.context.paths.vector_store_root for result in results.values()}),
                3,
            )

            for arm, result in results.items():
                self.assertEqual(
                    result.context.paths.database_path.read_text(encoding="utf-8"), arm
                )
                self.assertEqual(
                    result.context.paths.knowledge_database_path.read_text(encoding="utf-8"),
                    arm,
                )
                self.assertEqual(
                    (result.context.paths.vector_store_root / "marker.txt").read_text(
                        encoding="utf-8"
                    ),
                    arm,
                )
                self.assertEqual(
                    (result.context.paths.output_root / "coverage.txt").read_text(encoding="utf-8"),
                    arm,
                )
                archived_test = (
                    settings.resolve_output_root()
                    / "artifacts"
                    / target_id
                    / arm
                    / "pkg"
                    / f"{arm}IsolationTest.java"
                )
                self.assertTrue(archived_test.exists())
                self.assertEqual(
                    archived_test.read_text(encoding="utf-8"),
                    f"class {arm}IsolationTest {{}}\n",
                )

    def test_one_arm_failure_does_not_corrupt_other_arms(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            settings = _build_settings(tmp_dir)
            project_path = _create_isolated_project(root)
            runner = StudyRunner(
                workspace_project_path=str(project_path),
                artifacts_root=str(settings.resolve_output_root() / "artifacts"),
                settings=settings,
            )
            target_id = "pkg.Demo.fail#def456"

            def execute_arm(context: StudyArmContext) -> str:
                _ = _write_final_arm_test(context, "Failure")
                _ = (context.paths.state_root / "arm-marker.txt").write_text(
                    context.arm,
                    encoding="utf-8",
                )
                if context.arm == "M2":
                    _ = (context.paths.state_root / "failed.txt").write_text(
                        "boom\n",
                        encoding="utf-8",
                    )
                    raise RuntimeError("M2 boom")

                _ = (context.paths.output_root / "result.txt").write_text(
                    f"{context.arm}-ok\n",
                    encoding="utf-8",
                )
                return f"{context.arm}-ok"

            results = runner.run_target_arms(target_id, execute_arm)

            self.assertTrue(results["M0"].succeeded)
            self.assertFalse(results["M2"].succeeded)
            self.assertTrue(results["M3"].succeeded)
            self.assertEqual(results["M0"].value, "M0-ok")
            self.assertIsNone(results["M2"].value)
            self.assertEqual(results["M3"].value, "M3-ok")
            self.assertIsInstance(results["M2"].error, RuntimeError)
            self.assertEqual(str(results["M2"].error), "M2 boom")

            self.assertTrue((results["M2"].context.paths.state_root / "failed.txt").exists())
            self.assertFalse((results["M0"].context.paths.state_root / "failed.txt").exists())
            self.assertFalse((results["M3"].context.paths.state_root / "failed.txt").exists())
            self.assertEqual(
                (results["M0"].context.paths.output_root / "result.txt").read_text(
                    encoding="utf-8"
                ),
                "M0-ok\n",
            )
            self.assertEqual(
                (results["M3"].context.paths.output_root / "result.txt").read_text(
                    encoding="utf-8"
                ),
                "M3-ok\n",
            )

            for arm in ("M0", "M2", "M3"):
                archived_test = (
                    settings.resolve_output_root()
                    / "artifacts"
                    / target_id
                    / arm
                    / "pkg"
                    / f"{arm}FailureTest.java"
                )
                self.assertTrue(archived_test.exists())


if __name__ == "__main__":
    _ = unittest.main()
