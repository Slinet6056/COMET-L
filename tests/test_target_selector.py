import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from comet.agent.target_selector import TargetSelector
from comet.executor.coverage_parser import MethodCoverage
from comet.utils.method_keys import build_method_key


class TargetSelectorKillrateSignatureTests(unittest.TestCase):
    def test_select_by_killrate_keeps_method_signature_for_overloads(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database = Mock()
            database.get_method_mutant_stats.return_value = {
                build_method_key("Calculator", "add", "int add(int a, int b)"): {
                    "class_name": "Calculator",
                    "method_name": "add",
                    "method_signature": "int add(int a, int b)",
                    "total": 4,
                    "killed": 1,
                    "survived": 3,
                    "killrate": 0.25,
                },
                build_method_key("Calculator", "add", "double add(double a, double b)"): {
                    "class_name": "Calculator",
                    "method_name": "add",
                    "method_signature": "double add(double a, double b)",
                    "total": 4,
                    "killed": 3,
                    "survived": 1,
                    "killrate": 0.75,
                },
            }

            selector = TargetSelector(
                project_path=str(Path(tmp_dir)),
                java_executor=Mock(),
                database=database,
            )
            selector._get_public_methods = Mock(
                return_value=[
                    {"name": "add", "signature": "int add(int a, int b)"},
                    {"name": "add", "signature": "double add(double a, double b)"},
                ]
            )

            selected = selector.select_by_killrate()

            self.assertEqual(selected["method_signature"], "int add(int a, int b)")
            self.assertEqual(selected["method_info"]["signature"], "int add(int a, int b)")

    def test_select_by_killrate_preserves_database_signature_without_method_metadata(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database = Mock()
            database.get_method_mutant_stats.return_value = {
                build_method_key("Calculator", "add", "int add(int a, int b)"): {
                    "class_name": "Calculator",
                    "method_name": "add",
                    "method_signature": "int add(int a, int b)",
                    "total": 4,
                    "killed": 1,
                    "survived": 3,
                    "killrate": 0.25,
                }
            }

            selector = TargetSelector(
                project_path=str(Path(tmp_dir)),
                java_executor=Mock(),
                database=database,
            )
            selector._get_public_methods = Mock(return_value=[])

            selected = selector.select_by_killrate()

            self.assertEqual(selected["method_signature"], "int add(int a, int b)")
            self.assertIsNone(selected["method_info"])


class TargetSelectorCoverageSignatureTests(unittest.TestCase):
    def test_select_by_coverage_preserves_database_signature_without_method_metadata(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database = Mock()
            database.get_low_coverage_methods.return_value = [
                MethodCoverage(
                    class_name="Calculator",
                    method_name="add",
                    method_signature="int add(int a, int b)",
                    covered_lines=[],
                    missed_lines=[10, 11],
                    total_lines=2,
                    covered_branches=0,
                    missed_branches=0,
                    total_branches=0,
                    line_coverage_rate=0.0,
                    branch_coverage_rate=0.0,
                )
            ]

            selector = TargetSelector(
                project_path=str(Path(tmp_dir)),
                java_executor=Mock(),
                database=database,
            )
            selector._get_public_methods = Mock(return_value=[])

            selected = selector.select_by_coverage()

            self.assertEqual(selected["method_signature"], "int add(int a, int b)")
            self.assertIsNone(selected["method_info"])

    def test_select_by_coverage_uses_database_signature_for_blacklist_and_processed(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database = Mock()
            low_cov = MethodCoverage(
                class_name="Calculator",
                method_name="add",
                method_signature="int add(int a, int b)",
                covered_lines=[],
                missed_lines=[10, 11],
                total_lines=2,
                covered_branches=0,
                missed_branches=0,
                total_branches=0,
                line_coverage_rate=0.0,
                branch_coverage_rate=0.0,
            )
            fallback = MethodCoverage(
                class_name="Calculator",
                method_name="subtract",
                method_signature="int subtract(int a, int b)",
                covered_lines=[],
                missed_lines=[20, 21],
                total_lines=2,
                covered_branches=0,
                missed_branches=0,
                total_branches=0,
                line_coverage_rate=0.1,
                branch_coverage_rate=0.0,
            )
            database.get_low_coverage_methods.return_value = [low_cov, fallback]

            selector = TargetSelector(
                project_path=str(Path(tmp_dir)),
                java_executor=Mock(),
                database=database,
            )
            selector._get_public_methods = Mock(return_value=[])

            selected = selector.select_by_coverage(
                blacklist={build_method_key("Calculator", "add", "int add(int a, int b)")},
                processed_targets={build_method_key("Calculator", "add", "int add(int a, int b)")},
            )

            self.assertEqual(selected["method_name"], "subtract")
            self.assertEqual(selected["method_signature"], "int subtract(int a, int b)")
