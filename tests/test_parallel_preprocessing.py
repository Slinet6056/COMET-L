from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

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
