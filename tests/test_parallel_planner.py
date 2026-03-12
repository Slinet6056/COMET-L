import tempfile
import unittest
from pathlib import Path
from typing import cast

from comet.agent.parallel_planner import ParallelPlannerAgent
from comet.agent.state import ParallelAgentState
from comet.executor.coverage_parser import CoverageParser, MethodCoverage
from comet.llm.client import LLMClient
from comet.store.database import Database


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


if __name__ == "__main__":
    unittest.main()
