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

    def test_select_by_coverage_require_unprocessed_skips_processed_fallback(self) -> None:
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
            database.get_low_coverage_methods.return_value = [low_cov]
            database.get_all_method_coverage.return_value = [low_cov]

            selector = TargetSelector(
                project_path=str(Path(tmp_dir)),
                java_executor=Mock(),
                database=database,
            )
            selector._get_public_methods = Mock(return_value=[])

            selected = selector.select_by_coverage(
                processed_targets={build_method_key("Calculator", "add", "int add(int a, int b)")},
                require_unprocessed=True,
            )

            self.assertIsNone(selected["class_name"])
            self.assertIsNone(selected["method_name"])

    def test_select_by_coverage_require_unprocessed_only_counts_low_coverage_targets(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database = Mock()
            high_cov = MethodCoverage(
                class_name="Calculator",
                method_name="stable",
                method_signature="int stable()",
                covered_lines=[10, 11, 12, 13],
                missed_lines=[],
                total_lines=4,
                covered_branches=0,
                missed_branches=0,
                total_branches=0,
                line_coverage_rate=1.0,
                branch_coverage_rate=0.0,
            )
            database.get_low_coverage_methods.return_value = []
            database.get_all_method_coverage.return_value = [high_cov]

            selector = TargetSelector(
                project_path=str(Path(tmp_dir)),
                java_executor=Mock(),
                database=database,
            )
            selector._get_public_methods = Mock(return_value=[])

            selected = selector.select_by_coverage(require_unprocessed=True)

            self.assertIsNone(selected["class_name"])
            self.assertIsNone(selected["method_name"])


class TargetSelectorMutationDisabledFailFastTests(unittest.TestCase):
    def test_select_rejects_killrate_without_mutation_data_when_mutation_disabled(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database = Mock()
            selector = TargetSelector(
                project_path=str(Path(tmp_dir)),
                java_executor=Mock(),
                database=database,
                mutation_enabled=False,
            )

            with self.assertRaisesRegex(ValueError, "已禁用变异分析.*killrate"):
                selector.select(criteria="killrate")

            database.get_method_mutant_stats.assert_not_called()
            database.get_low_coverage_methods.assert_not_called()

    def test_mutation_dependent_strategies_fail_fast_when_mutation_disabled(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database = Mock()
            selector = TargetSelector(
                project_path=str(Path(tmp_dir)),
                java_executor=Mock(),
                database=database,
                mutation_enabled=False,
            )

            strategies = [
                ("mutations", selector.select_by_mutations),
                ("priority", selector.select_by_priority),
            ]
            for strategy_name, strategy_method in strategies:
                with self.subTest(strategy=strategy_name):
                    with self.assertRaisesRegex(ValueError, f"已禁用变异分析.*{strategy_name}"):
                        strategy_method()

            database.get_all_mutants.assert_not_called()


class TargetSelectorKillrateFailFastTests(unittest.TestCase):
    def test_select_by_killrate_no_longer_falls_back_to_coverage_when_stats_missing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database = Mock()
            database.get_method_mutant_stats.return_value = {}
            selector = TargetSelector(
                project_path=str(Path(tmp_dir)),
                java_executor=Mock(),
                database=database,
            )

            with self.assertRaisesRegex(ValueError, "没有可用的变异统计数据"):
                selector.select_by_killrate()

            database.get_low_coverage_methods.assert_not_called()
