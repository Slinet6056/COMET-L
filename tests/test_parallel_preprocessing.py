from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

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
