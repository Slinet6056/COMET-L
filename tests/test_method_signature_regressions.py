import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comet.agent.state import AgentState
from comet.agent.tools import AgentTools
from comet.executor.surefire_parser import TestResult as SurefireTestResult
from comet.executor.surefire_parser import TestSuiteResult as SurefireTestSuiteResult
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

    def test_add_failed_target_deduplicates_same_target(self) -> None:
        state = AgentState()

        state.add_failed_target(
            "Calculator",
            "add",
            "boom",
            "int add(int a, int b)",
        )
        state.add_failed_target(
            "Calculator",
            "add",
            "boom again",
            "int add(int a, int b)",
        )

        self.assertEqual(len(state.failed_targets), 1)
        self.assertEqual(
            state.failed_targets[0]["target"],
            build_method_key("Calculator", "add", "int add(int a, int b)"),
        )


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
            tools.db.get_class_file_path = Mock(return_value=str(java_file))

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


class AgentToolsVerifyAndFixTestsRegressionTests(unittest.TestCase):
    def _build_tools(self) -> AgentTools:
        tools = AgentTools()
        tools.project_path = "/tmp/project"
        tools.java_executor = Mock()
        tools.test_generator = Mock()
        tools._get_formatting_config = Mock(return_value=(False, "GOOGLE"))
        return tools

    def _build_test_case(self, method_code: str) -> TestCase:
        return TestCase(
            id="test-1",
            class_name="GeneratedTest",
            target_class="DefaultParser",
            package_name="com.example",
            imports=[],
            methods=[
                TestMethod(
                    method_name="testSharedName",
                    code=method_code,
                    target_method="parse",
                    target_method_signature="CommandLine parse(Options, String[])",
                )
            ],
            full_code=build_test_class(
                test_class_name="GeneratedTest",
                target_class="DefaultParser",
                package_name="com.example",
                imports=[],
                test_methods=[method_code],
            ),
        )

    def test_verify_and_fix_tests_prefers_current_class_results_when_method_names_overlap(
        self,
    ) -> None:
        tools = self._build_tools()
        original_code = "@Test\nvoid testSharedName() { assertTrue(false); }"
        fixed_code = "@Test\nvoid testSharedName() { assertTrue(true); }"
        test_case = self._build_test_case(original_code)

        tools.java_executor.compile_tests.side_effect = [
            {"success": True},
            {"success": True},
            {"success": True},
        ]
        tools.java_executor.run_tests.side_effect = [
            {"success": False, "error": "suite failed"},
            {"success": True},
        ]
        tools.java_executor.run_single_test_method.return_value = {"success": True}
        tools.test_generator.fix_single_method.return_value = fixed_code

        suite_results = [
            SurefireTestSuiteResult(
                name="com.other.OtherGeneratedTest",
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                error_tests=0,
                skipped_tests=0,
                time=0.1,
                test_cases=[
                    SurefireTestResult(
                        class_name="com.other.OtherGeneratedTest",
                        method_name="testSharedName",
                        time=0.1,
                        passed=True,
                    )
                ],
            ),
            SurefireTestSuiteResult(
                name="com.example.GeneratedTest",
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                error_tests=0,
                skipped_tests=0,
                time=0.1,
                test_cases=[
                    SurefireTestResult(
                        class_name="com.example.GeneratedTest",
                        method_name="testSharedName",
                        time=0.1,
                        passed=False,
                        failure_message="boom",
                    )
                ],
            ),
        ]

        with (
            patch(
                "comet.executor.surefire_parser.SurefireParser.parse_surefire_reports",
                return_value=suite_results,
            ),
            patch(
                "comet.utils.project_utils.write_test_file",
                return_value=Path("/tmp/GeneratedTest.java"),
            ),
        ):
            result = tools._verify_and_fix_tests(
                test_case,
                class_code="public class DefaultParser {}",
                project_path="/tmp/project",
            )

        self.assertTrue(result.compile_success)
        self.assertEqual(result.methods[0].code, fixed_code)
        tools.test_generator.fix_single_method.assert_called_once()
        tools.java_executor.run_single_test_method.assert_called_once_with(
            "/tmp/project",
            "com.example.GeneratedTest",
            "testSharedName",
        )
        self.assertEqual(tools.java_executor.run_tests.call_count, 2)

    def test_verify_and_fix_tests_ignores_unrelated_failures_for_other_test_classes(self) -> None:
        tools = self._build_tools()
        original_code = "@Test\nvoid testSharedName() { assertTrue(true); }"
        test_case = self._build_test_case(original_code)

        tools.java_executor.compile_tests.return_value = {"success": True}
        tools.java_executor.run_tests.return_value = {"success": False, "error": "suite failed"}

        suite_results = [
            SurefireTestSuiteResult(
                name="com.example.GeneratedTest",
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                error_tests=0,
                skipped_tests=0,
                time=0.1,
                test_cases=[
                    SurefireTestResult(
                        class_name="com.example.GeneratedTest",
                        method_name="testSharedName",
                        time=0.1,
                        passed=True,
                    )
                ],
            ),
            SurefireTestSuiteResult(
                name="com.other.OtherGeneratedTest",
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                error_tests=0,
                skipped_tests=0,
                time=0.1,
                test_cases=[
                    SurefireTestResult(
                        class_name="com.other.OtherGeneratedTest",
                        method_name="testSharedName",
                        time=0.1,
                        passed=False,
                        failure_message="boom",
                    )
                ],
            ),
        ]

        with patch(
            "comet.executor.surefire_parser.SurefireParser.parse_surefire_reports",
            return_value=suite_results,
        ):
            result = tools._verify_and_fix_tests(
                test_case,
                class_code="public class DefaultParser {}",
                project_path="/tmp/project",
            )

        self.assertTrue(result.compile_success)
        self.assertIsNone(result.compile_error)
        tools.test_generator.fix_single_method.assert_not_called()
        tools.java_executor.run_single_test_method.assert_not_called()

    def test_verify_and_fix_tests_accepts_final_success_when_only_other_suites_fail(self) -> None:
        tools = self._build_tools()
        original_code = "@Test\nvoid testSharedName() { assertTrue(false); }"
        fixed_code = "@Test\nvoid testSharedName() { assertTrue(true); }"
        test_case = self._build_test_case(original_code)

        tools.java_executor.compile_tests.side_effect = [
            {"success": True},
            {"success": True},
            {"success": True},
        ]
        tools.java_executor.run_tests.side_effect = [
            {"success": False, "error": "suite failed"},
            {"success": False, "error": "other suite failed"},
        ]
        tools.java_executor.run_single_test_method.return_value = {"success": True}
        tools.test_generator.fix_single_method.return_value = fixed_code

        initial_suite_results = [
            SurefireTestSuiteResult(
                name="com.example.GeneratedTest",
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                error_tests=0,
                skipped_tests=0,
                time=0.1,
                test_cases=[
                    SurefireTestResult(
                        class_name="com.example.GeneratedTest",
                        method_name="testSharedName",
                        time=0.1,
                        passed=False,
                        failure_message="boom",
                    )
                ],
            )
        ]
        final_suite_results = [
            SurefireTestSuiteResult(
                name="com.example.GeneratedTest",
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                error_tests=0,
                skipped_tests=0,
                time=0.1,
                test_cases=[
                    SurefireTestResult(
                        class_name="com.example.GeneratedTest",
                        method_name="testSharedName",
                        time=0.1,
                        passed=True,
                    )
                ],
            ),
            SurefireTestSuiteResult(
                name="com.other.OtherGeneratedTest",
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                error_tests=0,
                skipped_tests=0,
                time=0.1,
                test_cases=[
                    SurefireTestResult(
                        class_name="com.other.OtherGeneratedTest",
                        method_name="testSharedName",
                        time=0.1,
                        passed=False,
                        failure_message="boom",
                    )
                ],
            ),
        ]

        with (
            patch(
                "comet.executor.surefire_parser.SurefireParser.parse_surefire_reports",
                side_effect=[initial_suite_results, final_suite_results],
            ),
            patch(
                "comet.utils.project_utils.write_test_file",
                return_value=Path("/tmp/GeneratedTest.java"),
            ),
        ):
            result = tools._verify_and_fix_tests(
                test_case,
                class_code="public class DefaultParser {}",
                project_path="/tmp/project",
            )

        self.assertTrue(result.compile_success)
        self.assertIsNone(result.compile_error)
        self.assertEqual(result.methods[0].code, fixed_code)


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
