from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, Field

DEFAULT_STUDY_SAMPLE_SIZE = 12
DEFAULT_STUDY_SEED = 42
MINIMUM_SAMPLED_METHODS = 12

BASELINE_ARCHIVE_DIR = "baseline"
STUDY_ARM_NAMES = ("M0", "M2", "M3")
STUDY_ARCHIVE_DIRS = (BASELINE_ARCHIVE_DIR, *STUDY_ARM_NAMES)
STUDY_OUTPUT_FILENAMES = {
    "summary": "summary.json",
    "per_method": "per_method.csv",
    "per_mutant": "per_mutant.jsonl",
    "sampled_methods": "sampled_methods.json",
    "analysis_metrics": "analysis_metrics.csv",
}
SINGLE_ROUND_IMPROVEMENT_SEMANTICS = "单轮改进仅比较同一方法在固定分母基线下的 pre/post 变化"
SHARED_BASELINE_STRATEGY = "每个目标方法只生成一次共享 baseline，并复用于 M0/M2/M3 三个研究臂"
FEWER_THAN_MINIMUM_METHODS_BEHAVIOR = "候选方法少于 12 个时不降采样，直接全量纳入研究"


class StudyMutantStatus(StrEnum):
    KILLED = "KILLED"
    SURVIVED = "SURVIVED"
    NO_COVERAGE = "NO_COVERAGE"
    TIMED_OUT = "TIMED_OUT"
    RUN_ERROR = "RUN_ERROR"
    NON_VIABLE = "NON_VIABLE"
    MEMORY_ERROR = "MEMORY_ERROR"


SCORABLE_MUTANT_STATUSES = frozenset({StudyMutantStatus.KILLED, StudyMutantStatus.SURVIVED})
NON_SURVIVED_MUTANT_STATUSES = frozenset(
    {
        StudyMutantStatus.NO_COVERAGE,
        StudyMutantStatus.TIMED_OUT,
        StudyMutantStatus.RUN_ERROR,
        StudyMutantStatus.NON_VIABLE,
        StudyMutantStatus.MEMORY_ERROR,
    }
)


def choose_study_sample_size(
    total_methods: int, sample_size: int = DEFAULT_STUDY_SAMPLE_SIZE
) -> int:
    return min(max(total_methods, 0), sample_size)


def compute_final_kill_rate(killed_mutants: int, baseline_total_mutants: int) -> float:
    if baseline_total_mutants <= 0:
        return 0.0
    return killed_mutants / baseline_total_mutants


def compute_delta_mutation_score(
    pre_killed_mutants: int,
    post_killed_mutants: int,
    baseline_total_mutants: int,
) -> float:
    return compute_final_kill_rate(
        post_killed_mutants, baseline_total_mutants
    ) - compute_final_kill_rate(
        pre_killed_mutants,
        baseline_total_mutants,
    )


def compute_delta_coverage(pre_coverage: float, post_coverage: float) -> float:
    return post_coverage - pre_coverage


def compute_effective_operator_ratio(
    post_killed_operator_names: Sequence[str],
    fixed_denominator_operator_names: Sequence[str],
) -> float:
    denominator_operator_names = {
        operator_name for operator_name in fixed_denominator_operator_names if operator_name
    }
    if not denominator_operator_names:
        return 0.0
    effective_operator_names = {
        operator_name
        for operator_name in post_killed_operator_names
        if operator_name and operator_name in denominator_operator_names
    }
    return len(effective_operator_names) / len(denominator_operator_names)


def count_survived_mutants(statuses: Sequence[StudyMutantStatus | str]) -> int:
    return sum(1 for status in statuses if status == StudyMutantStatus.SURVIVED)


class StudyOutputSummarySchema(BaseModel):
    arm: str
    baseline_arm: str = Field(default=BASELINE_ARCHIVE_DIR)
    sample_size: int = Field(default=DEFAULT_STUDY_SAMPLE_SIZE, ge=0)
    seed: int = Field(default=DEFAULT_STUDY_SEED)
    method_count: int = Field(default=0, ge=0)
    baseline_total_mutants: int = Field(default=0, ge=0)
    pre_killed: int = Field(default=0, ge=0)
    post_killed: int = Field(default=0, ge=0)
    final_kill_rate: float = Field(default=0.0, ge=0.0)
    delta_mutation_score: float = 0.0
    pre_line_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    post_line_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    delta_coverage: float = 0.0
    effective_operator_ratio: float = Field(default=0.0, ge=0.0)


class StudyPerMethodRowSchema(BaseModel):
    target_id: str
    arm: str
    class_name: str
    method_name: str
    method_signature: str
    archive_root: str
    baseline_dir: str
    m0_dir: str
    m2_dir: str
    m3_dir: str
    pre_line_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    post_line_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    pre_killed: int = Field(default=0, ge=0)
    post_killed: int = Field(default=0, ge=0)
    fixed_mutant_count: int = Field(default=0, ge=0)
    delta_mutation_score: float = 0.0
    delta_coverage: float = 0.0
    final_kill_rate: float = Field(default=0.0, ge=0.0)
    effective_operator_ratio: float = Field(default=0.0, ge=0.0)


class StudyPerMutantRecordSchema(BaseModel):
    target_id: str
    arm: str
    mutant_id: str
    mutator: str
    pre_status: str
    post_status: str
    counts_as_killed: bool
    counts_as_survived: bool
    counts_in_fixed_denominator: bool


class StudySampledMethodSchema(BaseModel):
    target_id: str
    class_name: str
    method_name: str
    method_signature: str
    order: int = Field(ge=0)


class StudyAnalysisRowSchema(BaseModel):
    target_id: str
    arm: str
    class_name: str
    method_name: str
    method_signature: str
    test_archive_dir: str
    jacoco_line_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    jacoco_branch_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    jacoco_method_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    jacoco_class_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    jacoco_total_lines: int = Field(default=0, ge=0)
    jacoco_covered_lines: int = Field(default=0, ge=0)
    jacoco_total_branches: int = Field(default=0, ge=0)
    jacoco_covered_branches: int = Field(default=0, ge=0)
    jacoco_total_methods: int = Field(default=0, ge=0)
    jacoco_covered_methods: int = Field(default=0, ge=0)
    jacoco_total_classes: int = Field(default=0, ge=0)
    jacoco_covered_classes: int = Field(default=0, ge=0)
    pit_total_mutants: int = Field(default=0, ge=0)
    pit_killed_mutants: int = Field(default=0, ge=0)
    pit_survived_mutants: int = Field(default=0, ge=0)
    pit_no_coverage_mutants: int = Field(default=0, ge=0)
    pit_timed_out_mutants: int = Field(default=0, ge=0)
    pit_run_error_mutants: int = Field(default=0, ge=0)
    pit_mutation_kill_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    pit_test_strength: float = Field(default=0.0, ge=0.0, le=1.0)


class StudyProtocolSchema(BaseModel):
    arm_names: tuple[str, ...] = Field(default=STUDY_ARM_NAMES)
    shared_baseline_strategy: str = Field(default=SHARED_BASELINE_STRATEGY)
    single_round_improvement_semantics: str = Field(default=SINGLE_ROUND_IMPROVEMENT_SEMANTICS)
    delta_mutation_score_formula: str = Field(
        default="post_killed/baseline_total_mutants - pre_killed/baseline_total_mutants"
    )
    delta_coverage_formula: str = Field(default="post_line_coverage - pre_line_coverage")
    final_kill_rate_formula: str = Field(default="post_killed/baseline_total_mutants")
    effective_operator_ratio_formula: str = Field(
        default=(
            "count(distinct post_killed_operators intersect fixed_denominator_operators)/"
            "count(distinct fixed_denominator_operators)"
        )
    )
    fewer_than_minimum_methods_behavior: str = Field(default=FEWER_THAN_MINIMUM_METHODS_BEHAVIOR)
    default_sample_size: int = Field(default=DEFAULT_STUDY_SAMPLE_SIZE, ge=0)
    default_seed: int = Field(default=DEFAULT_STUDY_SEED)
    output_filenames: dict[str, str] = Field(default_factory=lambda: dict(STUDY_OUTPUT_FILENAMES))
    baseline_archive_dir: str = Field(default=BASELINE_ARCHIVE_DIR)
    archive_dirs: tuple[str, ...] = Field(default=STUDY_ARCHIVE_DIRS)
    summary_fields: tuple[str, ...] = Field(
        default=(
            "arm",
            "baseline_arm",
            "sample_size",
            "seed",
            "method_count",
            "baseline_total_mutants",
            "pre_killed",
            "post_killed",
            "final_kill_rate",
            "delta_mutation_score",
            "pre_line_coverage",
            "post_line_coverage",
            "delta_coverage",
            "effective_operator_ratio",
        )
    )
    per_method_fields: tuple[str, ...] = Field(
        default=(
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
        )
    )
    per_mutant_fields: tuple[str, ...] = Field(
        default=(
            "target_id",
            "arm",
            "mutant_id",
            "mutator",
            "pre_status",
            "post_status",
            "counts_as_killed",
            "counts_as_survived",
            "counts_in_fixed_denominator",
        )
    )
    sampled_method_fields: tuple[str, ...] = Field(
        default=("target_id", "class_name", "method_name", "method_signature", "order")
    )
    analysis_fields: tuple[str, ...] = Field(
        default=tuple(StudyAnalysisRowSchema.model_fields.keys())
    )


def build_method_archive_dirs(method_id: str) -> dict[str, str]:
    return {
        BASELINE_ARCHIVE_DIR: f"{method_id}/{BASELINE_ARCHIVE_DIR}",
        "M0": f"{method_id}/M0",
        "M2": f"{method_id}/M2",
        "M3": f"{method_id}/M3",
    }


def build_study_protocol() -> StudyProtocolSchema:
    return StudyProtocolSchema()
