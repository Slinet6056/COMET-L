from __future__ import annotations

import copy
import csv
import json
import logging
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Generic, Protocol, TypeVar, cast

from ..config.settings import Settings
from ..executor.pit_xml_parser import PitMutantRecord, parse_pit_mutations_xml
from ..knowledge.knowledge_base import KnowledgeBase, RAGKnowledgeBase, create_knowledge_base
from ..models import Mutant, MutationPatch
from ..store.knowledge_store import KnowledgeStore
from ..utils.method_keys import build_method_key, normalize_method_signature
from ..utils.sandbox import SandboxManager
from .study_protocol import (
    BASELINE_ARCHIVE_DIR,
    DEFAULT_STUDY_SAMPLE_SIZE,
    DEFAULT_STUDY_SEED,
    STUDY_ARM_NAMES,
    STUDY_OUTPUT_FILENAMES,
    StudyMutantStatus,
    StudyOutputSummarySchema,
    StudyPerMethodRowSchema,
    StudyPerMutantRecordSchema,
    StudySampledMethodSchema,
    build_method_archive_dirs,
    build_study_protocol,
    compute_delta_coverage,
    compute_delta_mutation_score,
    compute_effective_operator_ratio,
    compute_final_kill_rate,
)
from .study_sampling import (
    ClassMappingStore,
    PublicMethodExecutor,
    discover_cold_start_methods,
    freeze_sampled_methods,
    sample_cold_start_methods,
)

logger = logging.getLogger(__name__)

ArmResultT = TypeVar("ArmResultT")

_PIT_MUTATION_GOAL = "org.pitest:pitest-maven:mutationCoverage"
_PIT_MUTATIONS_XML_RELATIVE_PATH = Path("target") / "pit-reports" / "mutations.xml"


class StudyCoverageLike(Protocol):
    line_coverage_rate: float


class StudyMutantLike(Protocol):
    survived: bool
    evaluated_at: object | None


class StudyTestCaseLike(Protocol):
    methods: Sequence[object]


class StudyDatabaseProtocol(Protocol):
    def save_method_coverage(self, coverage: object, iteration: int) -> None: ...

    def save_mutant(self, mutant: Mutant) -> None: ...

    def save_test_case(self, test_case: object) -> None: ...

    def get_tests_by_target_method(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> list[StudyTestCaseLike]: ...

    def get_method_coverage(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> StudyCoverageLike | None: ...

    def get_mutants_by_method(
        self,
        class_name: str,
        method_name: str,
        status: str | None = "valid",
        method_signature: str | None = None,
    ) -> list[StudyMutantLike]: ...


class StudySandboxManagerProtocol(Protocol):
    def cleanup_sandbox(self, sandbox_id: str) -> None: ...

    def create_validation_sandbox(
        self,
        project_path: str,
        validation_id: str | None = None,
    ) -> str: ...

    def create_workspace_sandbox(self, project_path: str) -> str: ...

    def export_test_files_to_directory(
        self,
        sandbox_id: str,
        target_root: str | Path,
    ) -> list[Path]: ...


class StudyToolsProtocol(Protocol):
    project_path: str
    original_project_path: str
    db: StudyDatabaseProtocol
    sandbox_manager: StudySandboxManagerProtocol
    knowledge_base: KnowledgeBase | None
    state: object | None
    test_generator: object | None

    def generate_tests(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> Mapping[str, object]: ...

    def generate_mutants(
        self,
        class_name: str,
        method_name: str | None = None,
        method_signature: str | None = None,
    ) -> Mapping[str, object]: ...

    def refine_tests(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> Mapping[str, object]: ...

    def run_evaluation(self) -> Mapping[str, object]: ...


@dataclass(slots=True)
class FrozenStudyMethod:
    target_id: str
    class_name: str
    method_name: str
    method_signature: str | None


@dataclass(slots=True)
class StudyBaselineMetrics:
    pre_line_coverage: float = 0.0
    pre_test_count: int = 0
    pre_killed: int = 0
    baseline_total_mutants: int = 0
    archived_test_files: tuple[str, ...] = ()


@dataclass(slots=True)
class StudyBaselineResult:
    target_id: str
    class_name: str
    method_name: str
    method_signature: str | None
    archive_root: str
    baseline_dir: str
    archive_dirs: dict[str, str]
    status: str = "completed"
    error: str | None = None
    workspace_path: str | None = None
    metrics: StudyBaselineMetrics = field(default_factory=StudyBaselineMetrics)

    @property
    def success(self) -> bool:
        return self.status == "completed"


@dataclass(slots=True, frozen=True)
class StudyArmPaths:
    target_id: str
    arm: str
    state_root: Path
    output_root: Path
    sandbox_root: Path
    workspace_root: Path
    artifacts_root: Path

    @property
    def database_path(self) -> Path:
        return self.state_root / "comet.db"

    @property
    def knowledge_database_path(self) -> Path:
        return self.state_root / "knowledge.db"

    @property
    def vector_store_root(self) -> Path:
        return self.state_root / "chromadb"


@dataclass(slots=True, frozen=True)
class StudyArmContext:
    arm: str
    target_id: str
    config: Settings
    paths: StudyArmPaths
    sandbox_manager: SandboxManager

    @property
    def workspace_path(self) -> Path:
        return self.paths.workspace_root


@dataclass(slots=True, frozen=True)
class StudyArmExecutionResult(Generic[ArmResultT]):
    context: StudyArmContext
    value: ArmResultT | None = None
    error: Exception | None = None
    archived_test_count: int = 0

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(slots=True, frozen=True)
class StudyMutantSnapshot:
    mutant_id: str
    mutator: str
    status: str


@dataclass(slots=True, frozen=True)
class StudyPostEvaluation:
    post_line_coverage: float = 0.0
    mutants: tuple[StudyMutantSnapshot, ...] = ()


@dataclass(slots=True, frozen=True)
class StudyRunArtifacts:
    output_root: Path
    summary_path: Path
    per_method_path: Path
    per_mutant_path: Path
    sampled_methods_path: Path


class _StudyBaselineState:
    def __init__(self, target: Mapping[str, object | None], iteration: int = 0) -> None:
        self.current_target: dict[str, object | None] = dict(target)
        self.failed_targets: list[dict[str, object | None]] = []
        self.iteration: int = iteration

    def update_target(self, target: Mapping[str, object | None] | None) -> None:
        if target is None:
            self.current_target = {}
            return
        self.current_target = dict(target)


class StudyRunner:
    def __init__(
        self,
        workspace_project_path: str,
        artifacts_root: str,
        output_root: str | None = None,
        tools: object | None = None,
        database: object | None = None,
        sandbox_manager: object | None = None,
        settings: Settings | None = None,
        pit_runner: Callable[[str], Mapping[str, object]] | None = None,
        arm_names: Sequence[str] = STUDY_ARM_NAMES,
    ) -> None:
        self.workspace_project_path: str = str(Path(workspace_project_path).resolve())
        self.artifacts_root: Path = Path(artifacts_root)
        self.output_root: Path = (
            Path(output_root) if output_root is not None else self.artifacts_root
        )
        self.tools: object | None = tools
        self.db: object | None = database if database is not None else None
        if self.db is None and tools is not None:
            self.db = getattr(tools, "db", None)
        self.sandbox_manager: object | None = sandbox_manager
        if self.sandbox_manager is None and tools is not None:
            self.sandbox_manager = getattr(tools, "sandbox_manager", None)
        self.settings: Settings | None = settings
        self.pit_runner: Callable[[str], Mapping[str, object]] | None = pit_runner
        self.arm_names: tuple[str, ...] = tuple(arm_names)
        self._baseline_cache: dict[str, StudyBaselineResult] = {}

    def run_study(
        self,
        frozen_methods: str | Path | Sequence[StudySampledMethodSchema | Mapping[str, object]],
        arm_executor: Callable[
            [StudyArmContext, FrozenStudyMethod, Sequence[object], KnowledgeBase | None], object
        ],
        post_evaluator: Callable[
            [StudyArmContext, FrozenStudyMethod], StudyPostEvaluation | Mapping[str, object]
        ],
        config: Settings | None = None,
        seed: int = DEFAULT_STUDY_SEED,
    ) -> StudyRunArtifacts:
        protocol = build_study_protocol()
        output_root = self.output_root
        sampled_methods = self._load_sampled_methods(frozen_methods)
        sampled_methods_path = freeze_sampled_methods(output_root, sampled_methods)

        per_method_rows: list[StudyPerMethodRowSchema] = []
        per_mutant_records: list[StudyPerMutantRecordSchema] = []
        method_summaries: list[dict[str, object]] = []

        try:
            for sampled_method in sampled_methods:
                frozen_method = self._freeze_method(sampled_method.model_dump(mode="json"))
                baseline = self.ensure_shared_baseline(frozen_method)
                method_summary = self._build_method_summary(frozen_method, baseline)
                arm_statuses = cast(dict[str, str], method_summary["arm_statuses"])
                arm_errors = cast(dict[str, str | None], method_summary["arm_errors"])

                if not baseline.success:
                    self._mark_method_skipped_by_baseline(method_summary, baseline)
                    method_summaries.append(method_summary)
                    continue

                baseline_mutants = self._collect_method_mutant_snapshots(frozen_method)

                for arm in self.arm_names:
                    arm_context = self.prepare_arm_context(frozen_method.target_id, arm, config)
                    try:
                        guidance, knowledge_base = self._prepare_arm_inputs(
                            arm=arm,
                            method=frozen_method,
                            baseline=baseline,
                            context=arm_context,
                        )
                        _ = arm_executor(arm_context, frozen_method, guidance, knowledge_base)
                        post_evaluation = self._normalize_post_evaluation(
                            post_evaluator(arm_context, frozen_method)
                        )
                        row, mutant_records = self._build_method_artifacts(
                            method=frozen_method,
                            arm=arm,
                            baseline=baseline,
                            baseline_mutants=baseline_mutants,
                            post_evaluation=post_evaluation,
                        )
                        per_method_rows.append(row)
                        per_mutant_records.extend(mutant_records)
                        arm_statuses[arm] = "completed"
                        arm_errors[arm] = None
                        successful_arm_count = cast(int, method_summary["successful_arm_count"])
                        method_summary["successful_arm_count"] = successful_arm_count + 1
                    except Exception as error:
                        logger.exception(
                            f"研究方法 {frozen_method.target_id} 的 {arm} 臂执行失败: {error}"
                        )
                        arm_statuses[arm] = "failed"
                        arm_errors[arm] = str(error)
                        failed_arm_count = cast(int, method_summary["failed_arm_count"])
                        method_summary["failed_arm_count"] = failed_arm_count + 1
                    finally:
                        _ = arm_context.sandbox_manager.export_test_files_to_directory(
                            "workspace",
                            arm_context.paths.artifacts_root,
                        )

                method_summary["status"] = self._derive_method_status(method_summary)
                method_summaries.append(method_summary)
        finally:
            self.cleanup_shared_baselines()

        summary_payload = self._build_summary_payload(
            sampled_methods=sampled_methods,
            per_method_rows=per_method_rows,
            method_summaries=method_summaries,
            seed=seed,
        )
        summary_path = output_root / STUDY_OUTPUT_FILENAMES["summary"]
        per_method_path = output_root / STUDY_OUTPUT_FILENAMES["per_method"]
        per_mutant_path = output_root / STUDY_OUTPUT_FILENAMES["per_mutant"]
        output_root.mkdir(parents=True, exist_ok=True)
        _ = summary_path.write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._write_per_method_csv(per_method_path, protocol.per_method_fields, per_method_rows)
        self._write_per_mutant_jsonl(
            per_mutant_path,
            protocol.per_mutant_fields,
            per_mutant_records,
        )
        return StudyRunArtifacts(
            output_root=output_root,
            summary_path=summary_path,
            per_method_path=per_method_path,
            per_mutant_path=per_mutant_path,
            sampled_methods_path=sampled_methods_path,
        )

    def ensure_shared_baseline(
        self,
        method: FrozenStudyMethod | Mapping[str, object],
    ) -> StudyBaselineResult:
        frozen_method = self._freeze_method(method)
        cached = self._baseline_cache.get(frozen_method.target_id)
        if cached is not None:
            return cached

        result = self._generate_method_baseline(frozen_method)
        self._baseline_cache[frozen_method.target_id] = result
        return result

    def bootstrap_shared_baselines(
        self,
        methods: Sequence[FrozenStudyMethod | Mapping[str, object]],
    ) -> dict[str, StudyBaselineResult]:
        results: dict[str, StudyBaselineResult] = {}
        for method in methods:
            frozen_method = self._freeze_method(method)
            results[frozen_method.target_id] = self.ensure_shared_baseline(frozen_method)
        return results

    def cleanup_shared_baselines(self) -> None:
        if self.sandbox_manager is None:
            return
        sandbox_manager = cast(StudySandboxManagerProtocol, self.sandbox_manager)

        for result in self._baseline_cache.values():
            if not result.workspace_path:
                continue
            sandbox_id = Path(result.workspace_path).name
            try:
                sandbox_manager.cleanup_sandbox(sandbox_id)
            except Exception as error:
                logger.warning(f"清理 baseline 沙箱失败 {sandbox_id}: {error}")
            result.workspace_path = None

    def build_arm_scoped_paths(
        self,
        target_id: str,
        arm: str,
        config: Settings | None = None,
    ) -> StudyArmPaths:
        resolved_config = self._require_settings(config)
        state_root = resolved_config.resolve_state_root() / "study" / target_id / arm
        output_root = resolved_config.resolve_output_root() / "study" / target_id / arm
        sandbox_root = resolved_config.resolve_sandbox_root() / "study" / target_id / arm
        archive_dirs = build_method_archive_dirs(target_id)
        return StudyArmPaths(
            target_id=target_id,
            arm=arm,
            state_root=state_root,
            output_root=output_root,
            sandbox_root=sandbox_root,
            workspace_root=sandbox_root / "workspace",
            artifacts_root=self.artifacts_root / archive_dirs[arm],
        )

    def prepare_arm_context(
        self,
        target_id: str,
        arm: str,
        config: Settings | None = None,
    ) -> StudyArmContext:
        resolved_config = self._require_settings(config)
        scoped_paths = self.build_arm_scoped_paths(target_id, arm, resolved_config)
        scoped_config = resolved_config.model_copy(deep=True)
        bug_reports_dir = resolved_config.resolve_bug_reports_dir()
        if bug_reports_dir is not None:
            scoped_config.set_bug_reports_dir(bug_reports_dir)
        scoped_config.set_runtime_roots(
            state=scoped_paths.state_root,
            output=scoped_paths.output_root,
            sandbox=scoped_paths.sandbox_root,
        )
        scoped_config.ensure_directories()

        arm_sandbox_manager = SandboxManager(str(scoped_paths.sandbox_root))
        workspace_path = Path(
            arm_sandbox_manager.create_workspace_sandbox(self.workspace_project_path)
        ).resolve()
        return StudyArmContext(
            arm=arm,
            target_id=target_id,
            config=scoped_config,
            paths=StudyArmPaths(
                target_id=scoped_paths.target_id,
                arm=scoped_paths.arm,
                state_root=scoped_paths.state_root,
                output_root=scoped_paths.output_root,
                sandbox_root=scoped_paths.sandbox_root,
                workspace_root=workspace_path,
                artifacts_root=scoped_paths.artifacts_root,
            ),
            sandbox_manager=arm_sandbox_manager,
        )

    def run_target_arms(
        self,
        target_id: str,
        arm_executor: Callable[[StudyArmContext], ArmResultT],
        config: Settings | None = None,
    ) -> dict[str, StudyArmExecutionResult[ArmResultT]]:
        results: dict[str, StudyArmExecutionResult[ArmResultT]] = {}

        for arm in self.arm_names:
            context = self.prepare_arm_context(target_id, arm, config)
            value: ArmResultT | None = None
            error: Exception | None = None
            archived_files: list[Path] = []

            try:
                value = arm_executor(context)
            except Exception as exc:
                error = exc
                logger.exception(f"研究臂 {arm} 执行失败: {exc}")
            finally:
                archived_files = context.sandbox_manager.export_test_files_to_directory(
                    "workspace",
                    context.paths.artifacts_root,
                )

            results[arm] = StudyArmExecutionResult(
                context=context,
                value=value,
                error=error,
                archived_test_count=len(archived_files),
            )

        return results

    def build_m0_pit_guidance_from_baseline(
        self,
        method: FrozenStudyMethod | Mapping[str, object],
        baseline_workspace_path: str,
    ) -> tuple[dict[str, object], ...]:
        frozen_method = self._freeze_method(method)
        workspace_root = Path(baseline_workspace_path).resolve()
        pit_result = self._run_pit_mutation_coverage(str(workspace_root))
        if not pit_result.get("success", False):
            error = str(pit_result.get("error") or "unknown error")
            raise RuntimeError(f"M0 PIT 执行失败: {error}")

        mutations_xml_path = workspace_root / _PIT_MUTATIONS_XML_RELATIVE_PATH
        if not mutations_xml_path.exists():
            raise RuntimeError(f"M0 PIT 报告缺失: {mutations_xml_path}")

        pit_records = parse_pit_mutations_xml(mutations_xml_path)
        guidance = self._map_survived_pit_records_to_guidance(frozen_method, pit_records)
        return tuple(guidance)

    def run_guided_m2_m3_arms(
        self,
        method: FrozenStudyMethod | Mapping[str, object],
        arm_executor: Callable[
            [StudyArmContext, KnowledgeBase, tuple[StudyMutantLike, ...]], ArmResultT
        ],
        config: Settings | None = None,
    ) -> dict[str, StudyArmExecutionResult[ArmResultT]]:
        frozen_method = self._freeze_method(method)
        baseline = self.ensure_shared_baseline(frozen_method)
        if not baseline.success:
            raise RuntimeError(
                f"共享 baseline 失败: {frozen_method.target_id}: {baseline.error or 'unknown error'}"
            )

        guidance_mutants = self._collect_baseline_survived_mutants(frozen_method)
        results: dict[str, StudyArmExecutionResult[ArmResultT]] = {}

        for arm in ("M2", "M3"):
            context = self.prepare_arm_context(frozen_method.target_id, arm, config)
            knowledge_base = self.create_arm_knowledge_base(context)

            value: ArmResultT | None = None
            error: Exception | None = None
            archived_files: list[Path] = []

            try:
                value = arm_executor(context, knowledge_base, guidance_mutants)
            except Exception as exc:
                error = exc
                logger.exception(f"研究臂 {arm} 语义变异执行失败: {exc}")
            finally:
                archived_files = context.sandbox_manager.export_test_files_to_directory(
                    "workspace",
                    context.paths.artifacts_root,
                )

            results[arm] = StudyArmExecutionResult(
                context=context,
                value=value,
                error=error,
                archived_test_count=len(archived_files),
            )

        return results

    def create_arm_knowledge_base(self, context: StudyArmContext) -> KnowledgeBase:
        knowledge_config = context.config.knowledge.model_copy(deep=True)
        if context.arm == "M2":
            knowledge_config.enabled = False
        elif context.arm == "M3":
            knowledge_config.enabled = True

        store = KnowledgeStore(db_path=str(context.paths.knowledge_database_path))
        knowledge_base = create_knowledge_base(
            store=store,
            config=knowledge_config,
            llm_api_key=context.config.llm.api_key,
            vector_store_directory=str(context.paths.vector_store_root),
        )
        if context.arm == "M3":
            bug_reports_dir = context.config.resolve_bug_reports_dir()
            if bug_reports_dir is not None:
                try:
                    count = knowledge_base.index_bug_reports(str(bug_reports_dir))
                    logger.info(f"M3 已索引 {count} 个 Bug 报告: {bug_reports_dir}")
                except AttributeError:
                    logger.warning("研究臂知识库不支持 RAG 模式，跳过 Bug 报告索引")
                except Exception as error:
                    logger.warning(f"M3 索引 Bug 报告失败: {error}")

        return knowledge_base

    def _run_pit_mutation_coverage(self, project_path: str) -> dict[str, object]:
        if self.pit_runner is not None:
            return dict(self.pit_runner(project_path))

        settings = self._require_settings()
        pom_path = Path(project_path) / "pom.xml"
        cmd = [
            settings.execution.resolve_mvn_cmd(),
            "-q",
            "-f",
            str(pom_path),
            "test-compile",
            _PIT_MUTATION_GOAL,
            "-DskipTests=false",
        ]
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=settings.execution.build_target_subprocess_env(),
        )
        stdout = process.stdout or ""
        stderr = process.stderr or ""
        if process.returncode == 0:
            return {
                "success": True,
                "returncode": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }

        error = stderr.strip() or stdout.strip() or f"Maven PIT exit code {process.returncode}"
        return {
            "success": False,
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": error,
        }

    def _load_sampled_methods(
        self,
        frozen_methods: str | Path | Sequence[StudySampledMethodSchema | Mapping[str, object]],
    ) -> list[StudySampledMethodSchema]:
        if isinstance(frozen_methods, (str, Path)):
            payload_object: object = json.loads(Path(frozen_methods).read_text(encoding="utf-8"))
            if not isinstance(payload_object, list):
                raise RuntimeError("冻结清单格式错误: sampled_methods.json 顶层必须是数组")
            payload = payload_object
        else:
            payload = list(frozen_methods)

        methods = [StudySampledMethodSchema.model_validate(item) for item in payload]
        return sorted(methods, key=lambda method: method.order)

    def _build_method_summary(
        self,
        method: FrozenStudyMethod,
        baseline: StudyBaselineResult,
    ) -> dict[str, object]:
        return {
            "target_id": method.target_id,
            "class_name": method.class_name,
            "method_name": method.method_name,
            "method_signature": method.method_signature or "",
            "status": "pending",
            "baseline_status": baseline.status,
            "baseline_error": baseline.error,
            "successful_arm_count": 0,
            "failed_arm_count": 0,
            "skipped_arm_count": 0,
            "arm_statuses": {arm: "pending" for arm in self.arm_names},
            "arm_errors": {arm: None for arm in self.arm_names},
        }

    def _mark_method_skipped_by_baseline(
        self,
        method_summary: dict[str, object],
        baseline: StudyBaselineResult,
    ) -> None:
        method_summary["status"] = "failed"
        method_summary["baseline_status"] = baseline.status
        method_summary["baseline_error"] = baseline.error
        method_summary["skipped_arm_count"] = len(self.arm_names)
        arm_statuses = cast(dict[str, str], method_summary["arm_statuses"])
        arm_errors = cast(dict[str, str | None], method_summary["arm_errors"])
        for arm in self.arm_names:
            arm_statuses[arm] = "skipped"
            arm_errors[arm] = baseline.error

    def _derive_method_status(self, method_summary: Mapping[str, object]) -> str:
        successful_arm_count = self._to_int(method_summary.get("successful_arm_count", 0))
        failed_arm_count = self._to_int(method_summary.get("failed_arm_count", 0))
        skipped_arm_count = self._to_int(method_summary.get("skipped_arm_count", 0))
        if successful_arm_count == len(self.arm_names):
            return "completed"
        if successful_arm_count == 0 and (
            failed_arm_count > 0 or skipped_arm_count == len(self.arm_names)
        ):
            return "failed"
        return "partial_failed"

    def _prepare_arm_inputs(
        self,
        arm: str,
        method: FrozenStudyMethod,
        baseline: StudyBaselineResult,
        context: StudyArmContext,
    ) -> tuple[Sequence[object], KnowledgeBase | None]:
        if arm == "M0":
            if not baseline.workspace_path:
                raise RuntimeError(f"M0 缺少 baseline workspace: {method.target_id}")
            guidance = self.build_m0_pit_guidance_from_baseline(method, baseline.workspace_path)
            return guidance, None

        knowledge_base = self.create_arm_knowledge_base(context)
        guidance = self._collect_baseline_survived_mutants(method)
        return tuple(guidance), knowledge_base

    def _collect_method_mutant_snapshots(
        self,
        method: FrozenStudyMethod,
    ) -> tuple[StudyMutantSnapshot, ...]:
        db = self._require_db()
        mutants = db.get_mutants_by_method(
            method.class_name,
            method.method_name,
            status="valid",
            method_signature=method.method_signature,
        )
        snapshots = [self._normalize_mutant_snapshot(mutant) for mutant in mutants]
        snapshots.sort(key=lambda item: item.mutant_id)
        return tuple(snapshots)

    def _normalize_post_evaluation(
        self,
        evaluation: StudyPostEvaluation | Mapping[str, object],
    ) -> StudyPostEvaluation:
        if isinstance(evaluation, StudyPostEvaluation):
            return evaluation

        mutants_payload_object = evaluation.get("mutants") or evaluation.get("mutant_records") or ()
        mutants_payload: Sequence[object]
        if isinstance(mutants_payload_object, Sequence) and not isinstance(
            mutants_payload_object,
            (str, bytes, bytearray),
        ):
            mutants_payload = mutants_payload_object
        else:
            mutants_payload = ()

        line_coverage = evaluation.get("post_line_coverage")
        if line_coverage is None:
            line_coverage = evaluation.get(
                "line_coverage_rate", evaluation.get("line_coverage", 0.0)
            )
        mutants = tuple(self._normalize_mutant_snapshot(item) for item in mutants_payload)
        ordered_mutants = tuple(sorted(mutants, key=lambda item: item.mutant_id))
        return StudyPostEvaluation(
            post_line_coverage=self._to_float(line_coverage),
            mutants=ordered_mutants,
        )

    def _normalize_mutant_snapshot(self, mutant: object) -> StudyMutantSnapshot:
        if isinstance(mutant, StudyMutantSnapshot):
            return mutant

        if isinstance(mutant, Mapping):
            mutant_id = str(mutant.get("mutant_id") or mutant.get("id") or "")
            mutator = str(
                mutant.get("mutator")
                or mutant.get("operator")
                or self._resolve_mutator_from_patch(mutant.get("patch"))
                or ""
            )
            status = self._coerce_mutant_status(
                mutant.get("status"),
                mutant.get("survived"),
                mutant.get("evaluated_at"),
            )
            return StudyMutantSnapshot(mutant_id=mutant_id, mutator=mutator, status=status)

        mutant_id = str(getattr(mutant, "id", getattr(mutant, "mutant_id", "")))
        mutator = str(
            getattr(mutant, "mutator", getattr(mutant, "operator", ""))
            or self._resolve_mutator_from_patch(getattr(mutant, "patch", None))
            or ""
        )
        status = self._coerce_mutant_status(
            getattr(mutant, "status", None),
            getattr(mutant, "survived", None),
            getattr(mutant, "evaluated_at", None),
        )
        return StudyMutantSnapshot(mutant_id=mutant_id, mutator=mutator, status=status)

    def _resolve_mutator_from_patch(self, patch: object) -> str:
        if isinstance(patch, Mapping):
            return str(patch.get("mutator") or patch.get("operator") or "")
        return str(getattr(patch, "mutator", getattr(patch, "operator", "")) or "")

    def _coerce_mutant_status(
        self,
        raw_status: object,
        survived: object,
        evaluated_at: object,
    ) -> str:
        if isinstance(raw_status, StudyMutantStatus):
            return raw_status.value

        if isinstance(raw_status, str):
            normalized = raw_status.strip().upper()
            if normalized in StudyMutantStatus._value2member_map_:
                return normalized
            if normalized == "KILLED":
                return StudyMutantStatus.KILLED.value
            if normalized == "SURVIVED":
                return StudyMutantStatus.SURVIVED.value
            if normalized in {"NO_COVERAGE", "TIMED_OUT", "RUN_ERROR"}:
                return normalized

        if isinstance(survived, bool) and evaluated_at is not None:
            return StudyMutantStatus.SURVIVED.value if survived else StudyMutantStatus.KILLED.value

        return StudyMutantStatus.RUN_ERROR.value

    def _build_method_artifacts(
        self,
        method: FrozenStudyMethod,
        arm: str,
        baseline: StudyBaselineResult,
        baseline_mutants: Sequence[StudyMutantSnapshot],
        post_evaluation: StudyPostEvaluation,
    ) -> tuple[StudyPerMethodRowSchema, tuple[StudyPerMutantRecordSchema, ...]]:
        baseline_by_id = {mutant.mutant_id: mutant for mutant in baseline_mutants}
        post_by_id = {mutant.mutant_id: mutant for mutant in post_evaluation.mutants}
        all_mutant_ids = sorted(set(baseline_by_id) | set(post_by_id))

        post_killed = 0
        post_killed_operator_names: list[str] = []
        fixed_denominator_operator_names = [
            mutant.mutator for mutant in baseline_mutants if mutant.mutator.strip()
        ]
        mutant_records: list[StudyPerMutantRecordSchema] = []

        for mutant_id in all_mutant_ids:
            baseline_mutant = baseline_by_id.get(mutant_id)
            post_mutant = post_by_id.get(mutant_id)
            pre_status = (
                baseline_mutant.status
                if baseline_mutant is not None
                else StudyMutantStatus.RUN_ERROR.value
            )
            post_status = (
                post_mutant.status if post_mutant is not None else StudyMutantStatus.RUN_ERROR.value
            )
            counts_in_fixed_denominator = baseline_mutant is not None
            counts_as_killed = (
                counts_in_fixed_denominator and post_status == StudyMutantStatus.KILLED.value
            )
            counts_as_survived = post_status == StudyMutantStatus.SURVIVED.value
            if counts_as_killed:
                post_killed += 1
                if post_mutant is not None and post_mutant.mutator.strip():
                    post_killed_operator_names.append(post_mutant.mutator)

            mutator_name = ""
            if post_mutant is not None and post_mutant.mutator.strip():
                mutator_name = post_mutant.mutator
            elif baseline_mutant is not None:
                mutator_name = baseline_mutant.mutator

            mutant_records.append(
                StudyPerMutantRecordSchema(
                    target_id=method.target_id,
                    arm=arm,
                    mutant_id=mutant_id,
                    mutator=mutator_name,
                    pre_status=pre_status,
                    post_status=post_status,
                    counts_as_killed=counts_as_killed,
                    counts_as_survived=counts_as_survived,
                    counts_in_fixed_denominator=counts_in_fixed_denominator,
                )
            )

        fixed_mutant_count = baseline.metrics.baseline_total_mutants
        delta_mutation_score = compute_delta_mutation_score(
            baseline.metrics.pre_killed,
            post_killed,
            fixed_mutant_count,
        )
        delta_coverage = compute_delta_coverage(
            baseline.metrics.pre_line_coverage,
            post_evaluation.post_line_coverage,
        )
        final_kill_rate = compute_final_kill_rate(post_killed, fixed_mutant_count)
        effective_operator_ratio = compute_effective_operator_ratio(
            post_killed_operator_names,
            fixed_denominator_operator_names,
        )

        archive_dirs = baseline.archive_dirs
        row = StudyPerMethodRowSchema(
            target_id=method.target_id,
            arm=arm,
            class_name=method.class_name,
            method_name=method.method_name,
            method_signature=method.method_signature or "",
            archive_root=baseline.archive_root,
            baseline_dir=str(self.artifacts_root / archive_dirs[BASELINE_ARCHIVE_DIR]),
            m0_dir=str(self.artifacts_root / archive_dirs["M0"]),
            m2_dir=str(self.artifacts_root / archive_dirs["M2"]),
            m3_dir=str(self.artifacts_root / archive_dirs["M3"]),
            pre_line_coverage=baseline.metrics.pre_line_coverage,
            post_line_coverage=post_evaluation.post_line_coverage,
            pre_killed=baseline.metrics.pre_killed,
            post_killed=post_killed,
            fixed_mutant_count=fixed_mutant_count,
            delta_mutation_score=delta_mutation_score,
            delta_coverage=delta_coverage,
            final_kill_rate=final_kill_rate,
            effective_operator_ratio=effective_operator_ratio,
        )
        return row, tuple(mutant_records)

    def _build_summary_payload(
        self,
        sampled_methods: Sequence[StudySampledMethodSchema],
        per_method_rows: Sequence[StudyPerMethodRowSchema],
        method_summaries: Sequence[Mapping[str, object]],
        seed: int,
    ) -> dict[str, object]:
        project_averages: dict[str, dict[str, object]] = {}
        for arm in self.arm_names:
            arm_rows = [row for row in per_method_rows if row.arm == arm]
            baseline_total_mutants = sum(row.fixed_mutant_count for row in arm_rows)
            pre_killed = sum(row.pre_killed for row in arm_rows)
            post_killed = sum(row.post_killed for row in arm_rows)
            row_count = len(arm_rows)
            summary = StudyOutputSummarySchema(
                arm=arm,
                baseline_arm=BASELINE_ARCHIVE_DIR,
                sample_size=len(sampled_methods),
                seed=seed,
                method_count=row_count,
                baseline_total_mutants=baseline_total_mutants,
                pre_killed=pre_killed,
                post_killed=post_killed,
                final_kill_rate=compute_final_kill_rate(post_killed, baseline_total_mutants),
                delta_mutation_score=compute_delta_mutation_score(
                    pre_killed,
                    post_killed,
                    baseline_total_mutants,
                ),
                pre_line_coverage=self._average([row.pre_line_coverage for row in arm_rows]),
                post_line_coverage=self._average([row.post_line_coverage for row in arm_rows]),
                delta_coverage=self._average([row.delta_coverage for row in arm_rows]),
                effective_operator_ratio=self._average(
                    [row.effective_operator_ratio for row in arm_rows]
                ),
            )
            project_averages[arm] = summary.model_dump(mode="json")

        successful_method_count = sum(
            1 for item in method_summaries if item["status"] == "completed"
        )
        partial_failure_method_count = sum(
            1 for item in method_summaries if item["status"] == "partial_failed"
        )
        failed_method_count = sum(1 for item in method_summaries if item["status"] == "failed")

        return {
            "arms": list(self.arm_names),
            "baseline_arm": BASELINE_ARCHIVE_DIR,
            "sample_size": len(sampled_methods),
            "seed": seed,
            "method_count": len(sampled_methods),
            "successful_method_count": successful_method_count,
            "partial_failure_method_count": partial_failure_method_count,
            "failed_method_count": failed_method_count,
            "successful_arm_count": sum(
                self._to_int(item.get("successful_arm_count", 0)) for item in method_summaries
            ),
            "failed_arm_count": sum(
                self._to_int(item.get("failed_arm_count", 0)) for item in method_summaries
            ),
            "skipped_arm_count": sum(
                self._to_int(item.get("skipped_arm_count", 0)) for item in method_summaries
            ),
            "project_averages": project_averages,
            "methods": [dict(item) for item in method_summaries],
        }

    def _write_per_method_csv(
        self,
        output_path: Path,
        fieldnames: Sequence[str],
        rows: Sequence[StudyPerMethodRowSchema],
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
            writer.writeheader()
            for row in rows:
                writer.writerow(row.model_dump(mode="json"))

    def _write_per_mutant_jsonl(
        self,
        output_path: Path,
        fieldnames: Sequence[str],
        rows: Sequence[StudyPerMutantRecordSchema],
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                payload = cast(dict[str, object], row.model_dump(mode="json"))
                ordered_payload = {field: payload[field] for field in fieldnames}
                handle.write(json.dumps(ordered_payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _average(values: Sequence[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)

    @staticmethod
    def _to_float(value: object) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return float(value)
        return 0.0

    @staticmethod
    def _to_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            return int(value)
        return 0

    def _map_survived_pit_records_to_guidance(
        self,
        method: FrozenStudyMethod,
        pit_records: Sequence[PitMutantRecord],
    ) -> list[dict[str, object]]:
        guidance_mutants: list[dict[str, object]] = []
        for record in pit_records:
            if record.status != StudyMutantStatus.SURVIVED.value:
                continue
            if not self._is_pit_record_matching_method(record, method):
                continue
            guidance_mutants.append(self._build_pit_guidance_mutant(record))

        guidance_mutants.sort(key=lambda item: str(item.get("id") or ""))
        return guidance_mutants

    def _is_pit_record_matching_method(
        self,
        record: PitMutantRecord,
        method: FrozenStudyMethod,
    ) -> bool:
        if record.method_name != method.method_name:
            return False

        if method.method_signature and record.method_signature != method.method_signature:
            return False

        if record.class_name == method.class_name:
            return True

        record_simple_name = record.class_name.split(".")[-1]
        method_simple_name = method.class_name.split(".")[-1]
        return record_simple_name == method_simple_name

    def _build_pit_guidance_mutant(self, record: PitMutantRecord) -> dict[str, object]:
        operator_tag = self._extract_operator_tag(record.mutator)
        mutated_code = (
            f"// PIT operator: {operator_tag}\n"
            f"// mutator: {record.mutator}\n"
            f"// location: {record.class_name}.{record.method_name}:{record.line_number}"
        )
        return {
            "id": record.mutant_id,
            "status": record.status,
            "mutator": record.mutator,
            "operator": operator_tag,
            "patch": {
                "file_path": f"{record.class_name.replace('.', '/')}.java",
                "line_start": record.line_number,
                "line_end": record.line_number,
                "original_code": "",
                "mutated_code": mutated_code,
                "mutator": record.mutator,
                "operator": operator_tag,
            },
        }

    def _extract_operator_tag(self, mutator: str) -> str:
        if not mutator:
            return "UNKNOWN"
        return mutator.split(".")[-1]

    def _collect_baseline_survived_mutants(
        self,
        method: FrozenStudyMethod,
    ) -> tuple[StudyMutantLike, ...]:
        db = self._require_db()
        baseline_mutants = db.get_mutants_by_method(
            method.class_name,
            method.method_name,
            status="valid",
            method_signature=method.method_signature,
        )
        survived_mutants = [
            mutant
            for mutant in baseline_mutants
            if mutant.survived and mutant.evaluated_at is not None
        ]
        return tuple(survived_mutants)

    def _generate_method_baseline(self, method: FrozenStudyMethod) -> StudyBaselineResult:
        db = self._require_db()
        sandbox_manager = self._require_sandbox_manager()
        tools = self._require_tools()

        archive_dirs = build_method_archive_dirs(method.target_id)
        archive_root = self.artifacts_root / method.target_id
        baseline_dir = self.artifacts_root / archive_dirs[BASELINE_ARCHIVE_DIR]
        baseline_dir.mkdir(parents=True, exist_ok=True)

        workspace_path = sandbox_manager.create_validation_sandbox(
            self.workspace_project_path,
            validation_id=f"study_baseline_{self._sanitize_target_id(method.target_id)}",
        )
        sandbox_id = Path(workspace_path).name

        result = StudyBaselineResult(
            target_id=method.target_id,
            class_name=method.class_name,
            method_name=method.method_name,
            method_signature=method.method_signature,
            archive_root=str(archive_root),
            baseline_dir=str(baseline_dir),
            archive_dirs=archive_dirs,
            workspace_path=workspace_path,
        )

        try:
            scoped_tools = self._build_scoped_tools(
                tools, db, sandbox_manager, method, workspace_path
            )

            generation_result = scoped_tools.generate_tests(
                method.class_name,
                method.method_name,
                method.method_signature,
            )
            test_cases = self._get_test_cases(db, method)
            if not test_cases:
                message = str(generation_result.get("error") or "baseline 没有生成任何测试")
                raise RuntimeError(message)

            mutant_generation_result = scoped_tools.generate_mutants(
                method.class_name,
                method.method_name,
                method.method_signature,
            )
            self._raise_for_failed_mutant_generation(mutant_generation_result)

            evaluation_result = scoped_tools.run_evaluation()
            self._raise_for_failed_evaluation(evaluation_result)

            exported_files = sandbox_manager.export_test_files_to_directory(
                sandbox_id, baseline_dir
            )
            if not exported_files:
                raise RuntimeError("baseline 测试工件导出为空")

            result.metrics = self._collect_baseline_metrics(db, method, exported_files)
            return result
        except Exception as error:
            result.status = "failed"
            result.error = str(error)
            result.workspace_path = None
            sandbox_manager.cleanup_sandbox(sandbox_id)
            return result

    def _collect_baseline_metrics(
        self,
        db: StudyDatabaseProtocol,
        method: FrozenStudyMethod,
        exported_files: Sequence[Path],
    ) -> StudyBaselineMetrics:
        test_cases = self._get_test_cases(db, method)
        mutants = db.get_mutants_by_method(
            method.class_name,
            method.method_name,
            status="valid",
            method_signature=method.method_signature,
        )
        coverage = self._get_method_coverage(db, method)

        pre_line_coverage = 0.0
        if coverage is not None:
            pre_line_coverage = float(coverage.line_coverage_rate)

        pre_test_count = sum(len(test_case.methods) for test_case in test_cases)
        pre_killed = sum(
            1 for mutant in mutants if mutant.evaluated_at is not None and not mutant.survived
        )

        return StudyBaselineMetrics(
            pre_line_coverage=pre_line_coverage,
            pre_test_count=pre_test_count,
            pre_killed=pre_killed,
            baseline_total_mutants=len(mutants),
            archived_test_files=tuple(str(path) for path in exported_files),
        )

    def _get_test_cases(
        self,
        db: StudyDatabaseProtocol,
        method: FrozenStudyMethod,
    ) -> list[StudyTestCaseLike]:
        return db.get_tests_by_target_method(
            method.class_name,
            method.method_name,
            method.method_signature,
        )

    def _get_method_coverage(
        self,
        db: StudyDatabaseProtocol,
        method: FrozenStudyMethod,
    ) -> StudyCoverageLike | None:
        coverage = db.get_method_coverage(
            method.class_name,
            method.method_name,
            method.method_signature,
        )
        if coverage is not None or "." not in method.class_name:
            return coverage

        simple_class_name = method.class_name.split(".")[-1]
        return db.get_method_coverage(
            simple_class_name,
            method.method_name,
            method.method_signature,
        )

    def _build_scoped_tools(
        self,
        tools: StudyToolsProtocol,
        db: StudyDatabaseProtocol,
        sandbox_manager: StudySandboxManagerProtocol,
        method: FrozenStudyMethod,
        workspace_path: str,
    ) -> StudyToolsProtocol:
        scoped_tools = copy.copy(tools)
        scoped_tools.project_path = workspace_path
        scoped_tools.original_project_path = self.workspace_project_path
        scoped_tools.db = db
        scoped_tools.sandbox_manager = sandbox_manager

        current_target = {
            "target_id": method.target_id,
            "class_name": method.class_name,
            "method_name": method.method_name,
            "method_signature": method.method_signature,
        }

        base_iteration = 0
        if tools.state is not None:
            iteration = getattr(tools.state, "iteration", 0)
            if isinstance(iteration, int):
                base_iteration = iteration
        scoped_tools.state = _StudyBaselineState(current_target, iteration=base_iteration)
        return scoped_tools

    def _freeze_method(self, method: FrozenStudyMethod | Mapping[str, object]) -> FrozenStudyMethod:
        if isinstance(method, FrozenStudyMethod):
            return method

        class_name = str(method["class_name"])
        method_name = str(method["method_name"])
        raw_signature = method.get("method_signature")
        method_signature = normalize_method_signature(
            str(raw_signature) if isinstance(raw_signature, str) else None
        )
        target_id = str(
            method.get("target_id") or build_method_key(class_name, method_name, method_signature)
        )
        return FrozenStudyMethod(
            target_id=target_id,
            class_name=class_name,
            method_name=method_name,
            method_signature=method_signature,
        )

    def _raise_for_failed_evaluation(self, evaluation_result: Mapping[str, object]) -> None:
        error = evaluation_result.get("error")
        if error:
            raise RuntimeError(str(error))

        status = str(evaluation_result.get("status") or "")
        reason = str(evaluation_result.get("reason") or "")
        if status == "blocked" or status == "empty" or reason in {"no_tests", "no_mutants"}:
            message = (
                evaluation_result.get("message")
                or evaluation_result.get("error")
                or "baseline evaluation 失败"
            )
            raise RuntimeError(str(message))

    def _raise_for_failed_mutant_generation(self, generation_result: Mapping[str, object]) -> None:
        error = generation_result.get("error")
        if error:
            raise RuntimeError(str(error))

        status = str(generation_result.get("status") or "")
        reason = str(generation_result.get("reason") or "")
        if status == "empty" or reason == "no_mutants":
            message = (
                generation_result.get("message")
                or generation_result.get("error")
                or "baseline 未生成任何变异体"
            )
            raise RuntimeError(str(message))

    def _require_db(self) -> StudyDatabaseProtocol:
        if self.db is None:
            raise RuntimeError("StudyRunner 缺少 database")
        return cast(StudyDatabaseProtocol, self.db)

    def _require_sandbox_manager(self) -> StudySandboxManagerProtocol:
        if self.sandbox_manager is None:
            raise RuntimeError("StudyRunner 缺少 sandbox_manager")
        return cast(StudySandboxManagerProtocol, self.sandbox_manager)

    def _require_tools(self) -> StudyToolsProtocol:
        if self.tools is None:
            raise RuntimeError("StudyRunner 缺少 tools")
        return cast(StudyToolsProtocol, self.tools)

    def _require_settings(self, config: Settings | None = None) -> Settings:
        if config is not None:
            return config
        if self.settings is None:
            raise RuntimeError("StudyRunner 缺少 settings")
        return self.settings

    @staticmethod
    def _sanitize_target_id(target_id: str) -> str:
        return (
            target_id.replace("/", "_")
            .replace("\\", "_")
            .replace(":", "_")
            .replace("#", "_")
            .replace(".", "_")
        )


def run_default_study(
    *,
    project_path: str,
    output_dir: str | Path,
    sample_size: int = DEFAULT_STUDY_SAMPLE_SIZE,
    seed: int = DEFAULT_STUDY_SEED,
    components: Mapping[str, object],
    settings: Settings,
    system_initializer: Callable[..., Mapping[str, object]],
) -> StudyRunArtifacts:
    resolved_project_path = str(Path(project_path).expanduser().resolve())
    resolved_output_dir = Path(output_dir).expanduser().resolve()
    runner = StudyRunner(
        workspace_project_path=resolved_project_path,
        artifacts_root=str(resolved_output_dir / "artifacts"),
        output_root=str(resolved_output_dir),
        tools=components.get("tools"),
        database=components.get("db"),
        sandbox_manager=components.get("sandbox_manager"),
        settings=settings,
    )

    java_executor = components.get("java_executor")
    if java_executor is None:
        raise RuntimeError("研究运行缺少 java_executor")

    db = runner._require_db()
    discovered_methods = discover_cold_start_methods(
        resolved_project_path,
        java_executor=cast(PublicMethodExecutor, java_executor),
        db=cast(ClassMappingStore, cast(object, db)),
        min_method_lines=settings.evolution.min_method_lines,
    )
    sampled_methods = sample_cold_start_methods(
        discovered_methods,
        sample_size=sample_size,
        seed=seed,
    )
    if not sampled_methods:
        raise RuntimeError("未找到可用于研究的公共方法，请检查项目源码与最小方法行数配置")

    post_evaluations: dict[tuple[str, str], StudyPostEvaluation] = {}

    def execute_arm(
        context: StudyArmContext,
        method: FrozenStudyMethod,
        guidance: Sequence[object],
        knowledge_base: KnowledgeBase | None,
    ) -> None:
        arm_components = system_initializer(context.config, parallel_mode=False)
        post_evaluations[(method.target_id, context.arm)] = _execute_default_study_arm(
            runner=runner,
            arm_components=arm_components,
            method=method,
            context=context,
            guidance=guidance,
            knowledge_base=knowledge_base,
        )

    def post_evaluator(context: StudyArmContext, method: FrozenStudyMethod) -> StudyPostEvaluation:
        return post_evaluations[(method.target_id, context.arm)]

    return runner.run_study(
        sampled_methods,
        arm_executor=execute_arm,
        post_evaluator=post_evaluator,
        config=settings,
        seed=seed,
    )


def _execute_default_study_arm(
    *,
    runner: StudyRunner,
    arm_components: Mapping[str, object],
    method: FrozenStudyMethod,
    context: StudyArmContext,
    guidance: Sequence[object],
    knowledge_base: KnowledgeBase | None,
) -> StudyPostEvaluation:
    raw_arm_db = arm_components.get("db")
    raw_arm_tools = arm_components.get("tools")
    if raw_arm_db is None or raw_arm_tools is None:
        raise RuntimeError(f"研究臂 {context.arm} 初始化失败：缺少 db 或 tools")
    arm_db = cast(StudyDatabaseProtocol, raw_arm_db)
    arm_tools = cast(StudyToolsProtocol, raw_arm_tools)

    project_scanner = arm_components.get("project_scanner")
    if project_scanner is not None:
        scan_project = getattr(project_scanner, "scan_project", None)
        if callable(scan_project):
            scan_project(str(context.workspace_path), use_cache=True)

    current_target = {
        "target_id": method.target_id,
        "class_name": method.class_name,
        "method_name": method.method_name,
        "method_signature": method.method_signature,
    }
    arm_tools.project_path = str(context.workspace_path)
    arm_tools.original_project_path = runner.workspace_project_path
    arm_tools.db = arm_db
    arm_tools.sandbox_manager = context.sandbox_manager
    arm_tools.state = _StudyBaselineState(current_target)

    if knowledge_base is not None:
        arm_tools.knowledge_base = knowledge_base
        test_generator = arm_components.get("test_generator")
        if test_generator is not None:
            arm_tools.test_generator = test_generator
            setattr(test_generator, "kb", knowledge_base)
            setattr(test_generator, "_is_rag_enabled", isinstance(knowledge_base, RAGKnowledgeBase))

    baseline_db = runner._require_db()
    _seed_arm_test_cases(arm_db, baseline_db, method)
    _seed_arm_coverage(arm_db, runner, baseline_db, method)

    if context.arm == "M0":
        guidance_mutants = _build_guidance_mutants(method, guidance)
        for mutant in guidance_mutants:
            arm_db.save_mutant(mutant)
        _run_arm_refinement(arm_tools, method, context.arm)
        for mutant in guidance_mutants:
            arm_db.save_mutant(mutant.model_copy(update={"status": "outdated"}))
        _seed_arm_baseline_mutants(arm_db, baseline_db, method)
    else:
        _seed_arm_baseline_mutants(arm_db, baseline_db, method)
        _run_arm_refinement(arm_tools, method, context.arm)

    evaluation_result = arm_tools.run_evaluation()
    runner._raise_for_failed_evaluation(evaluation_result)
    return _collect_post_evaluation_from_db(arm_db, runner, method)


def _run_arm_refinement(
    arm_tools: StudyToolsProtocol,
    method: FrozenStudyMethod,
    arm: str,
) -> None:
    refine_method = getattr(arm_tools, "refine_tests", None)
    if not callable(refine_method):
        raise RuntimeError(f"研究臂 {arm} 缺少 refine_tests 能力")

    result = refine_method(method.class_name, method.method_name, method.method_signature)
    refined_count = 0
    if isinstance(result, Mapping):
        refined_value = result.get("refined", 0)
        if isinstance(refined_value, (int, float)):
            refined_count = int(refined_value)
        elif isinstance(refined_value, str):
            refined_count = int(refined_value)
        error = result.get("error")
        if error:
            raise RuntimeError(f"{arm} 测试改进失败: {error}")
    if refined_count <= 0:
        raise RuntimeError(f"{arm} 未生成任何改进测试")


def _seed_arm_test_cases(
    arm_db: StudyDatabaseProtocol,
    baseline_db: StudyDatabaseProtocol,
    method: FrozenStudyMethod,
) -> None:
    baseline_tests = baseline_db.get_tests_by_target_method(
        method.class_name,
        method.method_name,
        method.method_signature,
    )
    if not baseline_tests:
        raise RuntimeError(f"共享 baseline 缺少测试用例: {method.target_id}")
    for test_case in baseline_tests:
        save_test_case = getattr(arm_db, "save_test_case", None)
        if not callable(save_test_case):
            raise RuntimeError("研究臂 database 不支持保存测试用例")
        save_test_case(test_case)


def _seed_arm_coverage(
    arm_db: StudyDatabaseProtocol,
    runner: StudyRunner,
    baseline_db: StudyDatabaseProtocol,
    method: FrozenStudyMethod,
) -> None:
    coverage = runner._get_method_coverage(baseline_db, method)
    if coverage is None:
        return
    save_method_coverage = getattr(arm_db, "save_method_coverage", None)
    if callable(save_method_coverage):
        save_method_coverage(coverage, 0)


def _seed_arm_baseline_mutants(
    arm_db: StudyDatabaseProtocol,
    baseline_db: StudyDatabaseProtocol,
    method: FrozenStudyMethod,
) -> None:
    baseline_mutants = baseline_db.get_mutants_by_method(
        method.class_name,
        method.method_name,
        status="valid",
        method_signature=method.method_signature,
    )
    save_mutant = getattr(arm_db, "save_mutant", None)
    if not callable(save_mutant):
        raise RuntimeError("研究臂 database 不支持保存变异体")
    for mutant in baseline_mutants:
        save_mutant(mutant)


def _build_guidance_mutants(
    method: FrozenStudyMethod,
    guidance: Sequence[object],
) -> tuple[Mutant, ...]:
    guidance_mutants: list[Mutant] = []
    timestamp = datetime.now()
    for index, item in enumerate(guidance):
        if isinstance(item, Mutant):
            guidance_mutants.append(
                item.model_copy(
                    update={"status": "valid", "survived": True, "evaluated_at": timestamp}
                )
            )
            continue

        payload: Mapping[str, object] = (
            cast(Mapping[str, object], item) if isinstance(item, Mapping) else {}
        )
        raw_patch = payload.get("patch")
        patch_payload: Mapping[str, object] = (
            cast(Mapping[str, object], raw_patch) if isinstance(raw_patch, Mapping) else {}
        )
        raw_line_start = patch_payload.get("line_start")
        raw_line_end = patch_payload.get("line_end")
        line_start = int(raw_line_start) if isinstance(raw_line_start, (int, float, str)) else 1
        line_end = int(raw_line_end) if isinstance(raw_line_end, (int, float, str)) else line_start
        patch = MutationPatch(
            file_path=str(
                patch_payload.get("file_path") or f"{method.class_name.replace('.', '/')}.java"
            ),
            line_start=line_start,
            line_end=line_end,
            original_code=str(patch_payload.get("original_code") or ""),
            mutated_code=str(
                patch_payload.get("mutated_code")
                or payload.get("mutator")
                or payload.get("operator")
                or "guidance mutant"
            ),
            mutator=str(
                patch_payload.get("mutator")
                or payload.get("mutator")
                or patch_payload.get("operator")
                or ""
            )
            or None,
            operator=str(
                patch_payload.get("operator")
                or payload.get("operator")
                or patch_payload.get("mutator")
                or payload.get("mutator")
                or ""
            )
            or None,
        )
        guidance_mutants.append(
            Mutant(
                id=str(
                    payload.get("id")
                    or payload.get("mutant_id")
                    or f"{method.target_id}-guidance-{index}"
                ),
                class_name=method.class_name,
                method_name=method.method_name,
                method_signature=method.method_signature,
                patch=patch,
                status="valid",
                survived=True,
                evaluated_at=timestamp,
            )
        )
    return tuple(guidance_mutants)


def _collect_post_evaluation_from_db(
    arm_db: StudyDatabaseProtocol,
    runner: StudyRunner,
    method: FrozenStudyMethod,
) -> StudyPostEvaluation:
    coverage = runner._get_method_coverage(arm_db, method)
    mutants = arm_db.get_mutants_by_method(
        method.class_name,
        method.method_name,
        status="valid",
        method_signature=method.method_signature,
    )
    snapshots = tuple(
        sorted(
            (runner._normalize_mutant_snapshot(mutant) for mutant in mutants),
            key=lambda item: item.mutant_id,
        )
    )
    return StudyPostEvaluation(
        post_line_coverage=float(coverage.line_coverage_rate) if coverage is not None else 0.0,
        mutants=snapshots,
    )
