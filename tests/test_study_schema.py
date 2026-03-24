import unittest

from comet.web.study_protocol import (
    BASELINE_ARCHIVE_DIR,
    DEFAULT_STUDY_SAMPLE_SIZE,
    DEFAULT_STUDY_SEED,
    STUDY_ARCHIVE_DIRS,
    STUDY_ARM_NAMES,
    STUDY_OUTPUT_FILENAMES,
    StudyOutputSummarySchema,
    StudyPerMethodRowSchema,
    StudyPerMutantRecordSchema,
    StudyProtocolSchema,
    StudySampledMethodSchema,
    build_method_archive_dirs,
    build_study_protocol,
)


class StudySchemaTest(unittest.TestCase):
    def test_protocol_exports_stable_contract(self) -> None:
        protocol = build_study_protocol()
        self.assertEqual(protocol.arm_names, STUDY_ARM_NAMES)
        self.assertEqual(protocol.default_sample_size, DEFAULT_STUDY_SAMPLE_SIZE)
        self.assertEqual(protocol.default_seed, DEFAULT_STUDY_SEED)
        self.assertEqual(protocol.output_filenames, STUDY_OUTPUT_FILENAMES)
        self.assertEqual(protocol.archive_dirs, STUDY_ARCHIVE_DIRS)
        self.assertEqual(
            protocol.summary_fields,
            tuple(StudyOutputSummarySchema.model_fields.keys()),
        )
        self.assertEqual(
            protocol.per_method_fields,
            tuple(StudyPerMethodRowSchema.model_fields.keys()),
        )
        self.assertEqual(
            protocol.per_mutant_fields,
            tuple(StudyPerMutantRecordSchema.model_fields.keys()),
        )
        self.assertEqual(
            protocol.sampled_method_fields,
            tuple(StudySampledMethodSchema.model_fields.keys()),
        )
        self.assertEqual(
            protocol.per_method_fields,
            (
                "target_id",
                "arm",
                "class_name",
                "method_name",
                "method_signature",
                "archive_root",
                "baseline_dir",
                "m0_dir",
                "m2_dir",
                "m3_dir",
                "pre_line_coverage",
                "post_line_coverage",
                "pre_killed",
                "post_killed",
                "fixed_mutant_count",
                "delta_mutation_score",
                "delta_coverage",
                "final_kill_rate",
                "effective_operator_ratio",
            ),
        )

    def test_test_artifact_layout_contract(self) -> None:
        archive_dirs = build_method_archive_dirs("pkg.Class#method()")
        self.assertEqual(tuple(archive_dirs.keys()), STUDY_ARCHIVE_DIRS)
        self.assertEqual(archive_dirs[BASELINE_ARCHIVE_DIR], "pkg.Class#method()/baseline")
        self.assertEqual(archive_dirs["M0"], "pkg.Class#method()/M0")
        self.assertEqual(archive_dirs["M2"], "pkg.Class#method()/M2")
        self.assertEqual(archive_dirs["M3"], "pkg.Class#method()/M3")

    def test_output_filename_set_is_fixed(self) -> None:
        self.assertEqual(
            set(STUDY_OUTPUT_FILENAMES.values()),
            {"summary.json", "per_method.csv", "per_mutant.jsonl", "sampled_methods.json"},
        )

    def test_protocol_schema_formula_strings_are_stable(self) -> None:
        protocol = StudyProtocolSchema()
        self.assertEqual(
            protocol.delta_mutation_score_formula,
            "post_killed/baseline_total_mutants - pre_killed/baseline_total_mutants",
        )
        self.assertEqual(
            protocol.delta_coverage_formula,
            "post_line_coverage - pre_line_coverage",
        )
        self.assertEqual(protocol.final_kill_rate_formula, "post_killed/baseline_total_mutants")
        self.assertEqual(
            protocol.effective_operator_ratio_formula,
            (
                "count(distinct post_killed_operators intersect fixed_denominator_operators)/"
                "count(distinct fixed_denominator_operators)"
            ),
        )


if __name__ == "__main__":
    _ = unittest.main()
