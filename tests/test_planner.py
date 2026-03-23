import unittest
from typing import Any
from unittest.mock import Mock

from comet.agent.planner import PlannerAgent


class PlannerMutationAblationTests(unittest.TestCase):
    def _build_planner(self, mutation_enabled: bool = False) -> tuple[PlannerAgent, Mock]:
        tools = Mock()
        tools.db = None
        llm = Mock()
        planner = PlannerAgent(
            llm_client=llm,
            tools=tools,
            max_iterations=5,
            budget=20,
            mutation_enabled=mutation_enabled,
        )
        return planner, tools

    def test_auto_workflow_for_new_target_skips_mutation_chain_when_disabled(self) -> None:
        planner, tools = self._build_planner(mutation_enabled=False)

        target = {
            "class_name": "Calculator",
            "method_name": "add",
            "method_signature": "int add(int a, int b)",
        }

        calls: list[tuple[str, dict[str, Any]]] = []

        def fake_call(action: str, **params: Any) -> dict[str, Any]:
            calls.append((action, params))
            if action == "generate_tests":
                return {"generated": 1}
            if action in {"generate_mutants", "run_evaluation"}:
                raise AssertionError(f"disabled 模式不应调用 {action}")
            return {}

        tools.call.side_effect = fake_call

        planner._auto_workflow_for_new_target(target)

        self.assertEqual([action for action, _ in calls], ["generate_tests"])

    def test_execute_tool_skips_mutation_actions_when_disabled(self) -> None:
        planner, tools = self._build_planner(mutation_enabled=False)

        result = planner._execute_tool({"action": "run_evaluation", "params": {}})

        self.assertEqual(result["status"], "disabled")
        self.assertTrue(result["skipped"])
        self.assertFalse(result["mutation_enabled"])
        tools.call.assert_not_called()

    def test_check_improvement_uses_test_only_signals_when_disabled(self) -> None:
        planner, _ = self._build_planner(mutation_enabled=False)
        planner.state.global_mutation_score = 0.0
        planner.state.line_coverage = 0.30
        planner.state.total_tests = 4

        has_improvement = planner._check_improvement(
            prev_mutation_score=None,
            prev_line_coverage=0.30,
            threshold=0.01,
            prev_total_tests=2,
        )

        self.assertTrue(has_improvement)

    def test_check_excellent_quality_in_test_only_mode_does_not_require_mutation_score(
        self,
    ) -> None:
        planner, _ = self._build_planner(mutation_enabled=False)
        planner.state.global_mutation_score = 0.0
        planner.state.line_coverage = 0.95
        planner.state.branch_coverage = 0.90

        self.assertTrue(planner._check_excellent_quality())

    def test_run_automatic_flow_calls_test_generation_without_mutation_tools(self) -> None:
        planner, tools = self._build_planner(mutation_enabled=False)

        calls: list[str] = []

        def fake_call(action: str, **params: Any) -> dict[str, Any]:
            calls.append(action)
            if action == "select_target":
                return {
                    "class_name": "Calculator",
                    "method_name": "add",
                    "method_signature": "int add(int a, int b)",
                }
            if action == "generate_tests":
                return {"generated": 1}
            if action in {"generate_mutants", "refine_mutants", "run_evaluation"}:
                raise AssertionError(f"disabled 模式不应调用 {action}")
            return {}

        tools.call.side_effect = fake_call
        tools.select_target.return_value = {"class_name": None, "method_name": None}
        planner._make_decision = Mock(
            side_effect=[
                {
                    "action": "select_target",
                    "params": {},
                    "reasoning": "先选目标",
                },
                {
                    "action": "stop",
                    "params": {},
                    "reasoning": "结束",
                },
            ]
        )

        planner.run(stop_on_no_improvement_rounds=5, min_improvement_threshold=0.01)

        self.assertEqual(calls, ["select_target", "generate_tests"])


if __name__ == "__main__":
    unittest.main()
