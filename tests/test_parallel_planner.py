import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from comet.agent.parallel_planner import ParallelPlannerAgent
from comet.agent.state import ParallelAgentState
from comet.executor.coverage_parser import CoverageParser, MethodCoverage
from comet.llm.client import LLMClient
from comet.store.database import Database
from comet.utils.sandbox import SandboxManager


class FakeCoverageParser(CoverageParser):
    def __init__(self):
        super().__init__()
        self.last_parse_path = ""
        self.last_aggregate_path = ""

    def parse_jacoco_xml_with_lines(self, xml_path: str):
        self.last_parse_path = xml_path
        return [
            MethodCoverage(
                class_name="org.example.Example",
                method_name="doWork",
                method_signature="void doWork()",
                covered_lines=[10, 11],
                missed_lines=[12],
                total_lines=3,
                covered_branches=1,
                missed_branches=1,
                total_branches=2,
                line_coverage_rate=2 / 3,
                branch_coverage_rate=0.5,
                source_filename="Example.java",
            )
        ]

    def aggregate_global_coverage_from_xml(self, xml_path: str):
        self.last_aggregate_path = xml_path
        return {"line_coverage": 0.5, "branch_coverage": 0.25}


class FakeDatabase(Database):
    def __init__(self):
        super().__init__(":memory:")
        self.saved = []

    def save_method_coverage(self, coverage: MethodCoverage, iteration: int) -> None:
        self.saved.append((coverage.class_name, coverage.method_name, iteration))


class FakeTargetDatabase(Database):
    def __init__(self):
        super().__init__(":memory:")

    def get_method_coverage(self, class_name: str, method_name: str, method_signature=None):
        return None


class ParallelPlannerCoverageSyncTest(unittest.TestCase):
    def _make_planner(self, workspace_path: Path) -> tuple[ParallelPlannerAgent, FakeDatabase]:
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.workspace_path = str(workspace_path)
        planner.coverage_parser = FakeCoverageParser()
        fake_db = FakeDatabase()
        planner.db = fake_db
        planner.state = ParallelAgentState()
        planner.state.iteration = 3
        return planner, fake_db

    def test_sync_workspace_coverage_persists_method_coverage_and_updates_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            jacoco_path = workspace / "target" / "site" / "jacoco" / "jacoco.xml"
            jacoco_path.parent.mkdir(parents=True)
            jacoco_path.write_text("<report />", encoding="utf-8")

            planner, fake_db = self._make_planner(workspace)

            synced = planner.sync_workspace_coverage(wait_for_report=False)

            self.assertTrue(synced)
            self.assertEqual(
                fake_db.saved,
                [("org.example.Example", "doWork", 3)],
            )
            self.assertEqual(planner.state.line_coverage, 0.5)
            self.assertEqual(planner.state.branch_coverage, 0.25)

    def test_sync_workspace_coverage_returns_false_when_report_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            planner, fake_db = self._make_planner(Path(tmp_dir))

            synced = planner.sync_workspace_coverage(wait_for_report=False)

            self.assertFalse(synced)
            self.assertEqual(fake_db.saved, [])


class CoverageParserSignatureTests(unittest.TestCase):
    def test_build_method_signature_from_jacoco_descriptor(self):
        parser = CoverageParser()

        signature = parser._build_method_signature("add", "(II)I")

        self.assertEqual(signature, "int add(int, int)")


class FakeLLMCounter:
    def __init__(self, total_calls: int = 0):
        self.total_calls = total_calls

    def get_total_calls(self) -> int:
        return self.total_calls


class ParallelPlannerLLMCallSyncTest(unittest.TestCase):
    def _make_planner(self, base_calls: int, budget: int) -> ParallelPlannerAgent:
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.llm = cast(LLMClient, cast(object, FakeLLMCounter(base_calls)))
        planner._llm_calls_base = base_calls
        planner.state = ParallelAgentState()
        planner.budget = budget
        planner.max_iterations = 100
        planner._interrupted = False
        return planner

    def test_should_stop_syncs_llm_calls_and_respects_budget(self):
        planner = self._make_planner(base_calls=10, budget=3)

        planner.llm.total_calls = 12
        should_stop = planner._should_stop()

        self.assertFalse(should_stop)
        self.assertEqual(planner.state.llm_calls, 2)

        planner.llm.total_calls = 13
        should_stop = planner._should_stop()

        self.assertTrue(should_stop)
        self.assertEqual(planner.state.llm_calls, 3)


class FakeSandboxManager:
    def __init__(self, sandbox_path: str):
        self.sandbox_path = sandbox_path
        self.cleaned = []

    def create_target_sandbox(self, project_path: str, class_name: str, method_name: str) -> str:
        return self.sandbox_path

    def cleanup_sandbox(self, sandbox_id: str) -> None:
        self.cleaned.append(sandbox_id)


class FakeWorkerFuture:
    def __init__(self, result_value=None, error: Exception | None = None):
        self.result_value = result_value
        self.error = error

    def result(self, timeout: int | None = None):
        if self.error is not None:
            raise self.error
        return self.result_value


class ParallelPlannerLoggingTest(unittest.TestCase):
    def test_process_single_target_logs_explicit_timeout_for_test_generation(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.project_path = "/tmp/project"
        fake_sandbox_manager = FakeSandboxManager("/tmp/sandboxes/target-1")
        planner.sandbox_manager = cast(SandboxManager, cast(object, fake_sandbox_manager))
        planner.db = FakeTargetDatabase()

        target = {
            "class_name": "org.example.Example",
            "method_name": "doWork",
            "method_signature": "void doWork()",
            "method_coverage": 0.5,
        }

        futures = iter(
            [
                FakeWorkerFuture(error=TimeoutError()),
                FakeWorkerFuture(result_value={"generated": 1}),
            ]
        )

        with patch(
            "comet.agent.parallel_planner.submit_with_log_context",
            side_effect=lambda *args, **kwargs: next(futures),
        ):
            with self.assertLogs("comet.agent.parallel_planner", level="WARNING") as captured_logs:
                result = planner._process_single_target_impl(
                    target,
                    "org.example.Example",
                    "doWork",
                    "org.example.Example#doWork:void doWork()",
                )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "测试生成失败")
        self.assertIn("target-1", fake_sandbox_manager.cleaned)
        warning_output = "\n".join(captured_logs.output)
        self.assertIn(
            "测试生成超时: org.example.Example#doWork:void doWork() (timeout=180s)", warning_output
        )
        self.assertNotIn("测试生成异常:", warning_output)


if __name__ == "__main__":
    unittest.main()
