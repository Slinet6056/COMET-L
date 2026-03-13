import json
import unittest
from collections.abc import Mapping

from comet.executor.java_executor import JavaExecutor


class StubJavaExecutor(JavaExecutor):
    _payload: Mapping[str, object]

    def __init__(self, payload: Mapping[str, object]) -> None:
        super().__init__(java_runtime_jar="/tmp/nonexistent.jar")
        self._payload = payload

    def _run_java_command(
        self, main_class: str, args: list[str], timeout: int = 300
    ) -> dict[str, object]:
        _ = (main_class, args, timeout)
        return {
            "success": False,
            "returncode": 1,
            "stdout": json.dumps(self._payload),
            "stderr": "",
        }


class RawFailureJavaExecutor(JavaExecutor):
    def __init__(self, result: Mapping[str, object]) -> None:
        super().__init__(java_runtime_jar="/tmp/nonexistent.jar")
        self._result = dict(result)

    def _run_java_command(
        self, main_class: str, args: list[str], timeout: int = 300
    ) -> dict[str, object]:
        _ = (main_class, args, timeout)
        return dict(self._result)


class JavaExecutorResultNormalizationTests(unittest.TestCase):
    @staticmethod
    def _make_executor(payload: Mapping[str, object]) -> JavaExecutor:
        return StubJavaExecutor(payload)

    def test_compile_tests_uses_output_as_error_when_parsed_json_lacks_error(self) -> None:
        payload = {
            "success": False,
            "exitCode": 1,
            "output": "[ERROR] cannot find symbol",
        }

        executor = self._make_executor(payload)

        result = executor.compile_tests("/tmp/project")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "[ERROR] cannot find symbol")
        self.assertEqual(result["output"], "[ERROR] cannot find symbol")

    def test_compile_project_uses_output_as_error_when_parsed_json_lacks_error(self) -> None:
        payload = {
            "success": False,
            "exitCode": 1,
            "output": "[ERROR] package does not exist",
        }

        executor = self._make_executor(payload)

        result = executor.compile_project("/tmp/project")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "[ERROR] package does not exist")
        self.assertEqual(result["output"], "[ERROR] package does not exist")

    def test_run_tests_uses_output_as_error_when_parsed_json_lacks_error(self) -> None:
        payload = {
            "success": False,
            "exitCode": 1,
            "output": "Tests run: 1, Failures: 1",
        }

        executor = self._make_executor(payload)

        result = executor.run_tests("/tmp/project")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Tests run: 1, Failures: 1")
        self.assertEqual(result["output"], "Tests run: 1, Failures: 1")

    def test_normalization_preserves_explicit_error(self) -> None:
        payload = {
            "success": False,
            "exitCode": 1,
            "error": "Timeout after 60 seconds",
            "output": "[ERROR] build timed out",
        }

        executor = self._make_executor(payload)

        result = executor.compile_tests("/tmp/project")

        self.assertEqual(result["error"], "Timeout after 60 seconds")
        self.assertEqual(result["output"], "[ERROR] build timed out")

    def test_compile_project_normalizes_non_json_failure_output(self) -> None:
        executor = RawFailureJavaExecutor(
            {
                "success": False,
                "returncode": 1,
                "stdout": "[ERROR] compilation failure\nmissing symbol",
                "stderr": "",
            }
        )

        result = executor.compile_project("/tmp/project")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "[ERROR] compilation failure\nmissing symbol")
