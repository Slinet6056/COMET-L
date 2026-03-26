from __future__ import annotations

import csv
import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from ..executor.coverage_parser import CoverageParser
from ..executor.pit_xml_parser import parse_pit_mutations_xml
from ..utils import SandboxManager
from .study_protocol import (
    STUDY_ARCHIVE_DIRS,
    STUDY_ARM_NAMES,
    STUDY_OUTPUT_FILENAMES,
    StudyAnalysisRowSchema,
)

logger = logging.getLogger(__name__)

_DEFAULT_ANALYSIS_SANDBOX_DIR = ".study-analysis-sandbox"
_JACOCO_XML_RELATIVE_PATH = Path("target") / "site" / "jacoco" / "jacoco.xml"


@dataclass(slots=True)
class StudyAnalysisTarget:
    target_id: str
    class_name: str
    method_name: str
    method_signature: str


class StudyAnalysisError(RuntimeError):
    pass


def _parse_percentage_text(value: str) -> float | None:
    normalized = value.strip()
    if not normalized:
        return None

    if normalized.endswith("%"):
        normalized = normalized[:-1]

    try:
        return float(normalized) / 100.0
    except ValueError:
        return None


def _extract_pit_percentage_from_html(html: str, label: str) -> float | None:
    escaped_label = re.escape(label)
    patterns = (
        rf"{escaped_label}</td>\s*<td[^>]*>\s*([0-9]+(?:\.[0-9]+)?)%",
        rf"{escaped_label}</span>\s*<span[^>]*>\s*([0-9]+(?:\.[0-9]+)?)%",
        rf"{escaped_label}[^0-9]*([0-9]+(?:\.[0-9]+)?)%",
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match is None:
            continue
        value = _parse_percentage_text(match.group(1))
        if value is not None:
            return value
    return None


class StudyReplayAnalyzer:
    def __init__(
        self,
        *,
        settings: Settings,
        sandbox_manager: SandboxManager | None = None,
        maven_runner: Callable[[Path, Settings], Mapping[str, object]] | None = None,
    ) -> None:
        self.settings = settings
        self.coverage_parser = CoverageParser()
        sandbox_root = settings.resolve_output_root() / _DEFAULT_ANALYSIS_SANDBOX_DIR
        self.sandbox_manager = sandbox_manager or SandboxManager(str(sandbox_root))
        self.maven_runner = maven_runner or self._run_maven_replay

    def analyze(
        self,
        *,
        project_path: str | Path,
        study_results_path: str | Path,
        output_csv: str | Path | None = None,
        max_workers: int | None = None,
    ) -> Path:
        resolved_project_path = Path(project_path).expanduser().resolve()
        resolved_results_path = Path(study_results_path).expanduser().resolve()
        self._validate_project_path(resolved_project_path)
        self._validate_results_path(resolved_results_path)

        summary_path = resolved_results_path / STUDY_OUTPUT_FILENAMES["summary"]
        summary = self._load_summary(summary_path)
        targets = self._select_completed_targets(summary)
        if not targets:
            raise StudyAnalysisError("summary.json 中没有三臂全部成功的目标，无法生成分析结果")

        output_path = (
            Path(output_csv).expanduser().resolve()
            if output_csv is not None
            else resolved_results_path / STUDY_OUTPUT_FILENAMES["analysis_metrics"]
        )
        tasks: list[tuple[StudyAnalysisTarget, str]] = [
            (target, arm) for target in targets for arm in STUDY_ARCHIVE_DIRS
        ]
        rows = self._collect_rows(
            tasks=tasks,
            project_path=resolved_project_path,
            study_results_path=resolved_results_path,
            max_workers=max_workers,
        )

        self._write_csv(output_path, rows)
        logger.info("study 分析完成，输出 CSV: %s", output_path)
        return output_path

    def _collect_rows(
        self,
        *,
        tasks: Sequence[tuple[StudyAnalysisTarget, str]],
        project_path: Path,
        study_results_path: Path,
        max_workers: int | None,
    ) -> list[StudyAnalysisRowSchema]:
        if not tasks:
            return []

        resolved_max_workers = self._resolve_max_workers(max_workers, len(tasks))
        if resolved_max_workers <= 1:
            return [
                self._analyze_target_arm(
                    project_path=project_path,
                    study_results_path=study_results_path,
                    target=target,
                    arm=arm,
                )
                for target, arm in tasks
            ]

        logger.info(
            "study 分析并行执行开始: 任务数=%s, 并发数=%s", len(tasks), resolved_max_workers
        )
        results_by_index: dict[int, StudyAnalysisRowSchema] = {}
        with ThreadPoolExecutor(max_workers=resolved_max_workers) as executor:
            future_map = {
                executor.submit(
                    self._analyze_target_arm,
                    project_path=project_path,
                    study_results_path=study_results_path,
                    target=target,
                    arm=arm,
                ): index
                for index, (target, arm) in enumerate(tasks)
            }
            for future in as_completed(future_map):
                task_index = future_map[future]
                results_by_index[task_index] = future.result()

        return [results_by_index[index] for index in range(len(tasks))]

    def _resolve_max_workers(self, requested_max_workers: int | None, task_count: int) -> int:
        if task_count <= 0:
            return 1
        if requested_max_workers is not None:
            return max(1, min(requested_max_workers, task_count))

        configured_max_workers = self.settings.preprocessing.max_workers
        if configured_max_workers is not None:
            return max(1, min(configured_max_workers, task_count))

        cpu_count = os.cpu_count() or 1
        return max(1, min(cpu_count, task_count))

    def _validate_project_path(self, project_path: Path) -> None:
        if not project_path.exists():
            raise FileNotFoundError(f"目标项目不存在: {project_path}")
        if not project_path.is_dir():
            raise NotADirectoryError(f"目标项目不是目录: {project_path}")
        if not (project_path / "pom.xml").exists():
            raise FileNotFoundError(f"目标项目缺少 pom.xml: {project_path}")

    def _validate_results_path(self, study_results_path: Path) -> None:
        if not study_results_path.exists():
            raise FileNotFoundError(f"study 结果目录不存在: {study_results_path}")
        if not study_results_path.is_dir():
            raise NotADirectoryError(f"study 结果路径不是目录: {study_results_path}")
        summary_path = study_results_path / STUDY_OUTPUT_FILENAMES["summary"]
        if not summary_path.exists():
            raise FileNotFoundError(f"study 结果缺少 summary.json: {summary_path}")

    def _load_summary(self, summary_path: Path) -> dict[str, object]:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise StudyAnalysisError("summary.json 顶层格式错误：必须是对象")
        return payload

    def _select_completed_targets(self, summary: Mapping[str, object]) -> list[StudyAnalysisTarget]:
        methods = summary.get("methods")
        if not isinstance(methods, list):
            raise StudyAnalysisError("summary.json 缺少 methods 数组")

        targets: list[StudyAnalysisTarget] = []
        for item in methods:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("status") or "") != "completed":
                continue
            if str(item.get("baseline_status") or "") != "completed":
                continue
            arm_statuses = item.get("arm_statuses")
            if not isinstance(arm_statuses, Mapping):
                continue
            if any(str(arm_statuses.get(arm) or "") != "completed" for arm in STUDY_ARM_NAMES):
                continue
            targets.append(
                StudyAnalysisTarget(
                    target_id=str(item.get("target_id") or ""),
                    class_name=str(item.get("class_name") or ""),
                    method_name=str(item.get("method_name") or ""),
                    method_signature=str(item.get("method_signature") or ""),
                )
            )

        return targets

    def _analyze_target_arm(
        self,
        *,
        project_path: Path,
        study_results_path: Path,
        target: StudyAnalysisTarget,
        arm: str,
    ) -> StudyAnalysisRowSchema:
        archive_dir = study_results_path / "artifacts" / target.target_id / arm
        if not archive_dir.exists():
            raise FileNotFoundError(f"缺少研究臂测试归档目录: {archive_dir}")

        validation_id = f"study_analysis_{self._sanitize_target_id(target.target_id)}_{arm}"
        workspace_path = Path(
            self.sandbox_manager.create_validation_sandbox(
                str(project_path), validation_id=validation_id
            )
        ).resolve()
        sandbox_id = workspace_path.name

        try:
            self._replace_test_sources(workspace_path, archive_dir)
            replay_result = dict(self.maven_runner(workspace_path, self.settings))
            if not bool(replay_result.get("success", False)):
                error_detail = (
                    replay_result.get("error")
                    or replay_result.get("stderr")
                    or replay_result.get("stdout")
                    or "unknown error"
                )
                raise StudyAnalysisError(f"{target.target_id} [{arm}] 回放失败: {error_detail}")

            jacoco_metrics = self._load_jacoco_metrics(workspace_path)
            pit_metrics = self._load_pit_metrics(workspace_path)
            return StudyAnalysisRowSchema(
                target_id=target.target_id,
                arm=arm,
                class_name=target.class_name,
                method_name=target.method_name,
                method_signature=target.method_signature,
                test_archive_dir=str(archive_dir),
                jacoco_line_coverage=float(jacoco_metrics["line_coverage"]),
                jacoco_branch_coverage=float(jacoco_metrics["branch_coverage"]),
                jacoco_method_coverage=float(jacoco_metrics["method_coverage"]),
                jacoco_class_coverage=float(jacoco_metrics["class_coverage"]),
                jacoco_total_lines=int(jacoco_metrics["total_lines"]),
                jacoco_covered_lines=int(jacoco_metrics["covered_lines_count"]),
                jacoco_total_branches=int(jacoco_metrics["total_branches"]),
                jacoco_covered_branches=int(jacoco_metrics["covered_branches"]),
                jacoco_total_methods=int(jacoco_metrics["total_methods"]),
                jacoco_covered_methods=int(jacoco_metrics["covered_methods"]),
                jacoco_total_classes=int(jacoco_metrics["total_classes"]),
                jacoco_covered_classes=int(jacoco_metrics["covered_classes"]),
                pit_total_mutants=int(pit_metrics["total_mutants"]),
                pit_killed_mutants=int(pit_metrics["killed_mutants"]),
                pit_survived_mutants=int(pit_metrics["survived_mutants"]),
                pit_no_coverage_mutants=int(pit_metrics["no_coverage_mutants"]),
                pit_timed_out_mutants=int(pit_metrics["timed_out_mutants"]),
                pit_run_error_mutants=int(pit_metrics["run_error_mutants"]),
                pit_mutation_kill_rate=float(pit_metrics["mutation_kill_rate"]),
                pit_test_strength=float(pit_metrics["test_strength"]),
            )
        finally:
            self.sandbox_manager.cleanup_sandbox(sandbox_id)

    def _replace_test_sources(self, workspace_path: Path, archive_dir: Path) -> None:
        test_root = workspace_path / "src" / "test" / "java"
        test_root.mkdir(parents=True, exist_ok=True)

        for existing_test_file in sorted(test_root.rglob("*Test.java")):
            if existing_test_file.is_file():
                existing_test_file.unlink()

        copied = False
        for source_file in sorted(path for path in archive_dir.rglob("*.java") if path.is_file()):
            rel_path = source_file.relative_to(archive_dir)
            target_file = test_root / rel_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
            copied = True

        if not copied:
            raise StudyAnalysisError(f"研究臂归档目录中没有测试文件: {archive_dir}")

    def _run_maven_replay(self, workspace_path: Path, settings: Settings) -> Mapping[str, object]:
        pom_path = workspace_path / "pom.xml"
        cmd = [
            settings.execution.resolve_mvn_cmd(),
            "clean",
            "test",
            "org.pitest:pitest-maven:mutationCoverage",
            "-f",
            str(pom_path),
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
        error = stderr.strip() or stdout.strip() or f"Maven exit code {process.returncode}"
        return {
            "success": False,
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": error,
        }

    def _load_jacoco_metrics(self, workspace_path: Path) -> dict[str, Any]:
        jacoco_path = workspace_path / _JACOCO_XML_RELATIVE_PATH
        if not jacoco_path.exists():
            raise StudyAnalysisError(f"JaCoCo 报告缺失: {jacoco_path}")
        return self.coverage_parser.aggregate_global_coverage_from_xml(str(jacoco_path))

    def _load_pit_metrics(self, workspace_path: Path) -> dict[str, Any]:
        mutations_path = self._resolve_pit_mutations_xml_path(workspace_path)

        records = parse_pit_mutations_xml(mutations_path)
        total_mutants = len(records)
        killed_mutants = sum(1 for record in records if record.status == "KILLED")
        survived_mutants = sum(1 for record in records if record.status == "SURVIVED")
        no_coverage_mutants = sum(1 for record in records if record.status == "NO_COVERAGE")
        timed_out_mutants = sum(1 for record in records if record.status == "TIMED_OUT")
        run_error_mutants = sum(
            1 for record in records if record.status in {"RUN_ERROR", "NON_VIABLE", "MEMORY_ERROR"}
        )

        test_strength_denominator = max(total_mutants - no_coverage_mutants, 0)
        mutation_kill_rate = killed_mutants / total_mutants if total_mutants > 0 else 0.0
        test_strength = (
            killed_mutants / test_strength_denominator if test_strength_denominator > 0 else 0.0
        )

        html_metrics = self._load_pit_html_metrics(workspace_path)
        if html_metrics["mutation_kill_rate"] is not None:
            mutation_kill_rate = float(html_metrics["mutation_kill_rate"])
        if html_metrics["test_strength"] is not None:
            test_strength = float(html_metrics["test_strength"])

        return {
            "total_mutants": total_mutants,
            "killed_mutants": killed_mutants,
            "survived_mutants": survived_mutants,
            "no_coverage_mutants": no_coverage_mutants,
            "timed_out_mutants": timed_out_mutants,
            "run_error_mutants": run_error_mutants,
            "mutation_kill_rate": mutation_kill_rate,
            "test_strength": test_strength,
        }

    def _load_pit_html_metrics(self, workspace_path: Path) -> dict[str, float | None]:
        reports_root = workspace_path / "target" / "pit-reports"
        if not reports_root.exists():
            return {"mutation_kill_rate": None, "test_strength": None}

        candidate_paths = sorted(reports_root.rglob("index.html"))
        for index_path in candidate_paths:
            html = index_path.read_text(encoding="utf-8")
            return {
                "mutation_kill_rate": _extract_pit_percentage_from_html(html, "Mutation Coverage"),
                "test_strength": _extract_pit_percentage_from_html(html, "Test Strength"),
            }
        return {"mutation_kill_rate": None, "test_strength": None}

    def _resolve_pit_mutations_xml_path(self, workspace_path: Path) -> Path:
        reports_root = workspace_path / "target" / "pit-reports"
        if not reports_root.exists():
            raise StudyAnalysisError(f"PIT 报告目录缺失: {reports_root}")

        direct_path = reports_root / "mutations.xml"
        if direct_path.exists():
            return direct_path

        candidates = sorted(reports_root.rglob("mutations.xml"))
        if candidates:
            return candidates[0]

        raise StudyAnalysisError(f"PIT 报告缺失: {reports_root / 'mutations.xml'}")

    def _write_csv(self, output_path: Path, rows: Sequence[StudyAnalysisRowSchema]) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(StudyAnalysisRowSchema.model_fields.keys())
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row.model_dump(mode="json"))

    @staticmethod
    def _sanitize_target_id(target_id: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", target_id)


def analyze_study_results(
    *,
    project_path: str | Path,
    study_results_path: str | Path,
    output_csv: str | Path | None,
    settings: Settings,
    max_workers: int | None = None,
    sandbox_manager: SandboxManager | None = None,
    maven_runner: Callable[[Path, Settings], Mapping[str, object]] | None = None,
) -> Path:
    analyzer = StudyReplayAnalyzer(
        settings=settings,
        sandbox_manager=sandbox_manager,
        maven_runner=maven_runner,
    )
    return analyzer.analyze(
        project_path=project_path,
        study_results_path=study_results_path,
        output_csv=output_csv,
        max_workers=max_workers,
    )
