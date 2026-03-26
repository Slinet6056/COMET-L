from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from comet.models import Mutant, MutationPatch, TestMethod
from comet.models import TestCase as GeneratedTestCase
from comet.parallel_preprocessing import ParallelPreprocessor
from comet.utils.method_keys import build_preprocess_task_id
from comet.web.log_router import RunLogRouter


class ParallelPreprocessingLifecycleTests(TestCase):
    def _build_preprocessor(self, *, publisher: Mock | None = None) -> ParallelPreprocessor:
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
            "db": object(),
            "project_scanner": object(),
            "log_router": RunLogRouter(max_entries_per_stream=10),
            "runtime_snapshot_publisher": publisher,
        }
        return ParallelPreprocessor(config, components)

    def test_process_method_marks_preprocessing_stream_completed_immediately(
        self,
    ) -> None:
        publisher = Mock()
        preprocessor = self._build_preprocessor(publisher=publisher)
        setattr(
            preprocessor,
            "_process_method",
            Mock(return_value={"success": True, "elapsed": 1.25}),
        )

        result = preprocessor._process_method_with_timeout(
            "Calculator", "add", {"signature": "int add(int a, int b)"}
        )

        self.assertTrue(result["success"])
        router = preprocessor.log_router
        self.assertIsNotNone(router)
        assert router is not None
        stream = router.get_stream(
            build_preprocess_task_id("Calculator", "add", "int add(int a, int b)")
        )
        self.assertIsNotNone(stream)
        assert stream is not None
        self.assertEqual(stream["status"], "completed")
        self.assertIsNotNone(stream["completedAt"])
        self.assertIsNotNone(stream["endedAt"])
        self.assertEqual(stream["durationSeconds"], 1.25)
        self.assertGreaterEqual(publisher.call_count, 2)

    def test_process_method_marks_preprocessing_stream_failed_immediately(self) -> None:
        publisher = Mock()
        preprocessor = self._build_preprocessor(publisher=publisher)
        setattr(
            preprocessor,
            "_process_method",
            Mock(return_value={"success": False, "error": "boom", "elapsed": 0.5}),
        )

        result = preprocessor._process_method_with_timeout(
            "Calculator", "divide", {"signature": "int divide(int a, int b)"}
        )

        self.assertFalse(result["success"])
        router = preprocessor.log_router
        self.assertIsNotNone(router)
        assert router is not None
        stream = router.get_stream(
            build_preprocess_task_id("Calculator", "divide", "int divide(int a, int b)")
        )
        self.assertIsNotNone(stream)
        assert stream is not None
        self.assertEqual(stream["status"], "failed")
        self.assertIsNone(stream["completedAt"])
        self.assertIsNotNone(stream["endedAt"])
        self.assertEqual(stream["durationSeconds"], 0.5)
        self.assertGreaterEqual(publisher.call_count, 2)

    def test_overloaded_methods_use_distinct_preprocessing_stream_ids(self) -> None:
        publisher = Mock()
        preprocessor = self._build_preprocessor(publisher=publisher)
        setattr(
            preprocessor,
            "_process_method",
            Mock(
                side_effect=[{"success": False, "elapsed": 0.25}, {"success": True, "elapsed": 0.4}]
            ),
        )

        first_signature = "int add(int a, int b)"
        second_signature = "double add(double a, double b)"
        preprocessor._process_method_with_timeout(
            "Calculator", "add", {"signature": first_signature}
        )
        preprocessor._process_method_with_timeout(
            "Calculator", "add", {"signature": second_signature}
        )

        router = preprocessor.log_router
        self.assertIsNotNone(router)
        assert router is not None
        first_stream = router.get_stream(
            build_preprocess_task_id("Calculator", "add", first_signature)
        )
        second_stream = router.get_stream(
            build_preprocess_task_id("Calculator", "add", second_signature)
        )
        self.assertIsNotNone(first_stream)
        self.assertIsNotNone(second_stream)
        assert first_stream is not None
        assert second_stream is not None
        self.assertEqual(first_stream["status"], "failed")
        self.assertEqual(second_stream["status"], "completed")


class ParallelPreprocessingFailureReasonTests(TestCase):
    def _build_preprocessor(self) -> tuple[ParallelPreprocessor, Mock]:
        config = SimpleNamespace(
            preprocessing=SimpleNamespace(max_workers=1, timeout_per_method=30),
            evolution=SimpleNamespace(min_method_lines=1),
            formatting=SimpleNamespace(enabled=False, style="google"),
        )
        sandbox_manager = Mock()
        sandbox_manager.create_target_sandbox.return_value = "/tmp/sandbox-1"
        components = {
            "sandbox_manager": sandbox_manager,
            "java_executor": Mock(),
            "test_generator": Mock(),
            "mutant_generator": Mock(),
            "static_guard": Mock(),
            "mutation_evaluator": Mock(),
            "db": Mock(),
            "project_scanner": Mock(),
        }
        preprocessor = ParallelPreprocessor(config, components)
        preprocessor.project_path = "/tmp/project"
        preprocessor.workspace_sandbox = "/tmp/workspace"
        return preprocessor, sandbox_manager

    def test_process_method_returns_specific_reason_when_class_file_is_missing(self) -> None:
        preprocessor, sandbox_manager = self._build_preprocessor()

        with patch("comet.utils.project_utils.find_java_file", return_value=None):
            result = preprocessor._process_method(
                "Calculator",
                "add",
                {"signature": "int add(int a, int b)"},
            )

        self.assertFalse(result.get("success", False))
        self.assertEqual(result.get("error"), "未找到类文件: Calculator")
        sandbox_manager.cleanup_sandbox.assert_called_once_with("sandbox-1")

    def test_parallel_process_logs_concise_failure_reason(self) -> None:
        preprocessor, _ = self._build_preprocessor()
        setattr(
            preprocessor,
            "_process_method_with_timeout",
            Mock(
                return_value={
                    "success": False,
                    "error": "未找到类文件: Calculator",
                    "elapsed": 0.1,
                }
            ),
        )

        with self.assertLogs("comet.parallel_preprocessing", level="WARNING") as captured_logs:
            preprocessor._parallel_process_methods([("Calculator", "add", {})])

        logs = "\n".join(captured_logs.output)
        self.assertIn("✗ Calculator.add 失败: 未找到类文件", logs)
        self.assertNotIn("未找到类文件: Calculator", logs)
        self.assertNotIn("失败: Unknown", logs)

    def test_parallel_process_logs_compile_failure_without_verbose_details(self) -> None:
        preprocessor, _ = self._build_preprocessor()
        setattr(
            preprocessor,
            "_process_method_with_timeout",
            Mock(
                return_value={
                    "success": False,
                    "error": "测试编译失败: Calculator.add - [ERROR] Maven 编译失败\n[ERROR] line 12",
                    "elapsed": 0.1,
                }
            ),
        )

        with self.assertLogs("comet.parallel_preprocessing", level="WARNING") as captured_logs:
            preprocessor._parallel_process_methods([("Calculator", "add", {})])

        logs = "\n".join(captured_logs.output)
        self.assertIn("✗ Calculator.add 失败: 测试编译失败", logs)
        self.assertNotIn("测试编译失败: Calculator.add", logs)
        self.assertNotIn("Maven 编译失败", logs)
        self.assertNotIn("line 12", logs)


class ParallelPreprocessingPersistenceGuardTests(TestCase):
    def _build_preprocessor(self) -> tuple[ParallelPreprocessor, Mock]:
        config = SimpleNamespace(
            preprocessing=SimpleNamespace(max_workers=1, timeout_per_method=30),
            evolution=SimpleNamespace(min_method_lines=1),
            formatting=SimpleNamespace(enabled=False, style="google"),
        )
        db = Mock()
        db.get_all_test_cases.return_value = []
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
        return ParallelPreprocessor(config, components), db

    def test_stage_preprocessing_result_defers_database_persistence(self) -> None:
        preprocessor, db = self._build_preprocessor()
        test_case = GeneratedTestCase(
            id="tc-1",
            class_name="FooTest",
            target_class="Foo",
            package_name="com.example",
            imports=[],
            methods=[
                TestMethod(
                    method_name="testValue",
                    code="@Test void testValue() {}",
                    target_method="value",
                    target_method_signature="int value()",
                )
            ],
            full_code="class FooTest {}",
            compile_success=True,
        )
        mutant = Mutant(
            id="mutant-1",
            class_name="Foo",
            method_name="value",
            patch=MutationPatch(
                file_path="src/main/java/com/example/Foo.java",
                line_start=1,
                line_end=1,
                original_code="return 1;",
                mutated_code="return 2;",
            ),
        )

        preprocessor._stage_preprocessing_result(test_case, [mutant])

        db.save_test_case.assert_not_called()
        db.save_mutant.assert_not_called()
        self.assertEqual(preprocessor._get_candidate_test_cases(), [test_case])

    def test_commit_preprocessing_results_persists_staged_items(self) -> None:
        preprocessor, db = self._build_preprocessor()
        test_case = GeneratedTestCase(
            id="tc-1",
            class_name="FooTest",
            target_class="Foo",
            package_name="com.example",
            imports=[],
            methods=[
                TestMethod(
                    method_name="testValue",
                    code="@Test void testValue() {}",
                    target_method="value",
                    target_method_signature="int value()",
                )
            ],
            full_code="class FooTest {}",
            compile_success=True,
        )
        mutant = Mutant(
            id="mutant-1",
            class_name="Foo",
            method_name="value",
            patch=MutationPatch(
                file_path="src/main/java/com/example/Foo.java",
                line_start=1,
                line_end=1,
                original_code="return 1;",
                mutated_code="return 2;",
            ),
        )
        preprocessor._stage_preprocessing_result(test_case, [mutant])

        preprocessor._commit_preprocessing_results([test_case])

        db.save_test_case.assert_called_once_with(test_case)
        db.save_mutant.assert_called_once_with(mutant)


class ParallelPreprocessingMutationDisabledTests(TestCase):
    def _build_preprocessor(self) -> tuple[ParallelPreprocessor, Mock, Mock, Mock]:
        config = SimpleNamespace(
            preprocessing=SimpleNamespace(max_workers=1, timeout_per_method=30),
            evolution=SimpleNamespace(min_method_lines=1, mutation_enabled=False),
            formatting=SimpleNamespace(enabled=False, style="google"),
        )
        sandbox_manager = Mock()
        sandbox_manager.create_target_sandbox.return_value = "/tmp/sandbox-1"
        mutant_generator = Mock()
        mutation_evaluator = Mock()
        db = Mock()
        db.get_tests_by_target_class.return_value = []
        db.get_all_test_cases.return_value = []
        components = {
            "sandbox_manager": sandbox_manager,
            "java_executor": Mock(),
            "test_generator": Mock(),
            "mutant_generator": mutant_generator,
            "static_guard": Mock(),
            "mutation_evaluator": mutation_evaluator,
            "db": db,
            "project_scanner": Mock(),
        }
        preprocessor = ParallelPreprocessor(config, components)
        preprocessor.project_path = "/tmp/project"
        preprocessor.workspace_sandbox = "/tmp/workspace"
        return preprocessor, sandbox_manager, mutant_generator, mutation_evaluator

    def test_process_method_skips_mutant_generation_when_mutation_disabled(self) -> None:
        preprocessor, sandbox_manager, mutant_generator, mutation_evaluator = (
            self._build_preprocessor()
        )
        test_case = GeneratedTestCase(
            id="tc-1",
            class_name="FooTest",
            target_class="Foo",
            package_name="com.example",
            imports=[],
            methods=[
                TestMethod(
                    method_name="testValue",
                    code="@Test void testValue() {}",
                    target_method="value",
                    target_method_signature="int value()",
                )
            ],
            full_code="class FooTest {}",
            compile_success=True,
        )
        preprocessor.test_generator.generate_tests_for_method.return_value = test_case

        with (
            patch("comet.utils.project_utils.find_java_file") as mock_find_java_file,
            patch("comet.utils.code_utils.extract_class_from_file", return_value="class Foo {}"),
            patch("comet.utils.project_utils.write_test_file", return_value="/tmp/FooTest.java"),
            patch("comet.agent.tools.AgentTools._verify_and_fix_tests", return_value=test_case),
            patch("comet.utils.code_utils.validate_test_methods", return_value=[]),
        ):
            mock_find_java_file.side_effect = ["/tmp/workspace/Foo.java", "/tmp/sandbox-1/Foo.java"]

            result = preprocessor._process_method("Foo", "value", {"signature": "int value()"})

        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("tests"), 1)
        self.assertEqual(result.get("mutants"), 0)
        mutant_generator.generate_mutants.assert_not_called()
        mutation_evaluator.build_kill_matrix.assert_not_called()
        self.assertEqual(preprocessor._get_candidate_test_cases(), [test_case])
        sandbox_manager.cleanup_sandbox.assert_called_once_with("sandbox-1")


class ParallelPreprocessingHelperRetentionTests(TestCase):
    def _build_preprocessor(self) -> ParallelPreprocessor:
        config = SimpleNamespace(
            preprocessing=SimpleNamespace(max_workers=1, timeout_per_method=30),
            evolution=SimpleNamespace(min_method_lines=1),
            formatting=SimpleNamespace(enabled=False, style="google"),
        )
        components = {
            "sandbox_manager": Mock(),
            "java_executor": Mock(),
            "test_generator": Mock(),
            "mutant_generator": Mock(),
            "static_guard": Mock(),
            "mutation_evaluator": Mock(),
            "db": Mock(),
            "project_scanner": Mock(),
        }
        preprocessor = ParallelPreprocessor(config, components)
        preprocessor.project_path = "/tmp/project"
        preprocessor.workspace_sandbox = "/tmp/workspace"
        return preprocessor

    def test_merge_results_to_workspace_preserves_helper_class(self) -> None:
        preprocessor = self._build_preprocessor()
        helper_full_code = (
            "package com.google.gson;\n\n"
            "import org.junit.jupiter.api.Test;\n\n"
            "public class TypeAdapterFromJsonTest {\n\n"
            "    @Test\n"
            "    void testFromJsonWithComplexObject() {\n"
            "        SimpleObject obj = new SimpleObject();\n"
            '        obj.name = "demo";\n'
            "    }\n\n"
            "    class SimpleObject {\n"
            "        String name;\n"
            "    }\n"
            "}\n"
        )
        test_case = GeneratedTestCase(
            id="tc-helper",
            class_name="TypeAdapterFromJsonTest",
            target_class="TypeAdapter",
            package_name="com.google.gson",
            imports=["import org.junit.jupiter.api.Test;"],
            methods=[
                TestMethod(
                    method_name="testFromJsonWithComplexObject",
                    code=(
                        "@Test\n"
                        "void testFromJsonWithComplexObject() {\n"
                        "    SimpleObject obj = new SimpleObject();\n"
                        '    obj.name = "demo";\n'
                        "}"
                    ),
                    target_method="fromJson",
                    target_method_signature="T fromJson(Reader json)",
                )
            ],
            full_code=helper_full_code,
            compile_success=True,
        )
        preprocessor._get_candidate_test_cases = Mock(return_value=[test_case])
        preprocessor._build_and_validate_in_sandbox = Mock(return_value=test_case.methods)
        preprocessor._validate_and_fix_workspace_tests = Mock(side_effect=lambda cases: cases)
        preprocessor._commit_preprocessing_results = Mock()

        with (
            patch("comet.utils.project_utils.clear_test_directory", return_value=True),
            patch("comet.utils.project_utils.write_test_file") as mock_write_test_file,
        ):
            preprocessor._merge_results_to_workspace()

        preprocessor._build_and_validate_in_sandbox.assert_called_once_with(
            test_class_name="TypeAdapterFromJsonTest",
            target_class="TypeAdapter",
            package_name="com.google.gson",
            imports=["import org.junit.jupiter.api.Test;"],
            all_methods=test_case.methods,
            existing_full_code=helper_full_code,
        )

        self.assertTrue(mock_write_test_file.called)
        written_code = mock_write_test_file.call_args.kwargs["test_code"]
        self.assertIn("class SimpleObject", written_code)
        self.assertIn("testFromJsonWithComplexObject", written_code)

    def test_handle_workspace_test_failure_preserves_helper_class(self) -> None:
        preprocessor = self._build_preprocessor()
        helper_full_code = (
            "package com.google.gson;\n\n"
            "import org.junit.jupiter.api.Test;\n\n"
            "public class TypeAdapterFromJsonTest {\n\n"
            "    @Test\n"
            "    void testKeep() {\n"
            "        SimpleObject obj = new SimpleObject();\n"
            '        obj.name = "keep";\n'
            "    }\n\n"
            "    @Test\n"
            "    void testDrop() {\n"
            "        SimpleObject obj = new SimpleObject();\n"
            '        obj.name = "drop";\n'
            "    }\n\n"
            "    class SimpleObject {\n"
            "        String name;\n"
            "    }\n"
            "}\n"
        )
        keep_method = TestMethod(
            method_name="testKeep",
            code=(
                "@Test\n"
                "void testKeep() {\n"
                "    SimpleObject obj = new SimpleObject();\n"
                '    obj.name = "keep";\n'
                "}"
            ),
            target_method="fromJson",
            target_method_signature="T fromJson(Reader json)",
        )
        drop_method = TestMethod(
            method_name="testDrop",
            code=(
                "@Test\n"
                "void testDrop() {\n"
                "    SimpleObject obj = new SimpleObject();\n"
                '    obj.name = "drop";\n'
                "}"
            ),
            target_method="fromJson",
            target_method_signature="T fromJson(Reader json)",
        )
        test_case = GeneratedTestCase(
            id="tc-helper-failure",
            class_name="TypeAdapterFromJsonTest",
            target_class="TypeAdapter",
            package_name="com.google.gson",
            imports=["import org.junit.jupiter.api.Test;"],
            methods=[keep_method, drop_method],
            full_code=helper_full_code,
            compile_success=True,
        )
        preprocessor._identify_failed_test_methods = Mock(return_value={"testDrop"})
        preprocessor._rebuild_workspace_tests = Mock()

        updated_cases, removed_count = preprocessor._handle_workspace_test_failure([test_case])

        self.assertEqual(removed_count, 1)
        self.assertEqual(len(updated_cases), 1)
        self.assertIn("class SimpleObject", updated_cases[0].full_code or "")
        self.assertIn("testKeep", updated_cases[0].full_code or "")
        self.assertNotIn("testDrop", updated_cases[0].full_code or "")
