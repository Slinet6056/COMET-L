import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

from comet.agent.parallel_planner import ParallelPlannerAgent
from comet.agent.state import ParallelAgentState, WorkerResult
from comet.agent.target_selector import TargetSelector
from comet.executor.coverage_parser import CoverageParser, MethodCoverage
from comet.executor.java_executor import JavaExecutor
from comet.llm.client import LLMClient
from comet.store.database import Database
from comet.utils.method_keys import build_method_key
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


class FakeExecutor:
    def __init__(self, max_workers: int):
        self.max_workers = max_workers
        self.shutdown_calls = []

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


class FakeWorkerFuture:
    def __init__(
        self,
        result_value=None,
        error: Exception | None = None,
        *,
        done: bool = True,
        fail_on_result: bool = False,
        can_cancel: bool = True,
    ):
        self.result_value = result_value
        self.error = error
        self.done_value = done
        self.fail_on_result = fail_on_result
        self.can_cancel = can_cancel
        self.result_calls = 0
        self.cancel_calls = 0
        self.callbacks = []
        self.timeouts = []

    def result(self, timeout: int | None = None):
        self.result_calls += 1
        self.timeouts.append(timeout)
        if self.fail_on_result:
            raise AssertionError("unexpected result() call")
        if self.error is not None:
            raise self.error
        return self.result_value

    def done(self) -> bool:
        return self.done_value

    def cancel(self) -> bool:
        self.cancel_calls += 1
        if self.done_value or not self.can_cancel:
            return False
        self.done_value = True
        for callback in self.callbacks:
            callback(self)
        return True

    def add_done_callback(self, callback) -> None:
        self.callbacks.append(callback)
        if self.done_value:
            callback(self)

    def finish(self) -> None:
        self.done_value = True
        for callback in list(self.callbacks):
            callback(self)


class ParallelPlannerLoggingTest(unittest.TestCase):
    def test_process_single_target_does_not_create_mutant_future_when_mutation_disabled(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.project_path = "/tmp/project"
        planner.timeout_per_target = 75
        planner.mutation_enabled = False
        planner.sandbox_manager = cast(
            SandboxManager,
            cast(object, FakeSandboxManager("/tmp/sandboxes/target-1")),
        )
        planner.db = FakeTargetDatabase()
        planner.state = ParallelAgentState()

        target = {
            "class_name": "org.example.Example",
            "method_name": "doWork",
            "method_signature": "void doWork()",
            "method_coverage": 0.5,
        }

        test_files = {"org/example/ExampleTest.java": "class ExampleTest {}"}
        test_future = FakeWorkerFuture(result_value={"generated": 1, "test_files": test_files})

        with patch(
            "comet.agent.parallel_planner.submit_with_log_context",
            side_effect=[test_future],
        ) as submit_mock:
            with patch.object(
                planner,
                "_evaluate_in_sandbox",
                side_effect=AssertionError("mutation disabled should not evaluate mutants"),
            ):
                result = planner._process_single_target_impl(
                    target,
                    "org.example.Example",
                    "doWork",
                    "org.example.Example#doWork:void doWork()",
                )

        self.assertEqual(submit_mock.call_count, 1)
        self.assertTrue(result.success)
        self.assertEqual(result.tests_generated, 1)
        self.assertEqual(result.test_files, test_files)
        self.assertIsNone(result.mutants_generated)
        self.assertIsNone(result.mutants_evaluated)
        self.assertIsNone(result.mutants_killed)
        self.assertIsNone(result.local_mutation_score)
        self.assertFalse(result.mutation_enabled)

    def test_process_single_target_skips_waiting_for_mutants_when_blacklisted_midflight(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.project_path = "/tmp/project"
        planner.timeout_per_target = 75
        planner.sandbox_manager = cast(
            SandboxManager,
            cast(object, FakeSandboxManager("/tmp/sandboxes/target-1")),
        )
        planner.db = FakeTargetDatabase()
        planner.state = ParallelAgentState()
        planner.state.failed_targets.append(
            {
                "target": build_method_key(
                    "org.example.Example",
                    "doWork",
                    "void doWork()",
                ),
                "class_name": "org.example.Example",
                "method_name": "doWork",
                "method_signature": "void doWork()",
                "reason": "黑名单测试",
            }
        )

        target = {
            "class_name": "org.example.Example",
            "method_name": "doWork",
            "method_signature": "void doWork()",
            "method_coverage": 0.5,
        }

        test_future = FakeWorkerFuture(result_value={"generated": 1, "test_files": {}})
        mutant_future = FakeWorkerFuture(fail_on_result=True)

        with patch(
            "comet.agent.parallel_planner.submit_with_log_context",
            side_effect=[test_future, mutant_future],
        ):
            result = planner._process_single_target_impl(
                target,
                "org.example.Example",
                "doWork",
                "org.example.Example#doWork:void doWork()",
            )

        self.assertTrue(result.success)
        self.assertEqual(result.mutants_generated, 0)
        self.assertEqual(mutant_future.result_calls, 0)


class ParallelPlannerFrontierAwareStopTest(unittest.TestCase):
    def test_run_does_not_stop_on_no_improvement_when_untried_targets_remain(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.state = ParallelAgentState()
        planner.max_parallel_targets = 4
        planner.max_iterations = 10
        planner.budget = 100
        planner.max_eval_workers = 2
        planner.timeout_per_target = 60
        planner.mutation_enabled = False
        planner._interrupted = False
        planner._llm_calls_base = 0
        planner._sync_global_state = Mock()
        planner._collect_global_coverage = Mock()
        planner._merge_test_files = Mock()
        planner._validate_and_fix_conflicts = Mock()
        planner._log_final_summary = Mock()
        planner._check_excellent_quality = Mock(return_value=False)
        planner._check_improvement = Mock(return_value=False)
        planner._should_stop = Mock(side_effect=[False, False, True])
        planner._select_batch_targets = Mock(
            side_effect=[
                [{"class_name": "A", "method_name": "m1"}],
                [{"class_name": "B", "method_name": "m2"}],
            ]
        )
        planner._process_targets_parallel = Mock(
            return_value=[
                WorkerResult(
                    target_id="A#m1",
                    class_name="A",
                    method_name="m1",
                    success=False,
                    error="生成失败",
                    mutation_enabled=False,
                )
            ]
        )
        planner._has_untried_frontier = Mock(side_effect=[True, True, False, False])

        result = planner.run(stop_on_no_improvement_rounds=1, min_improvement_threshold=0.01)

        self.assertIs(result, planner.state)
        self.assertEqual(planner._select_batch_targets.call_count, 2)
        self.assertEqual(planner._process_targets_parallel.call_count, 2)


class ParallelPlannerExplorationSlotsTest(unittest.TestCase):
    def test_select_batch_targets_reserves_slot_for_unprocessed_target(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.max_parallel_targets = 4
        planner.state = ParallelAgentState()
        planner.target_selector = Mock(spec=TargetSelector)
        planner.target_selector.has_unprocessed_target.return_value = True

        regular_target = {
            "class_name": "Example",
            "method_name": "regular",
            "method_signature": "void regular()",
        }
        unprocessed_target = {
            "class_name": "Example",
            "method_name": "fresh",
            "method_signature": "void fresh()",
        }

        planner.target_selector.select.side_effect = [
            unprocessed_target,
            regular_target,
            {"class_name": None, "method_name": None},
            {"class_name": None, "method_name": None},
        ]

        selected = planner._select_batch_targets()

        self.assertEqual(selected, [unprocessed_target, regular_target])
        first_call = planner.target_selector.select.call_args_list[0]
        second_call = planner.target_selector.select.call_args_list[1]
        self.assertTrue(first_call.kwargs["require_unprocessed"])
        self.assertFalse(second_call.kwargs["require_unprocessed"])

    def test_process_single_target_defers_cleanup_until_running_mutant_future_finishes(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.project_path = "/tmp/project"
        planner.timeout_per_target = 75
        fake_sandbox_manager = FakeSandboxManager("/tmp/sandboxes/target-1")
        planner.sandbox_manager = cast(SandboxManager, cast(object, fake_sandbox_manager))
        planner.db = FakeTargetDatabase()
        planner.state = ParallelAgentState()

        target = {
            "class_name": "org.example.Example",
            "method_name": "doWork",
            "method_signature": "void doWork()",
            "method_coverage": 0.5,
        }

        test_future = FakeWorkerFuture(result_value={"generated": 0})
        mutant_future = FakeWorkerFuture(done=False, can_cancel=False)

        with patch(
            "comet.agent.parallel_planner.submit_with_log_context",
            side_effect=[test_future, mutant_future],
        ):
            result = planner._process_single_target_impl(
                target,
                "org.example.Example",
                "doWork",
                "org.example.Example#doWork:void doWork()",
            )

        self.assertFalse(result.success)
        self.assertEqual(fake_sandbox_manager.cleaned, [])
        self.assertEqual(mutant_future.cancel_calls, 1)

        mutant_future.finish()

        self.assertEqual(fake_sandbox_manager.cleaned, ["target-1"])

    def test_process_single_target_skips_waiting_for_mutants_after_test_failure(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.project_path = "/tmp/project"
        planner.timeout_per_target = 75
        planner.sandbox_manager = cast(
            SandboxManager,
            cast(object, FakeSandboxManager("/tmp/sandboxes/target-1")),
        )
        planner.db = FakeTargetDatabase()
        planner.state = ParallelAgentState()

        target = {
            "class_name": "org.example.Example",
            "method_name": "doWork",
            "method_signature": "void doWork()",
            "method_coverage": 0.5,
        }

        test_future = FakeWorkerFuture(result_value={"generated": 0})
        mutant_future = FakeWorkerFuture(fail_on_result=True)

        with patch(
            "comet.agent.parallel_planner.submit_with_log_context",
            side_effect=[test_future, mutant_future],
        ):
            result = planner._process_single_target_impl(
                target,
                "org.example.Example",
                "doWork",
                "org.example.Example#doWork:void doWork()",
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "测试生成失败")
        self.assertEqual(mutant_future.result_calls, 0)

    def test_process_single_target_logs_explicit_timeout_for_test_generation(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.project_path = "/tmp/project"
        planner.timeout_per_target = 75
        fake_sandbox_manager = FakeSandboxManager("/tmp/sandboxes/target-1")
        planner.sandbox_manager = cast(SandboxManager, cast(object, fake_sandbox_manager))
        planner.db = FakeTargetDatabase()

        target = {
            "class_name": "org.example.Example",
            "method_name": "doWork",
            "method_signature": "void doWork()",
            "method_coverage": 0.5,
        }

        test_future = FakeWorkerFuture(error=TimeoutError())
        mutant_future = FakeWorkerFuture(result_value={"generated": 1})
        futures = iter([test_future, mutant_future])

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
            "测试生成超时: org.example.Example#doWork:void doWork() (timeout=75s)", warning_output
        )
        self.assertNotIn("测试生成异常:", warning_output)
        self.assertEqual(test_future.timeouts, [75])

    def test_process_single_target_does_not_block_after_mutant_generation_timeout(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.project_path = "/tmp/project"
        planner.timeout_per_target = 75
        fake_sandbox_manager = FakeSandboxManager("/tmp/sandboxes/target-1")
        planner.sandbox_manager = cast(SandboxManager, cast(object, fake_sandbox_manager))
        planner.db = FakeTargetDatabase()
        planner.state = ParallelAgentState()

        target = {
            "class_name": "org.example.Example",
            "method_name": "doWork",
            "method_signature": "void doWork()",
            "method_coverage": 0.5,
        }

        fake_executor = FakeExecutor(max_workers=2)
        test_future = FakeWorkerFuture(result_value={"generated": 1, "test_files": {}})
        mutant_future = FakeWorkerFuture(error=TimeoutError(), done=False, can_cancel=False)

        with patch(
            "comet.agent.parallel_planner.ThreadPoolExecutor",
            side_effect=lambda max_workers: fake_executor,
        ):
            with patch(
                "comet.agent.parallel_planner.submit_with_log_context",
                side_effect=[test_future, mutant_future],
            ):
                with self.assertLogs(
                    "comet.agent.parallel_planner", level="WARNING"
                ) as captured_logs:
                    result = planner._process_single_target_impl(
                        target,
                        "org.example.Example",
                        "doWork",
                        "org.example.Example#doWork:void doWork()",
                    )

        self.assertTrue(result.success)
        self.assertEqual(result.mutants_generated, 0)
        self.assertEqual(mutant_future.cancel_calls, 1)
        self.assertEqual(fake_executor.shutdown_calls, [(False, True)])
        self.assertEqual(fake_sandbox_manager.cleaned, [])

        warning_output = "\n".join(captured_logs.output)
        self.assertIn(
            "变异体生成超时: org.example.Example#doWork:void doWork() (timeout=75s)",
            warning_output,
        )
        self.assertEqual(test_future.timeouts, [75])
        self.assertEqual(mutant_future.timeouts, [75])

        mutant_future.finish()

        self.assertEqual(fake_sandbox_manager.cleaned, ["target-1"])


class ParallelPlannerMutationAggregationTest(unittest.TestCase):
    def test_init_seeds_global_mutation_flag_before_later_sync(self):
        tools = Mock()
        tools.config = SimpleNamespace(
            evolution=SimpleNamespace(mutation_enabled=False),
        )

        planner = ParallelPlannerAgent(
            llm_client=cast(LLMClient, cast(object, FakeLLMCounter())),
            tools=tools,
            target_selector=cast(TargetSelector, cast(object, Mock())),
            java_executor=cast(JavaExecutor, cast(object, Mock())),
            sandbox_manager=cast(SandboxManager, cast(object, Mock())),
            database=cast(Database, Mock()),
            project_path="/tmp/project",
            workspace_path="/tmp/workspace",
        )

        self.assertFalse(planner.mutation_enabled)
        self.assertFalse(planner.state.global_mutation_enabled)
        payload = planner.state.to_dict()
        self.assertFalse(payload["global_mutation_enabled"])
        self.assertFalse(payload["globalMutationEnabled"])

    def test_sync_global_state_marks_mutation_disabled_and_skips_mutant_aggregation(self):
        planner = ParallelPlannerAgent.__new__(ParallelPlannerAgent)
        planner.mutation_enabled = False
        planner.state = ParallelAgentState()
        planner.state.line_coverage = 0.6
        planner.state.branch_coverage = 0.4

        fake_db = Mock()
        fake_db.get_all_evaluated_mutants.side_effect = AssertionError(
            "mutation disabled should not query evaluated mutants"
        )
        fake_db.get_all_test_cases.return_value = []
        planner.db = fake_db

        planner._sync_global_state()
        payload = planner.state.to_dict()

        fake_db.get_all_evaluated_mutants.assert_not_called()
        self.assertFalse(planner.state.global_mutation_enabled)
        self.assertIsNone(planner.state.global_total_mutants)
        self.assertIsNone(planner.state.global_killed_mutants)
        self.assertIsNone(planner.state.global_survived_mutants)
        self.assertIsNone(planner.state.global_mutation_score)
        self.assertIsNone(payload["global_total_mutants"])
        self.assertIsNone(payload["global_killed_mutants"])
        self.assertIsNone(payload["global_survived_mutants"])
        self.assertIsNone(payload["global_mutation_score"])
        self.assertFalse(payload["global_mutation_enabled"])
        self.assertFalse(payload["globalMutationEnabled"])

    def test_worker_card_preserves_disabled_mutation_semantics(self):
        state = ParallelAgentState()
        state.add_batch_result(
            [
                WorkerResult(
                    target_id=build_method_key("Calculator", "add", "int add(int a, int b)"),
                    class_name="Calculator",
                    method_name="add",
                    method_signature="int add(int a, int b)",
                    success=True,
                    tests_generated=2,
                    mutants_generated=None,
                    mutants_evaluated=None,
                    mutants_killed=None,
                    local_mutation_score=None,
                    mutation_enabled=False,
                    processing_time=1.5,
                    method_coverage=0.4,
                )
            ]
        )

        card = state.get_worker_cards()[0]
        payload = state.to_dict()

        self.assertFalse(card["mutationEnabled"])
        self.assertIsNone(card["mutantsGenerated"])
        self.assertIsNone(card["mutantsEvaluated"])
        self.assertIsNone(card["mutantsKilled"])
        self.assertIsNone(card["localMutationScore"])
        self.assertFalse(payload["workerCards"][0]["mutationEnabled"])


if __name__ == "__main__":
    unittest.main()
