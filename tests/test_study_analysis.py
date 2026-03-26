import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comet.config.settings import LLMConfig, Settings
from comet.executor.coverage_parser import CoverageParser
from comet.utils import SandboxManager
from comet.web.study_analysis import (
    StudyAnalysisError,
    StudyAnalysisTarget,
    StudyReplayAnalyzer,
    analyze_study_results,
)
from comet.web.study_protocol import StudyAnalysisRowSchema


def _write_test_archive(archive_dir: Path, file_name: str, body: str) -> None:
    target = archive_dir / "pkg" / file_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def _write_summary(summary_path: Path) -> None:
    summary_path.write_text(
        json.dumps(
            {
                "methods": [
                    {
                        "target_id": "pkg.Alpha.doWork#123",
                        "class_name": "pkg.Alpha",
                        "method_name": "doWork",
                        "method_signature": "void doWork()",
                        "status": "completed",
                        "baseline_status": "completed",
                        "arm_statuses": {
                            "M0": "completed",
                            "M2": "completed",
                            "M3": "completed",
                        },
                    },
                    {
                        "target_id": "pkg.Beta.skip#456",
                        "class_name": "pkg.Beta",
                        "method_name": "skip",
                        "method_signature": "void skip()",
                        "status": "partial_failed",
                        "baseline_status": "completed",
                        "arm_statuses": {
                            "M0": "completed",
                            "M2": "failed",
                            "M3": "completed",
                        },
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_reports(workspace_path: Path, mutation_coverage: int, test_strength: int) -> None:
    jacoco_dir = workspace_path / "target" / "site" / "jacoco"
    jacoco_dir.mkdir(parents=True, exist_ok=True)
    (jacoco_dir / "jacoco.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<report name="demo">
  <counter type="LINE" missed="2" covered="8"/>
  <counter type="BRANCH" missed="1" covered="3"/>
  <counter type="METHOD" missed="2" covered="6"/>
  <counter type="CLASS" missed="1" covered="2"/>
</report>
""",
        encoding="utf-8",
    )

    pit_dir = workspace_path / "target" / "pit-reports"
    pit_dir.mkdir(parents=True, exist_ok=True)
    (pit_dir / "mutations.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mutations>
  <mutation detected="true" status="KILLED" numberOfTestsRun="1">
    <sourceFile>Alpha.java</sourceFile>
    <mutatedClass>pkg.Alpha</mutatedClass>
    <mutatedMethod>doWork</mutatedMethod>
    <methodDescription>()V</methodDescription>
    <lineNumber>10</lineNumber>
    <mutator>MathMutator</mutator>
    <killingTest>pkg.AlphaTest.testA(pkg.AlphaTest)</killingTest>
    <description>killed</description>
  </mutation>
  <mutation detected="false" status="SURVIVED" numberOfTestsRun="1">
    <sourceFile>Alpha.java</sourceFile>
    <mutatedClass>pkg.Alpha</mutatedClass>
    <mutatedMethod>doWork</mutatedMethod>
    <methodDescription>()V</methodDescription>
    <lineNumber>11</lineNumber>
    <mutator>MathMutator</mutator>
    <killingTest />
    <description>survived</description>
  </mutation>
  <mutation detected="false" status="NO_COVERAGE" numberOfTestsRun="0">
    <sourceFile>Alpha.java</sourceFile>
    <mutatedClass>pkg.Alpha</mutatedClass>
    <mutatedMethod>doWork</mutatedMethod>
    <methodDescription>()V</methodDescription>
    <lineNumber>12</lineNumber>
    <mutator>MathMutator</mutator>
    <killingTest />
    <description>no coverage</description>
  </mutation>
</mutations>
""",
        encoding="utf-8",
    )
    (pit_dir / "index.html").write_text(
        f"""
<html><body>
  <table>
    <tr><td>Mutation Coverage</td><td>{mutation_coverage}%</td></tr>
    <tr><td>Test Strength</td><td>{test_strength}%</td></tr>
  </table>
</body></html>
""",
        encoding="utf-8",
    )


class StudyAnalysisTests(unittest.TestCase):
    def test_aggregate_global_coverage_from_xml_includes_method_and_class_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            xml_path = Path(tmp_dir) / "jacoco.xml"
            xml_path.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<report name="demo">
  <counter type="LINE" missed="1" covered="9"/>
  <counter type="BRANCH" missed="2" covered="6"/>
  <counter type="METHOD" missed="1" covered="4"/>
  <counter type="CLASS" missed="0" covered="2"/>
</report>
""",
                encoding="utf-8",
            )

            metrics = CoverageParser().aggregate_global_coverage_from_xml(str(xml_path))

        self.assertEqual(metrics["total_methods"], 5)
        self.assertEqual(metrics["covered_methods"], 4)
        self.assertAlmostEqual(metrics["method_coverage"], 0.8)
        self.assertEqual(metrics["total_classes"], 2)
        self.assertEqual(metrics["covered_classes"], 2)
        self.assertAlmostEqual(metrics["class_coverage"], 1.0)

    def test_analyze_study_results_replays_all_archives_and_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            test_root = project_path / "src" / "test" / "java" / "pkg"
            test_root.mkdir(parents=True)
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
            (test_root / "OriginalTest.java").write_text("class OriginalTest {}", encoding="utf-8")
            (test_root / "Helper.java").write_text("class Helper {}", encoding="utf-8")

            study_results_path = root / "study-output"
            artifacts_root = study_results_path / "artifacts" / "pkg.Alpha.doWork#123"
            for arm in ("baseline", "M0", "M2", "M3"):
                _write_test_archive(
                    artifacts_root / arm,
                    f"{arm.capitalize()}Test.java",
                    f"package pkg; class {arm.capitalize()}Test {{}}",
                )
            study_results_path.mkdir(parents=True, exist_ok=True)
            _write_summary(study_results_path / "summary.json")

            settings = Settings(llm=LLMConfig(api_key="test-key"))
            settings.set_runtime_roots(
                state=root / "analysis-state",
                output=study_results_path,
                sandbox=root / "analysis-sandbox",
            )
            settings.ensure_directories()

            seen_archives: list[str] = []

            def fake_maven_runner(workspace_path: Path, config: Settings) -> dict[str, object]:
                self.assertIs(config, settings)
                java_files = sorted(
                    path.relative_to(workspace_path / "src" / "test" / "java").as_posix()
                    for path in (workspace_path / "src" / "test" / "java").rglob("*.java")
                )
                self.assertEqual(len(java_files), 2)
                self.assertIn("pkg/Helper.java", java_files)
                current_test_files = [path for path in java_files if path.endswith("Test.java")]
                self.assertEqual(len(current_test_files), 1)
                seen_archives.append(current_test_files[0])
                _write_reports(workspace_path, mutation_coverage=91, test_strength=67)
                return {"success": True}

            output_csv = analyze_study_results(
                project_path=project_path,
                study_results_path=study_results_path,
                output_csv=None,
                settings=settings,
                sandbox_manager=SandboxManager(str(root / "analysis-sandbox-runtime")),
                maven_runner=fake_maven_runner,
            )

            with output_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(output_csv.name, "analysis_metrics.csv")
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["arm"] for row in rows], ["baseline", "M0", "M2", "M3"])
        self.assertEqual(
            seen_archives,
            [
                "pkg/BaselineTest.java",
                "pkg/M0Test.java",
                "pkg/M2Test.java",
                "pkg/M3Test.java",
            ],
        )
        self.assertTrue(all(row["target_id"] == "pkg.Alpha.doWork#123" for row in rows))
        self.assertTrue(all(row["pit_mutation_kill_rate"] == "0.91" for row in rows))
        self.assertTrue(all(row["pit_test_strength"] == "0.67" for row in rows))
        self.assertTrue(all(row["jacoco_method_coverage"] == "0.75" for row in rows))
        self.assertTrue(all(row["jacoco_class_coverage"] == str(2 / 3) for row in rows))

    def test_analyze_study_results_rejects_when_no_fully_completed_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            study_results_path = root / "study-output"
            study_results_path.mkdir()
            (study_results_path / "summary.json").write_text(
                json.dumps(
                    {
                        "methods": [
                            {
                                "target_id": "pkg.Beta.skip#456",
                                "class_name": "pkg.Beta",
                                "method_name": "skip",
                                "method_signature": "void skip()",
                                "status": "failed",
                                "baseline_status": "completed",
                                "arm_statuses": {
                                    "M0": "failed",
                                    "M2": "failed",
                                    "M3": "failed",
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            settings = Settings(llm=LLMConfig(api_key="test-key"))
            settings.set_runtime_roots(
                state=root / "analysis-state",
                output=study_results_path,
                sandbox=root / "analysis-sandbox",
            )
            settings.ensure_directories()

            with self.assertRaisesRegex(StudyAnalysisError, "没有三臂全部成功的目标"):
                _ = analyze_study_results(
                    project_path=project_path,
                    study_results_path=study_results_path,
                    output_csv=None,
                    settings=settings,
                )

    def test_analyze_study_results_uses_parallel_workers_and_keeps_output_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_path = root / "project"
            project_path.mkdir()
            (project_path / "pom.xml").write_text("<project/>", encoding="utf-8")

            study_results_path = root / "study-output"
            study_results_path.mkdir()
            (study_results_path / "summary.json").write_text(
                json.dumps(
                    {
                        "methods": [
                            {
                                "target_id": "pkg.Alpha.doWork#123",
                                "class_name": "pkg.Alpha",
                                "method_name": "doWork",
                                "method_signature": "void doWork()",
                                "status": "completed",
                                "baseline_status": "completed",
                                "arm_statuses": {
                                    "M0": "completed",
                                    "M2": "completed",
                                    "M3": "completed",
                                },
                            },
                            {
                                "target_id": "pkg.Beta.work#456",
                                "class_name": "pkg.Beta",
                                "method_name": "work",
                                "method_signature": "void work()",
                                "status": "completed",
                                "baseline_status": "completed",
                                "arm_statuses": {
                                    "M0": "completed",
                                    "M2": "completed",
                                    "M3": "completed",
                                },
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            settings = Settings(llm=LLMConfig(api_key="test-key"))
            settings.set_runtime_roots(
                state=root / "analysis-state",
                output=study_results_path,
                sandbox=root / "analysis-sandbox",
            )
            settings.ensure_directories()

            analyzer = StudyReplayAnalyzer(settings=settings)
            calls: list[tuple[str, str]] = []

            def build_target(
                target_id: str, class_name: str, method_name: str
            ) -> StudyAnalysisTarget:
                return StudyAnalysisTarget(
                    target_id=target_id,
                    class_name=class_name,
                    method_name=method_name,
                    method_signature=f"void {method_name}()",
                )

            def build_row(target_id: str, arm: str) -> StudyAnalysisRowSchema:
                return StudyAnalysisRowSchema(
                    target_id=target_id,
                    arm=arm,
                    class_name="pkg.Demo",
                    method_name="demo",
                    method_signature="void demo()",
                    test_archive_dir="/tmp/archive",
                )

            def fake_analyze_target_arm(
                *, project_path: Path, study_results_path: Path, target, arm: str
            ):
                calls.append((target.target_id, arm))
                return build_row(target.target_id, arm)

            with patch.object(analyzer, "_analyze_target_arm", side_effect=fake_analyze_target_arm):
                rows = analyzer._collect_rows(
                    tasks=[
                        (build_target("pkg.Alpha.doWork#123", "pkg.Alpha", "doWork"), "baseline"),
                        (build_target("pkg.Alpha.doWork#123", "pkg.Alpha", "doWork"), "M0"),
                        (build_target("pkg.Beta.work#456", "pkg.Beta", "work"), "baseline"),
                        (build_target("pkg.Beta.work#456", "pkg.Beta", "work"), "M0"),
                    ],
                    project_path=project_path,
                    study_results_path=study_results_path,
                    max_workers=2,
                )

        self.assertEqual(len(calls), 4)
        self.assertEqual(
            [(row.target_id, row.arm) for row in rows],
            [
                ("pkg.Alpha.doWork#123", "baseline"),
                ("pkg.Alpha.doWork#123", "M0"),
                ("pkg.Beta.work#456", "baseline"),
                ("pkg.Beta.work#456", "M0"),
            ],
        )


if __name__ == "__main__":
    _ = unittest.main()
