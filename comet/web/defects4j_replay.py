from __future__ import annotations

import csv
import json
import logging
import shutil
import subprocess
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..config import Settings
from ..executor.surefire_parser import SurefireParser, TestResult
from ..utils import SandboxManager

logger = logging.getLogger(__name__)

DEFECTS4J_REPLAY_OUTPUT_FILENAMES = {
    "summary": "summary.json",
    "per_bug": "per_bug.csv",
    "per_test": "per_test.csv",
}


class Defects4JReplayError(RuntimeError):
    pass


class Defects4JReplayManifestEntry(BaseModel):
    project_id: str = Field(min_length=1)
    bug_id: str = Field(min_length=1)
    method: str = Field(min_length=1)
    test_path: str = Field(min_length=1)
    buggy_path: str | None = None
    fixed_path: str | None = None
    pom_override_path: str | None = None

    @field_validator("bug_id", mode="before")
    @classmethod
    def _normalize_bug_id(cls, value: object) -> str:
        if value is None:
            raise ValueError("bug_id 不能为空")
        return str(value).strip()

    @property
    def bug_key(self) -> str:
        return f"{self.project_id}-{self.bug_id}"


class Defects4JReplayPerBugRow(BaseModel):
    method: str
    project_id: str
    bug_id: str
    test_path: str
    buggy_source_path: str
    fixed_source_path: str
    compile_valid: bool
    buggy_compile_success: bool
    fixed_compile_success: bool
    compile_fixed: bool
    pass_fixed: bool
    fail_buggy: bool
    compatible_test_file_count: int = 0
    pass_fixed_test_file_count: int = 0
    fail_buggy_test_file_count: int = 0
    compatible_test_files: str = ""
    pass_fixed_test_files: str = ""
    fail_buggy_test_files: str = ""
    buggy_total_tests: int = 0
    buggy_failed_tests: int = 0
    buggy_error_tests: int = 0
    fixed_total_tests: int = 0
    fixed_failed_tests: int = 0
    fixed_error_tests: int = 0
    triggered: bool
    triggered_test_count: int = 0
    buggy_failed_test_names: str = ""
    fixed_failed_test_names: str = ""
    triggered_test_names: str = ""
    consistency_ratio: float = Field(default=0.0, ge=0.0)
    buggy_compile_error: str = ""
    fixed_compile_error: str = ""
    entry_error: str = ""


class Defects4JReplayPerTestRow(BaseModel):
    method: str
    project_id: str
    bug_id: str
    test_file: str
    compile_fixed: bool
    pass_fixed: bool
    fail_buggy: bool
    fixed_compile_error: str = ""
    buggy_compile_error: str = ""
    fixed_total_tests: int = 0
    fixed_failed_tests: int = 0
    fixed_error_tests: int = 0
    buggy_total_tests: int = 0
    buggy_failed_tests: int = 0
    buggy_error_tests: int = 0


class Defects4JReplayMethodSummary(BaseModel):
    method: str
    total_defects: int = Field(default=0, ge=0)
    compatible_defects: int = Field(default=0, ge=0)
    compatibility_rate: float = Field(default=0.0, ge=0.0)
    valid_regression_defects: int = Field(default=0, ge=0)
    valid_regression_test_rate: float = Field(default=0.0, ge=0.0)
    end_to_end_success_defects: int = Field(default=0, ge=0)
    end_to_end_success_rate: float = Field(default=0.0, ge=0.0)


class Defects4JReplaySummary(BaseModel):
    manifest_path: str
    output_dir: str
    total_entries: int = Field(default=0, ge=0)
    checkout_mode: str
    per_method: list[Defects4JReplayMethodSummary] = Field(default_factory=list)


@dataclass(slots=True, frozen=True)
class Defects4JReplayArtifacts:
    output_root: Path
    summary_path: Path
    per_bug_path: Path
    per_test_path: Path


@dataclass(slots=True, frozen=True)
class _ResolvedSourcePair:
    buggy_source: Path
    fixed_source: Path


@dataclass(slots=True)
class _VersionRunResult:
    compile_success: bool
    compile_error: str = ""
    test_results: dict[str, TestResult] | None = None
    total_tests: int = 0
    failed_tests: int = 0
    error_tests: int = 0


@dataclass(slots=True, frozen=True)
class _TestFileReplayResult:
    test_file: Path
    fixed_result: _VersionRunResult
    buggy_result: _VersionRunResult | None


class _Defects4JSourceResolver:
    def __init__(
        self,
        *,
        checkout_mode: Literal["none", "local", "docker"],
        checkout_root: Path | None,
        defects4j_root: Path | None,
        docker_image: str | None,
        refresh_checkouts: bool,
        checkout_runner: Callable[[Sequence[str], Path | None], None] | None = None,
    ) -> None:
        self.checkout_mode = checkout_mode
        self.checkout_root = checkout_root
        self.defects4j_root = defects4j_root
        self.docker_image = docker_image
        self.refresh_checkouts = refresh_checkouts
        self.checkout_runner = checkout_runner or self._run_checkout_command
        self._cache: dict[tuple[str, str], _ResolvedSourcePair] = {}
        self._lock = threading.Lock()

    def resolve(self, entry: Defects4JReplayManifestEntry) -> _ResolvedSourcePair:
        if entry.buggy_path and entry.fixed_path:
            buggy_source = Path(entry.buggy_path).expanduser().resolve()
            fixed_source = Path(entry.fixed_path).expanduser().resolve()
            self._validate_project_root(buggy_source, f"{entry.bug_key} buggy", entry)
            self._validate_project_root(fixed_source, f"{entry.bug_key} fixed", entry)
            return _ResolvedSourcePair(buggy_source=buggy_source, fixed_source=fixed_source)

        if self.checkout_mode == "none":
            raise Defects4JReplayError(
                f"{entry.bug_key} 缺少 buggy_path/fixed_path，且未启用 checkout"
            )

        cache_key = (entry.project_id, entry.bug_id)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        resolved = self._checkout_sources(entry)
        with self._lock:
            self._cache[cache_key] = resolved
        return resolved

    def expected_sources(self, entry: Defects4JReplayManifestEntry) -> _ResolvedSourcePair:
        if entry.buggy_path and entry.fixed_path:
            return _ResolvedSourcePair(
                buggy_source=Path(entry.buggy_path).expanduser().resolve(),
                fixed_source=Path(entry.fixed_path).expanduser().resolve(),
            )

        if self.checkout_root is None:
            return _ResolvedSourcePair(buggy_source=Path(), fixed_source=Path())

        bug_root = self.checkout_root / f"{entry.project_id}-{entry.bug_id}"
        return _ResolvedSourcePair(buggy_source=bug_root / "buggy", fixed_source=bug_root / "fixed")

    def _checkout_sources(self, entry: Defects4JReplayManifestEntry) -> _ResolvedSourcePair:
        if self.checkout_root is None:
            raise Defects4JReplayError("启用 checkout 时必须提供 checkout_root")

        bug_root = self.checkout_root / f"{entry.project_id}-{entry.bug_id}"
        buggy_source = bug_root / "buggy"
        fixed_source = bug_root / "fixed"

        if self.refresh_checkouts and bug_root.exists():
            shutil.rmtree(bug_root)

        buggy_ready = self._is_ready_project_root(buggy_source, entry)
        fixed_ready = self._is_ready_project_root(fixed_source, entry)
        if buggy_ready and fixed_ready:
            return _ResolvedSourcePair(buggy_source=buggy_source, fixed_source=fixed_source)

        bug_root.mkdir(parents=True, exist_ok=True)
        self._run_single_checkout(entry.project_id, entry.bug_id, "b", buggy_source)
        self._run_single_checkout(entry.project_id, entry.bug_id, "f", fixed_source)
        self._validate_project_root(buggy_source, f"{entry.bug_key} buggy", entry)
        self._validate_project_root(fixed_source, f"{entry.bug_key} fixed", entry)
        return _ResolvedSourcePair(buggy_source=buggy_source, fixed_source=fixed_source)

    def _run_single_checkout(
        self,
        project_id: str,
        bug_id: str,
        version_suffix: Literal["b", "f"],
        target_path: Path,
    ) -> None:
        if target_path.exists():
            shutil.rmtree(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if self.checkout_mode == "local":
            if self.defects4j_root is None:
                raise Defects4JReplayError("local checkout 模式必须提供 defects4j_root")
            command = [
                str(self.defects4j_root / "framework" / "bin" / "defects4j"),
                "checkout",
                "-p",
                project_id,
                "-v",
                f"{bug_id}{version_suffix}",
                "-w",
                str(target_path),
            ]
            workdir = self.defects4j_root
        elif self.checkout_mode == "docker":
            if not self.docker_image:
                raise Defects4JReplayError("docker checkout 模式必须提供 docker_image")
            if self.checkout_root is None:
                raise Defects4JReplayError("docker checkout 模式必须提供 checkout_root")
            checkout_root = self.checkout_root.resolve()
            container_target = Path("/checkout") / target_path.relative_to(checkout_root)
            command = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{checkout_root}:/checkout",
                "-w",
                "/defects4j",
                self.docker_image,
                "defects4j",
                "checkout",
                "-p",
                project_id,
                "-v",
                f"{bug_id}{version_suffix}",
                "-w",
                str(container_target),
            ]
            workdir = None
        else:
            raise Defects4JReplayError(f"不支持的 checkout 模式: {self.checkout_mode}")

        self.checkout_runner(command, workdir)

    def _run_checkout_command(self, command: Sequence[str], workdir: Path | None) -> None:
        process = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            cwd=str(workdir) if workdir is not None else None,
            check=False,
        )
        if process.returncode == 0:
            return

        error_detail = process.stderr.strip() or process.stdout.strip() or "unknown error"
        raise Defects4JReplayError(f"checkout 失败: {error_detail}")

    def _validate_project_root(
        self,
        project_root: Path,
        label: str,
        entry: Defects4JReplayManifestEntry,
    ) -> None:
        if not self._is_ready_project_root(project_root, entry):
            raise FileNotFoundError(f"{label} 工作树无效: {project_root}")

    def _is_ready_project_root(
        self,
        project_root: Path,
        entry: Defects4JReplayManifestEntry,
    ) -> bool:
        if not project_root.is_dir():
            return False

        if (project_root / ".defects4j.config").exists():
            return True

        if (project_root / "pom.xml").exists():
            return True

        return bool(entry.pom_override_path)


class Defects4JReplayRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        use_xvfb: bool = False,
        sandbox_manager: SandboxManager | None = None,
        maven_runner: Callable[[Path, Settings], Mapping[str, object]] | None = None,
        checkout_runner: Callable[[Sequence[str], Path | None], None] | None = None,
    ) -> None:
        self.settings = settings
        self.use_xvfb = use_xvfb
        sandbox_root = settings.resolve_output_root() / ".defects4j-replay-sandbox-runtime"
        self.sandbox_manager = sandbox_manager or SandboxManager(str(sandbox_root))
        self.maven_runner = maven_runner or self._run_maven_replay
        self.checkout_runner = checkout_runner
        self.surefire_parser = SurefireParser()

    def replay(
        self,
        *,
        manifest_path: str | Path,
        output_dir: str | Path,
        checkout_mode: Literal["none", "local", "docker"] = "none",
        defects4j_root: str | Path | None = None,
        checkout_root: str | Path | None = None,
        docker_image: str | None = None,
        refresh_checkouts: bool = False,
        max_workers: int | None = None,
    ) -> Defects4JReplayArtifacts:
        resolved_manifest_path = Path(manifest_path).expanduser().resolve()
        if not resolved_manifest_path.exists():
            raise FileNotFoundError(f"manifest 不存在: {resolved_manifest_path}")

        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        entries = self._load_manifest(resolved_manifest_path)
        resolver = _Defects4JSourceResolver(
            checkout_mode=checkout_mode,
            checkout_root=Path(checkout_root).expanduser().resolve() if checkout_root else None,
            defects4j_root=Path(defects4j_root).expanduser().resolve() if defects4j_root else None,
            docker_image=docker_image,
            refresh_checkouts=refresh_checkouts,
            checkout_runner=self.checkout_runner,
        )

        rows = self._run_entries(entries, resolver=resolver, max_workers=max_workers)
        per_bug_rows = [row[0] for row in rows]
        per_test_rows = [test_row for _, test_rows in rows for test_row in test_rows]

        artifacts = Defects4JReplayArtifacts(
            output_root=output_root,
            summary_path=output_root / DEFECTS4J_REPLAY_OUTPUT_FILENAMES["summary"],
            per_bug_path=output_root / DEFECTS4J_REPLAY_OUTPUT_FILENAMES["per_bug"],
            per_test_path=output_root / DEFECTS4J_REPLAY_OUTPUT_FILENAMES["per_test"],
        )
        self._write_csv(artifacts.per_bug_path, per_bug_rows)
        self._write_csv(artifacts.per_test_path, per_test_rows)
        summary = self._build_summary(
            manifest_path=resolved_manifest_path,
            output_dir=output_root,
            checkout_mode=checkout_mode,
            per_bug_rows=per_bug_rows,
        )
        artifacts.summary_path.write_text(
            json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Defects4J 回放完成，汇总文件: %s", artifacts.summary_path)
        return artifacts

    def _run_entries(
        self,
        entries: Sequence[Defects4JReplayManifestEntry],
        *,
        resolver: _Defects4JSourceResolver,
        max_workers: int | None,
    ) -> list[tuple[Defects4JReplayPerBugRow, list[Defects4JReplayPerTestRow]]]:
        if not entries:
            return []

        resolved_workers = self._resolve_max_workers(max_workers, len(entries))
        if resolved_workers <= 1:
            return [self._run_single_entry_safe(entry, resolver) for entry in entries]

        results_by_index: dict[
            int, tuple[Defects4JReplayPerBugRow, list[Defects4JReplayPerTestRow]]
        ] = {}
        with ThreadPoolExecutor(max_workers=resolved_workers) as executor:
            future_map = {
                executor.submit(self._run_single_entry_safe, entry, resolver): index
                for index, entry in enumerate(entries)
            }
            for future in as_completed(future_map):
                results_by_index[future_map[future]] = future.result()

        return [results_by_index[index] for index in range(len(entries))]

    def _resolve_max_workers(self, requested_max_workers: int | None, task_count: int) -> int:
        if task_count <= 0:
            return 1
        if requested_max_workers is not None:
            return max(1, min(requested_max_workers, task_count))

        configured_max_workers = self.settings.preprocessing.max_workers
        if configured_max_workers is not None:
            return max(1, min(configured_max_workers, task_count))
        return 1

    def _run_single_entry(
        self,
        entry: Defects4JReplayManifestEntry,
        resolver: _Defects4JSourceResolver,
    ) -> tuple[Defects4JReplayPerBugRow, list[Defects4JReplayPerTestRow]]:
        test_root = Path(entry.test_path).expanduser().resolve()
        if not test_root.is_dir():
            raise FileNotFoundError(f"{entry.bug_key} 的测试目录不存在: {test_root}")

        sources = resolver.resolve(entry)
        candidate_test_files = self._discover_test_files(test_root)
        file_results: list[_TestFileReplayResult] = []
        for index, test_file in enumerate(candidate_test_files):
            fixed_result = self._execute_version(
                source_root=sources.fixed_source,
                version_label=f"fixed_{index}",
                entry=entry,
                test_root=test_root,
                included_test_files=(test_file,),
            )
            buggy_result: _VersionRunResult | None = None
            if self._is_passing_result(fixed_result):
                buggy_result = self._execute_version(
                    source_root=sources.buggy_source,
                    version_label=f"buggy_{index}",
                    entry=entry,
                    test_root=test_root,
                    included_test_files=(test_file,),
                )
            file_results.append(
                _TestFileReplayResult(
                    test_file=test_file,
                    fixed_result=fixed_result,
                    buggy_result=buggy_result,
                )
            )

        per_bug_row, per_test_rows = self._build_entry_results(
            entry=entry,
            test_root=test_root,
            sources=sources,
            file_results=file_results,
        )
        return per_bug_row, per_test_rows

    def _run_single_entry_safe(
        self,
        entry: Defects4JReplayManifestEntry,
        resolver: _Defects4JSourceResolver,
    ) -> tuple[Defects4JReplayPerBugRow, list[Defects4JReplayPerTestRow]]:
        try:
            return self._run_single_entry(entry, resolver)
        except Exception as exc:
            logger.exception("Defects4J 回放条目失败，已跳过: %s", entry.bug_key)
            return self._build_error_result(entry, resolver, str(exc)), []

    def _build_error_result(
        self,
        entry: Defects4JReplayManifestEntry,
        resolver: _Defects4JSourceResolver,
        error_message: str,
    ) -> Defects4JReplayPerBugRow:
        expected_sources = resolver.expected_sources(entry)
        return Defects4JReplayPerBugRow(
            method=entry.method,
            project_id=entry.project_id,
            bug_id=entry.bug_id,
            test_path=str(Path(entry.test_path).expanduser().resolve()),
            buggy_source_path=str(expected_sources.buggy_source),
            fixed_source_path=str(expected_sources.fixed_source),
            compile_valid=False,
            buggy_compile_success=False,
            fixed_compile_success=False,
            compile_fixed=False,
            pass_fixed=False,
            fail_buggy=False,
            triggered=False,
            buggy_compile_error=error_message,
            fixed_compile_error=error_message,
            entry_error=error_message,
        )

    def _execute_version(
        self,
        *,
        source_root: Path,
        version_label: str,
        entry: Defects4JReplayManifestEntry,
        test_root: Path,
        included_test_files: Sequence[Path] | None = None,
    ) -> _VersionRunResult:
        validation_id = f"d4j_{entry.project_id}_{entry.bug_id}_{entry.method}_{version_label}"
        workspace_path = Path(
            self.sandbox_manager.create_validation_sandbox(
                str(source_root),
                validation_id=validation_id,
            )
        ).resolve()
        sandbox_id = workspace_path.name

        try:
            self._apply_pom_override(workspace_path, entry)
            self._replace_test_directory(
                workspace_path,
                test_root,
                included_test_files=included_test_files,
            )
            replay_result = dict(self.maven_runner(workspace_path, self.settings))
            compile_success = bool(replay_result.get("compile_success", False))
            compile_error = str(replay_result.get("compile_error") or "")
            if not compile_success:
                return _VersionRunResult(compile_success=False, compile_error=compile_error)

            report_dir = workspace_path / "target" / "surefire-reports"
            test_results = self._collect_test_results(report_dir)
            if not test_results and not bool(replay_result.get("success", False)):
                compile_error = str(
                    replay_result.get("error")
                    or replay_result.get("stderr")
                    or replay_result.get("stdout")
                    or "测试执行失败"
                )
            summary = self.surefire_parser.get_test_summary(str(report_dir))
            return _VersionRunResult(
                compile_success=True,
                compile_error=compile_error,
                test_results=test_results,
                total_tests=int(summary.get("total_tests", 0)),
                failed_tests=int(summary.get("failed_tests", 0)),
                error_tests=int(summary.get("error_tests", 0)),
            )
        finally:
            self.sandbox_manager.cleanup_sandbox(sandbox_id)

    def _discover_test_files(self, test_root: Path) -> list[Path]:
        test_files = [
            path.relative_to(test_root)
            for path in sorted(test_root.rglob("*Test.java"))
            if path.is_file()
        ]
        if not test_files:
            raise Defects4JReplayError(f"{test_root} 中未找到可回放的 *Test.java 文件")
        return test_files

    def _replace_test_directory(
        self,
        workspace_path: Path,
        test_root: Path,
        *,
        included_test_files: Sequence[Path] | None = None,
    ) -> None:
        target_test_root = workspace_path / "src" / "test"
        target_test_root.mkdir(parents=True, exist_ok=True)
        target_java_root = target_test_root / "java"
        if target_java_root.exists():
            for existing_test_file in sorted(target_java_root.rglob("*Test.java")):
                if existing_test_file.is_file():
                    existing_test_file.unlink()

        included_set = {path.as_posix() for path in included_test_files or []}
        for source_path in sorted(test_root.rglob("*")):
            relative_path = source_path.relative_to(test_root)
            if (
                source_path.is_file()
                and relative_path.name.endswith("Test.java")
                and included_set
                and relative_path.as_posix() not in included_set
            ):
                continue
            target_path = target_test_root / relative_path
            if source_path.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)

    def _apply_pom_override(
        self, workspace_path: Path, entry: Defects4JReplayManifestEntry
    ) -> None:
        if not entry.pom_override_path:
            return

        override_path = Path(entry.pom_override_path).expanduser().resolve()
        if not override_path.is_file():
            raise Defects4JReplayError(
                f"{entry.bug_key} 的 pom_override_path 不存在或不是文件: {override_path}"
            )

        shutil.copy2(override_path, workspace_path / "pom.xml")

    def _run_maven_replay(self, workspace_path: Path, settings: Settings) -> Mapping[str, object]:
        pom_path = workspace_path / "pom.xml"
        env = settings.execution.build_target_subprocess_env()
        mvn_prefix = ["xvfb-run", "-a"] if self.use_xvfb else []
        compile_cmd = [
            *mvn_prefix,
            settings.execution.resolve_mvn_cmd(),
            "-f",
            str(pom_path),
            "-DskipTests=true",
            "test-compile",
        ]
        compile_process = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
            env=env,
        )
        compile_stdout = compile_process.stdout or ""
        compile_stderr = compile_process.stderr or ""
        if compile_process.returncode != 0:
            compile_error = compile_stderr.strip() or compile_stdout.strip() or "test-compile 失败"
            return {
                "success": False,
                "compile_success": False,
                "compile_error": compile_error,
                "stdout": compile_stdout,
                "stderr": compile_stderr,
            }

        test_cmd = [
            *mvn_prefix,
            settings.execution.resolve_mvn_cmd(),
            "-f",
            str(pom_path),
            "-Dmaven.test.failure.ignore=true",
            "-DskipTests=false",
            "test",
        ]
        test_process = subprocess.run(
            test_cmd,
            capture_output=True,
            text=True,
            env=env,
        )
        stdout = test_process.stdout or ""
        stderr = test_process.stderr or ""
        success = test_process.returncode == 0
        error = "" if success else (stderr.strip() or stdout.strip() or "测试执行失败")
        return {
            "success": success,
            "compile_success": True,
            "compile_error": "",
            "stdout": stdout,
            "stderr": stderr,
            "error": error,
        }

    def _collect_test_results(self, report_dir: Path) -> dict[str, TestResult]:
        suites = self.surefire_parser.parse_surefire_reports(str(report_dir))
        results: dict[str, TestResult] = {}
        for suite in suites:
            for test_case in suite.test_cases:
                full_name = f"{test_case.class_name}.{test_case.method_name}"
                results[full_name] = test_case
        return results

    def _build_entry_results(
        self,
        *,
        entry: Defects4JReplayManifestEntry,
        test_root: Path,
        sources: _ResolvedSourcePair,
        file_results: Sequence[_TestFileReplayResult],
    ) -> tuple[Defects4JReplayPerBugRow, list[Defects4JReplayPerTestRow]]:
        per_test_rows: list[Defects4JReplayPerTestRow] = []
        compatible_test_files: list[str] = []
        pass_fixed_test_files: list[str] = []
        fail_buggy_test_files: list[str] = []
        buggy_failed_test_names: list[str] = []
        fixed_failed_test_names: list[str] = []
        fixed_compile_errors: list[str] = []
        buggy_compile_errors: list[str] = []

        buggy_compile_success = False
        buggy_total_tests = 0
        buggy_failed_tests = 0
        buggy_error_tests = 0
        fixed_total_tests = 0
        fixed_failed_tests = 0
        fixed_error_tests = 0

        for file_result in file_results:
            relative_test_file = file_result.test_file.as_posix()
            compile_fixed = file_result.fixed_result.compile_success
            pass_fixed = self._is_passing_result(file_result.fixed_result)
            fail_buggy = self._is_buggy_failure(file_result.buggy_result)
            if compile_fixed:
                compatible_test_files.append(relative_test_file)
            if pass_fixed:
                pass_fixed_test_files.append(relative_test_file)
            if fail_buggy:
                fail_buggy_test_files.append(relative_test_file)

            if file_result.fixed_result.compile_error:
                fixed_compile_errors.append(
                    f"{relative_test_file}: {file_result.fixed_result.compile_error}"
                )
            if file_result.buggy_result and file_result.buggy_result.compile_error:
                buggy_compile_errors.append(
                    f"{relative_test_file}: {file_result.buggy_result.compile_error}"
                )

            fixed_total_tests += file_result.fixed_result.total_tests
            fixed_failed_tests += file_result.fixed_result.failed_tests
            fixed_error_tests += file_result.fixed_result.error_tests

            if file_result.buggy_result is not None:
                buggy_compile_success = (
                    buggy_compile_success or file_result.buggy_result.compile_success
                )
                buggy_total_tests += file_result.buggy_result.total_tests
                buggy_failed_tests += file_result.buggy_result.failed_tests
                buggy_error_tests += file_result.buggy_result.error_tests

            fixed_failed_test_names.extend(
                self._collect_failed_test_names(file_result.fixed_result, prefix=relative_test_file)
            )
            buggy_failed_test_names.extend(
                self._collect_failed_test_names(file_result.buggy_result, prefix=relative_test_file)
            )

            per_test_rows.append(
                Defects4JReplayPerTestRow(
                    method=entry.method,
                    project_id=entry.project_id,
                    bug_id=entry.bug_id,
                    test_file=relative_test_file,
                    compile_fixed=compile_fixed,
                    pass_fixed=pass_fixed,
                    fail_buggy=fail_buggy,
                    fixed_compile_error=file_result.fixed_result.compile_error,
                    buggy_compile_error=(
                        file_result.buggy_result.compile_error if file_result.buggy_result else ""
                    ),
                    fixed_total_tests=file_result.fixed_result.total_tests,
                    fixed_failed_tests=file_result.fixed_result.failed_tests,
                    fixed_error_tests=file_result.fixed_result.error_tests,
                    buggy_total_tests=(
                        file_result.buggy_result.total_tests if file_result.buggy_result else 0
                    ),
                    buggy_failed_tests=(
                        file_result.buggy_result.failed_tests if file_result.buggy_result else 0
                    ),
                    buggy_error_tests=(
                        file_result.buggy_result.error_tests if file_result.buggy_result else 0
                    ),
                )
            )

        compile_fixed = bool(compatible_test_files)
        pass_fixed = bool(pass_fixed_test_files)
        fail_buggy = bool(fail_buggy_test_files)
        compile_valid = compile_fixed
        consistency_ratio = (
            len(fail_buggy_test_files) / len(pass_fixed_test_files)
            if pass_fixed_test_files
            else 0.0
        )

        per_bug_row = Defects4JReplayPerBugRow(
            method=entry.method,
            project_id=entry.project_id,
            bug_id=entry.bug_id,
            test_path=str(test_root),
            buggy_source_path=str(sources.buggy_source),
            fixed_source_path=str(sources.fixed_source),
            compile_valid=compile_valid,
            buggy_compile_success=buggy_compile_success,
            fixed_compile_success=compile_fixed,
            compile_fixed=compile_fixed,
            pass_fixed=pass_fixed,
            fail_buggy=fail_buggy,
            compatible_test_file_count=len(compatible_test_files),
            pass_fixed_test_file_count=len(pass_fixed_test_files),
            fail_buggy_test_file_count=len(fail_buggy_test_files),
            compatible_test_files=";".join(compatible_test_files),
            pass_fixed_test_files=";".join(pass_fixed_test_files),
            fail_buggy_test_files=";".join(fail_buggy_test_files),
            buggy_total_tests=buggy_total_tests,
            buggy_failed_tests=buggy_failed_tests,
            buggy_error_tests=buggy_error_tests,
            fixed_total_tests=fixed_total_tests,
            fixed_failed_tests=fixed_failed_tests,
            fixed_error_tests=fixed_error_tests,
            triggered=fail_buggy,
            triggered_test_count=len(fail_buggy_test_files),
            buggy_failed_test_names=";".join(sorted(buggy_failed_test_names)),
            fixed_failed_test_names=";".join(sorted(fixed_failed_test_names)),
            triggered_test_names=";".join(fail_buggy_test_files),
            consistency_ratio=consistency_ratio,
            buggy_compile_error="; ".join(buggy_compile_errors),
            fixed_compile_error="; ".join(fixed_compile_errors),
        )
        return per_bug_row, per_test_rows

    def _build_summary(
        self,
        *,
        manifest_path: Path,
        output_dir: Path,
        checkout_mode: str,
        per_bug_rows: Sequence[Defects4JReplayPerBugRow],
    ) -> Defects4JReplaySummary:
        by_method: dict[str, list[Defects4JReplayPerBugRow]] = defaultdict(list)
        for row in per_bug_rows:
            by_method[row.method].append(row)

        method_summaries: list[Defects4JReplayMethodSummary] = []
        for method, rows in sorted(by_method.items()):
            defects: dict[tuple[str, str], dict[str, bool]] = {}
            for row in rows:
                defect_key = (row.project_id, row.bug_id)
                aggregate = defects.setdefault(
                    defect_key,
                    {"compile_fixed": False, "pass_fixed": False, "fail_buggy": False},
                )
                aggregate["compile_fixed"] = aggregate["compile_fixed"] or row.compile_fixed
                aggregate["pass_fixed"] = aggregate["pass_fixed"] or row.pass_fixed
                aggregate["fail_buggy"] = aggregate["fail_buggy"] or row.fail_buggy

            total_defects = len(defects)
            compatible_defects = sum(1 for defect in defects.values() if defect["compile_fixed"])
            valid_regression_defects = sum(1 for defect in defects.values() if defect["fail_buggy"])
            end_to_end_success_defects = valid_regression_defects
            method_summaries.append(
                Defects4JReplayMethodSummary(
                    method=method,
                    total_defects=total_defects,
                    compatible_defects=compatible_defects,
                    compatibility_rate=(
                        compatible_defects / total_defects if total_defects else 0.0
                    ),
                    valid_regression_defects=valid_regression_defects,
                    valid_regression_test_rate=(
                        valid_regression_defects / compatible_defects if compatible_defects else 0.0
                    ),
                    end_to_end_success_defects=end_to_end_success_defects,
                    end_to_end_success_rate=(
                        end_to_end_success_defects / total_defects if total_defects else 0.0
                    ),
                )
            )

        return Defects4JReplaySummary(
            manifest_path=str(manifest_path),
            output_dir=str(output_dir),
            total_entries=len(per_bug_rows),
            checkout_mode=checkout_mode,
            per_method=method_summaries,
        )

    def _write_csv(self, output_path: Path, rows: Sequence[BaseModel]) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            output_path.write_text("", encoding="utf-8")
            return

        fieldnames = list(rows[0].model_dump(mode="json").keys())
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row.model_dump(mode="json"))

    def _load_manifest(self, manifest_path: Path) -> list[Defects4JReplayManifestEntry]:
        suffix = manifest_path.suffix.lower()
        if suffix == ".jsonl":
            payloads = [
                json.loads(line)
                for line in manifest_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        elif suffix == ".json":
            raw_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(raw_payload, list):
                raise Defects4JReplayError("JSON manifest 顶层必须是数组")
            payloads = raw_payload
        elif suffix == ".csv":
            with manifest_path.open("r", encoding="utf-8", newline="") as handle:
                payloads = list(csv.DictReader(handle))
        else:
            raise Defects4JReplayError("manifest 仅支持 .jsonl / .json / .csv")

        entries = [Defects4JReplayManifestEntry.model_validate(item) for item in payloads]
        if not entries:
            raise Defects4JReplayError("manifest 为空，无法执行回放")
        return entries

    def _is_passing_result(self, result: _VersionRunResult) -> bool:
        return (
            result.compile_success
            and result.total_tests > 0
            and result.failed_tests == 0
            and result.error_tests == 0
            and not result.compile_error
        )

    def _is_buggy_failure(self, result: _VersionRunResult | None) -> bool:
        if result is None or not result.compile_success:
            return False
        return result.failed_tests > 0 or result.error_tests > 0

    def _collect_failed_test_names(
        self,
        result: _VersionRunResult | None,
        *,
        prefix: str,
    ) -> list[str]:
        if result is None or not result.test_results:
            return []
        return sorted(
            f"{prefix}::{test_name}"
            for test_name, test_result in result.test_results.items()
            if self._classify_test_status(test_result) in {"failed", "error"}
        )

    def _classify_test_status(self, result: TestResult | None) -> str:
        if result is None:
            return "missing"
        if result.skipped:
            return "skipped"
        if result.passed:
            return "passed"
        if result.error_type or result.error_message:
            return "error"
        return "failed"

    def _extract_failure_type(self, result: TestResult | None) -> str:
        if result is None:
            return ""
        return str(result.failure_type or result.error_type or "")

    def _extract_failure_message(self, result: TestResult | None) -> str:
        if result is None:
            return ""
        return str(result.failure_message or result.error_message or "")


def replay_defects4j_tests(
    *,
    manifest_path: str | Path,
    output_dir: str | Path,
    settings: Settings,
    checkout_mode: Literal["none", "local", "docker"] = "none",
    use_xvfb: bool = False,
    defects4j_root: str | Path | None = None,
    checkout_root: str | Path | None = None,
    docker_image: str | None = None,
    refresh_checkouts: bool = False,
    max_workers: int | None = None,
    sandbox_manager: SandboxManager | None = None,
    maven_runner: Callable[[Path, Settings], Mapping[str, object]] | None = None,
    checkout_runner: Callable[[Sequence[str], Path | None], None] | None = None,
) -> Defects4JReplayArtifacts:
    runner = Defects4JReplayRunner(
        settings=settings,
        use_xvfb=use_xvfb,
        sandbox_manager=sandbox_manager,
        maven_runner=maven_runner,
        checkout_runner=checkout_runner,
    )
    return runner.replay(
        manifest_path=manifest_path,
        output_dir=output_dir,
        checkout_mode=checkout_mode,
        defects4j_root=defects4j_root,
        checkout_root=checkout_root,
        docker_image=docker_image,
        refresh_checkouts=refresh_checkouts,
        max_workers=max_workers,
    )
