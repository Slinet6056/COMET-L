import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from comet.agent.state import AgentState
from comet.agent.tools import AgentTools
from comet.models import TestCase, TestMethod
from comet.parallel_preprocessing import ParallelPreprocessor
from comet.store.database import Database
from comet.utils.code_utils import build_test_class
from comet.utils.method_keys import build_method_key


class FailedTargetSignatureTests(unittest.TestCase):
    def test_add_failed_target_preserves_explicit_signature_without_current_target(self) -> None:
        state = AgentState()

        state.add_failed_target(
            "Calculator",
            "add",
            "boom",
            "int add(int a, int b)",
        )

        self.assertEqual(
            state.failed_targets[0]["target"],
            build_method_key("Calculator", "add", "int add(int a, int b)"),
        )
        self.assertEqual(state.failed_targets[0]["method_signature"], "int add(int a, int b)")


class AgentToolsSignatureInheritanceTests(unittest.TestCase):
    def test_generate_tests_inherits_current_target_signature(self) -> None:
        tools = AgentTools()
        tools.project_path = "/tmp/project"
        tools.test_generator = object()
        tools.java_executor = object()
        tools.db = object()
        tools.sandbox_manager = object()
        tools.state = AgentState()
        tools.state.current_target = {
            "class_name": "Calculator",
            "method_name": "add",
            "method_signature": "int add(int a, int b)",
        }
        tools._generate_and_verify_in_sandbox = Mock(
            return_value={"success": False, "error": "boom", "sandbox_id": None}
        )

        tools.generate_tests("Calculator", "add")

        tools._generate_and_verify_in_sandbox.assert_called_once_with(
            "Calculator",
            "add",
            "int add(int a, int b)",
        )
        self.assertEqual(
            tools.state.failed_targets[0]["target"],
            build_method_key("Calculator", "add", "int add(int a, int b)"),
        )

    def test_refine_mutants_inherits_current_target_signature(self) -> None:
        tools = AgentTools()
        tools.project_path = "/tmp/project"
        tools.db = Mock()
        tools.db.get_mutants_by_method.return_value = []
        tools.db.get_tests_by_target_class.return_value = [Mock()]
        tools.mutant_generator = Mock()
        tools.mutant_generator.refine_mutants.return_value = []
        tools.static_guard = Mock()
        tools.state = AgentState()
        tools.state.current_target = {
            "class_name": "Calculator",
            "method_name": "add",
            "method_signature": "int add(int a, int b)",
        }

        with TemporaryDirectory() as tmp_dir:
            java_file = Path(tmp_dir) / "Calculator.java"
            java_file.write_text("public class Calculator {}", encoding="utf-8")
            tools.project_path = tmp_dir
            tools.db.find_class_file = Mock(return_value=str(java_file))

            tools.refine_mutants("Calculator", "add")

        tools.db.get_mutants_by_method.assert_called_once_with(
            class_name="Calculator",
            method_name="add",
            status=None,
            method_signature="int add(int a, int b)",
        )


class AgentToolsMutationDisabledTests(unittest.TestCase):
    def _build_tools(self, mutation_enabled: bool) -> AgentTools:
        tools = AgentTools()
        tools.config = SimpleNamespace(evolution=SimpleNamespace(mutation_enabled=mutation_enabled))
        tools.project_path = "/tmp/project"
        tools.mutant_generator = Mock()
        tools.static_guard = Mock()
        tools.db = Mock()
        tools.mutation_evaluator = Mock()
        tools.java_executor = Mock()
        return tools

    def test_generate_mutants_returns_disabled_status_when_mutation_disabled(self) -> None:
        tools = self._build_tools(mutation_enabled=False)

        result = tools.generate_mutants("Calculator", "add")

        self.assertEqual(result["status"], "disabled")
        self.assertTrue(result["skipped"])
        self.assertTrue(result["disabled"])
        self.assertFalse(result["mutation_enabled"])
        self.assertEqual(result["reason"], "mutation_disabled")
        self.assertEqual(result["generated"], 0)
        tools.mutant_generator.generate_mutants.assert_not_called()

    def test_refine_mutants_returns_disabled_status_when_mutation_disabled(self) -> None:
        tools = self._build_tools(mutation_enabled=False)

        result = tools.refine_mutants("Calculator", "add")

        self.assertEqual(result["status"], "disabled")
        self.assertTrue(result["skipped"])
        self.assertTrue(result["disabled"])
        self.assertFalse(result["mutation_enabled"])
        self.assertEqual(result["reason"], "mutation_disabled")
        self.assertEqual(result["generated"], 0)
        tools.db.get_mutants_by_method.assert_not_called()
        tools.mutant_generator.refine_mutants.assert_not_called()

    def test_run_evaluation_returns_disabled_status_without_calling_mutation_boundary(
        self,
    ) -> None:
        tools = self._build_tools(mutation_enabled=False)

        result = tools.run_evaluation()

        self.assertEqual(result["status"], "disabled")
        self.assertTrue(result["skipped"])
        self.assertTrue(result["disabled"])
        self.assertFalse(result["mutation_enabled"])
        self.assertEqual(result["reason"], "mutation_disabled")
        self.assertEqual(result["evaluated"], 0)
        self.assertEqual(result["killed"], 0)
        self.assertEqual(result["survived"], 0)
        self.assertEqual(result["mutation_score"], 0.0)
        tools.db.get_valid_mutants.assert_not_called()
        tools.db.get_all_tests.assert_not_called()
        tools.mutation_evaluator.build_kill_matrix.assert_not_called()
        tools.java_executor.apply_mutation.assert_not_called()

    def test_run_evaluation_uses_empty_status_when_enabled_but_no_mutants(self) -> None:
        tools = self._build_tools(mutation_enabled=True)
        tools.db.get_valid_mutants.return_value = []
        tools.db.get_all_tests.return_value = []

        result = tools.run_evaluation()

        self.assertEqual(result["status"], "empty")
        self.assertFalse(result["skipped"])
        self.assertFalse(result["disabled"])
        self.assertTrue(result["mutation_enabled"])
        self.assertEqual(result["reason"], "no_mutants")
        self.assertEqual(result["evaluated"], 0)
        tools.mutation_evaluator.build_kill_matrix.assert_not_called()

    def test_select_target_propagates_mutation_disabled_to_selector_fail_fast(self) -> None:
        tools = self._build_tools(mutation_enabled=False)

        with self.assertRaisesRegex(ValueError, "已禁用变异分析.*killrate"):
            tools.select_target("killrate")

        tools.db.get_method_mutant_stats.assert_not_called()
        tools.db.get_low_coverage_methods.assert_not_called()


class DatabaseMethodSignatureIsolationTests(unittest.TestCase):
    def test_get_tests_by_target_method_returns_only_matching_signature_methods(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database = Database(str(Path(tmp_dir) / "comet.db"))
            try:
                methods = [
                    TestMethod(
                        method_name="testAddInt",
                        code="@Test void testAddInt() {}",
                        target_method="add",
                        target_method_signature="int add(int a, int b)",
                    ),
                    TestMethod(
                        method_name="testAddDouble",
                        code="@Test void testAddDouble() {}",
                        target_method="add",
                        target_method_signature="double add(double a, double b)",
                    ),
                ]
                database.save_test_case(
                    TestCase(
                        id="tc-1",
                        class_name="CalculatorAddTest",
                        target_class="Calculator",
                        package_name=None,
                        imports=[],
                        methods=methods,
                        full_code=build_test_class(
                            "CalculatorAddTest",
                            "Calculator",
                            None,
                            [],
                            [method.code for method in methods],
                        ),
                        compile_success=True,
                    )
                )

                results = database.get_tests_by_target_method(
                    "Calculator",
                    "add",
                    "int add(int a, int b)",
                )

                self.assertEqual(len(results), 1)
                self.assertEqual(len(results[0].methods), 1)
                self.assertEqual(results[0].methods[0].method_name, "testAddInt")
                self.assertIn("testAddInt", results[0].full_code or "")
                self.assertNotIn("testAddDouble", results[0].full_code or "")
            finally:
                database.close()


class ParallelPreprocessingSignatureCleanupTests(unittest.TestCase):
    def test_delete_test_method_from_db_forwards_method_signature(self) -> None:
        db = Mock()
        db.get_tests_by_target_class.return_value = [
            TestCase(
                id="tc-1",
                class_name="CalculatorAddTest",
                target_class="Calculator",
                methods=[
                    TestMethod(
                        method_name="testAdd",
                        code="@Test void testAdd() {}",
                        target_method="add",
                        target_method_signature="int add(int a, int b)",
                    )
                ],
                compile_success=True,
            )
        ]
        db.delete_test_method.return_value = True
        db.get_test_case.return_value = SimpleNamespace(methods=[object()])

        config = SimpleNamespace(
            preprocessing=SimpleNamespace(max_workers=1, timeout_per_method=30),
            evolution=SimpleNamespace(min_method_lines=1),
            formatting=SimpleNamespace(enabled=False, style="google"),
        )
        components = {
            "sandbox_manager": object(),
            "java_executor": object(),
            "test_generator": object(),
            "mutant_generator": object(),
            "static_guard": object(),
            "mutation_evaluator": object(),
            "db": db,
            "project_scanner": object(),
        }
        preprocessor = ParallelPreprocessor(config, components)

        preprocessor._delete_test_method_from_db(
            "Calculator",
            "testAdd",
            "int add(int a, int b)",
        )

        db.delete_test_method.assert_called_once_with(
            "tc-1",
            "testAdd",
            "int add(int a, int b)",
        )


if __name__ == "__main__":
    unittest.main()
