import unittest

from comet.web.study_protocol import (
    DEFAULT_STUDY_SAMPLE_SIZE,
    StudyMutantStatus,
    choose_study_sample_size,
    compute_delta_coverage,
    compute_delta_mutation_score,
    compute_effective_operator_ratio,
    compute_final_kill_rate,
    count_survived_mutants,
)


class StudyMetricsTest(unittest.TestCase):
    def test_fixed_denominator_delta_metrics(self) -> None:
        self.assertAlmostEqual(compute_final_kill_rate(9, 12), 0.75)
        self.assertAlmostEqual(compute_delta_mutation_score(4, 9, 12), 5 / 12)
        self.assertAlmostEqual(compute_delta_coverage(0.35, 0.6), 0.25)
        self.assertAlmostEqual(
            compute_effective_operator_ratio(
                ["INVERT_NEGS", "VOID_METHOD_CALLS", "INVERT_NEGS"],
                ["INVERT_NEGS", "VOID_METHOD_CALLS", "MATH", "NEGATE_CONDITIONALS"],
            ),
            0.5,
        )

    def test_non_survived_statuses_are_not_collapsed(self) -> None:
        statuses = [
            StudyMutantStatus.SURVIVED,
            StudyMutantStatus.NO_COVERAGE,
            StudyMutantStatus.TIMED_OUT,
            StudyMutantStatus.RUN_ERROR,
            StudyMutantStatus.KILLED,
        ]
        self.assertEqual(count_survived_mutants(statuses), 1)
        self.assertEqual(
            count_survived_mutants(
                [
                    StudyMutantStatus.NO_COVERAGE,
                    StudyMutantStatus.TIMED_OUT,
                    StudyMutantStatus.RUN_ERROR,
                ]
            ),
            0,
        )

    def test_fewer_than_twelve_methods_uses_all_methods(self) -> None:
        self.assertEqual(choose_study_sample_size(7), 7)
        self.assertEqual(choose_study_sample_size(20), DEFAULT_STUDY_SAMPLE_SIZE)

    def test_effective_operator_ratio_returns_zero_without_fixed_denominator(self) -> None:
        self.assertEqual(compute_effective_operator_ratio(["INVERT_NEGS"], []), 0.0)


if __name__ == "__main__":
    _ = unittest.main()
