from __future__ import annotations

import logging
import sqlite3
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

MISSING_TEXT = "未提供"

MetricValue = int | float | None
MetricsPayload = Mapping[str, MetricValue]
SourcesPayload = Mapping[str, bool | None]
CountPayload = Mapping[str, int | None]


def build_run_report(
    *,
    run_id: str,
    mode: str,
    project_path: str,
    repo_url: str | None,
    base_branch: str | None,
    java_version: str | None,
    started_at: str | None,
    completed_at: str,
    metrics: MetricsPayload,
    mutation_enabled: bool | None,
    sources: SourcesPayload,
    tests_summary: CountPayload,
    mutants_summary: CountPayload,
    test_files: list[str],
    git_branch: str | None,
    git_commit: str | None,
) -> str:
    final_state_available = bool(sources.get("finalState"))

    line_coverage = _format_percentage(
        metrics.get("lineCoverage") if final_state_available else None
    )
    branch_coverage = _format_percentage(
        metrics.get("branchCoverage") if final_state_available else None
    )
    total_tests = _format_count(metrics.get("totalTests") if final_state_available else None)

    if final_state_available:
        # 对齐前端 getDisplayMutationScore 逻辑：并行模式用全局分数，标准模式用迭代分数
        if mode == "parallel":
            mutation_score_value: MetricValue = metrics.get("globalMutationScore")
        else:
            mutation_score_value = metrics.get("mutationScore")
        # 兜底：分数为 0 或缺失但数据库有变异体时，从数据库计算
        db_total = mutants_summary.get("total")
        db_killed = mutants_summary.get("killed")
        if (
            (mutation_score_value is None or mutation_score_value == 0)
            and isinstance(db_total, int)
            and isinstance(db_killed, int)
            and db_total > 0
        ):
            mutation_score_value = db_killed / db_total
    else:
        mutation_score_value = None
    if mutation_enabled is False:
        mutation_score = "未启用"
    else:
        mutation_score = _format_percentage(mutation_score_value)

    risks = _build_risk_items(mutants_summary=mutants_summary, tests_summary=tests_summary)
    suggestions = _build_follow_up_suggestions(
        risks=risks,
        final_state_available=final_state_available,
        mutation_enabled=mutation_enabled,
        test_files=test_files,
    )

    lines = [
        "# COMET-L 报告",
        "",
        "## 执行摘要",
        f"- 运行 ID: {run_id}",
        "- 执行状态: 成功",
        f"- 运行模式: {mode}",
        f"- 开始时间: {_format_text(started_at)}",
        f"- 完成时间: {_format_text(completed_at)}",
        "- 说明: 本次运行已成功完成，并已生成可下载 Markdown 报告。",
        "",
        "## 目标仓库与基线分支",
        f"- 项目路径: {_format_text(project_path)}",
        f"- 仓库地址: {_format_text(repo_url)}",
        f"- 基线分支: {_format_text(base_branch)}",
        "",
        "## 提交/分支信息",
        f"- 当前分支: {_format_text(git_branch)}",
        f"- 当前提交: {_format_text(git_commit)}",
        "",
        "## Java 版本",
        f"- 目标项目 Java 版本: {_format_text(java_version)}",
        "",
        "## 生成测试文件列表",
    ]

    if test_files:
        lines.extend(f"- {path}" for path in test_files)
    else:
        lines.append("- 未生成测试文件")

    lines.extend(
        [
            "",
            "## 关键结果指标（覆盖率/变异分数/测试数）",
            f"- 行覆盖率: {line_coverage}",
            f"- 分支覆盖率: {branch_coverage}",
            f"- 变异分数: {mutation_score}",
            f"- 测试数: {total_tests}",
            "",
            "## 失败与风险说明（若无则写“无”）",
        ]
    )

    if risks:
        lines.extend(f"- {item}" for item in risks)
    else:
        lines.append("- 无")

    lines.extend(["", "## 后续建议"])
    lines.extend(f"- {item}" for item in suggestions)
    lines.append("")
    return "\n".join(lines)


def collect_generated_test_files(database_path: Path) -> list[str]:
    if not database_path.exists() or database_path.stat().st_size == 0:
        return []

    try:
        connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        logger.warning("打开报告测试清单数据库失败 %s: %s", database_path, exc)
        return []

    try:
        cursor = connection.cursor()
        _ = cursor.execute(
            """
            SELECT DISTINCT package_name, class_name
            FROM test_cases
            ORDER BY class_name ASC, package_name ASC
            """
        )
        rows: list[sqlite3.Row] = cursor.fetchall()
    except sqlite3.Error as exc:
        logger.warning("读取报告测试清单失败 %s: %s", database_path, exc)
        return []
    finally:
        connection.close()

    paths: list[str] = []
    for row in rows:
        class_name = str(cast(str | None, row["class_name"]) or "").strip()
        if not class_name:
            continue
        package_name = str(cast(str | None, row["package_name"]) or "").strip()
        package_path = package_name.replace(".", "/")
        relative_path = f"src/test/java/{class_name}.java"
        if package_path:
            relative_path = f"src/test/java/{package_path}/{class_name}.java"
        paths.append(relative_path)
    return paths


def resolve_git_metadata(project_path: Path) -> tuple[str | None, str | None]:
    branch = _run_git_command(project_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    commit = _run_git_command(project_path, ["rev-parse", "HEAD"])
    return branch, commit


def _run_git_command(project_path: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, NotADirectoryError, OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _build_risk_items(*, mutants_summary: CountPayload, tests_summary: CountPayload) -> list[str]:
    risks: list[str] = []

    survived_mutants = mutants_summary.get("survived")
    if isinstance(survived_mutants, int) and survived_mutants > 0:
        risks.append(f"仍有 {survived_mutants} 个幸存变异体需要继续分析。")

    total_cases = tests_summary.get("totalCases")
    compiled_cases = tests_summary.get("compiledCases")
    if (
        isinstance(total_cases, int)
        and isinstance(compiled_cases, int)
        and total_cases > compiled_cases
    ):
        risks.append(f"有 {total_cases - compiled_cases} 个测试用例未通过编译。")

    return risks


def _build_follow_up_suggestions(
    *,
    risks: list[str],
    final_state_available: bool,
    mutation_enabled: bool | None,
    test_files: list[str],
) -> list[str]:
    if risks:
        return ["优先补强幸存变异体或未编译测试对应的方法与断言。"]
    if not final_state_available:
        return ["检查 final_state.json 产出链路，补齐覆盖率、变异分数和测试数指标。"]
    if mutation_enabled is False:
        return ["如需变异分数，请在后续运行中启用变异分析。"]
    if not test_files:
        return ["确认测试生成与落库链路，确保后续运行能产出可追踪的测试文件。"]
    return ["基于本次结果继续扩展边界条件、异常路径和回归场景测试。"]


def _format_percentage(value: MetricValue) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value) * 100:.2f}%"
    return MISSING_TEXT


def _format_count(value: MetricValue) -> str:
    if isinstance(value, int):
        return str(value)
    return MISSING_TEXT


def _format_text(value: str | None) -> str:
    if value is None:
        return MISSING_TEXT
    normalized = value.strip()
    return normalized or MISSING_TEXT
