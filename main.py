#!/usr/bin/env python3
"""COMET-L 主程序入口"""

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from comet.agent import AgentTools, ParallelPlannerAgent, PlannerAgent
from comet.agent.target_selector import TargetSelector
from comet.config import Settings
from comet.executor import JavaExecutor, MetricsCollector, MutationEvaluator
from comet.extractors import PatternExtractor, SpecExtractor
from comet.generators import MutantGenerator, StaticGuard, TestGenerator
from comet.knowledge import create_knowledge_base
from comet.llm import LLMClient
from comet.store import Database, KnowledgeStore
from comet.utils import ProjectScanner, SandboxManager
from comet.web import run_cli

logger = logging.getLogger(__name__)


def configure_runtime_environment(config: Settings) -> dict[str, str]:
    execution = config.execution
    env = execution.build_runtime_subprocess_env()

    for key in ("JAVA_HOME", "MAVEN_HOME", "M2_HOME", "PATH"):
        if key in env:
            os.environ[key] = env[key]

    return env


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="COMET-L: 基于 LLM 的测试变异协同进化系统")

    parser.add_argument("--project-path", type=str, required=True, help="目标 Java Maven 项目路径")

    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="配置文件路径（默认: config.yaml）",
    )

    parser.add_argument(
        "--max-iterations", type=int, default=None, help="最大迭代次数（覆盖配置文件）"
    )

    parser.add_argument("--budget", type=int, default=None, help="LLM 调用预算（覆盖配置文件）")

    parser.add_argument("--resume", type=str, default=None, help="从保存的状态恢复（状态文件路径）")

    parser.add_argument("--output-dir", type=str, default=None, help="输出目录（覆盖配置文件）")

    parser.add_argument("--debug", action="store_true", help="启用调试日志（DEBUG级别）")

    parser.add_argument(
        "--bug-reports-dir",
        type=str,
        default=None,
        help="Bug 报告目录（Markdown 文件），用于 RAG 知识库",
    )

    parser.add_argument(
        "--parallel",
        action="store_true",
        help="启用并行 Agent 模式（批量并行处理多个目标）",
    )

    parser.add_argument(
        "--parallel-targets",
        type=int,
        default=None,
        help="并行目标数（覆盖配置文件）",
    )

    return parser.parse_args()


def initialize_system(
    config: Settings,
    bug_reports_dir: Optional[str] = None,
    parallel_mode: bool = False,
):
    """
    初始化系统组件

    Args:
        config: 配置对象
        bug_reports_dir: Bug 报告目录（可选，用于 RAG 知识库）
        parallel_mode: 是否启用并行 Agent 模式

    Returns:
        初始化后的组件字典
    """
    logger.info("初始化 COMET-L 系统...")

    # 确保目录存在
    config.ensure_directories()

    runtime_env = configure_runtime_environment(config)
    target_env = config.execution.build_target_subprocess_env()
    runtime_java_cmd = config.execution.resolve_runtime_java_cmd()
    target_javac_cmd = config.execution.resolve_target_javac_cmd()
    mvn_cmd = config.execution.resolve_mvn_cmd()
    target_java_home = config.execution.get_target_java_home()

    runtime_java_home = config.execution.get_runtime_java_home()
    if runtime_java_home:
        logger.info(f"使用 runtime Java: {runtime_java_home}")
        logger.info(f"使用 runtime Java 可执行文件: {runtime_java_cmd}")

    if target_java_home:
        logger.info(f"使用 target Java: {target_java_home}")
        logger.info(f"使用 target javac 可执行文件: {target_javac_cmd}")

    if config.execution.maven_home:
        logger.info(f"使用配置的 MAVEN_HOME: {target_env['MAVEN_HOME']}")
        logger.info(f"使用 Maven 可执行文件: {mvn_cmd}")

    # 初始化 LLM 客户端
    llm_client = LLMClient(
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        model=config.llm.model,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        max_retries=config.execution.max_retries,
        supports_json_mode=config.llm.supports_json_mode,
        timeout=config.llm.timeout,
        reasoning_effort=config.llm.reasoning_effort,
        reasoning_enabled=config.llm.reasoning_enabled,
        verbosity=config.llm.verbosity,
    )
    logger.info(f"LLM 客户端初始化: {config.llm.model} (timeout={config.llm.timeout}s)")

    # 初始化存储
    db = Database(db_path=str(config.resolve_database_path()))
    knowledge_store = KnowledgeStore(db_path=str(config.resolve_knowledge_database_path()))

    # 创建知识库（支持 RAG 模式）
    knowledge_base = create_knowledge_base(
        store=knowledge_store,
        config=config.knowledge,
        llm_api_key=config.llm.api_key,
        vector_store_directory=str(config.resolve_vector_store_path()),
    )

    if config.knowledge.enabled:
        logger.info("预初始化 RAG 知识库...")
        if knowledge_base.initialize():
            logger.info("RAG 知识库预初始化完成")
        else:
            logger.warning("RAG 知识库预初始化失败，将在后续按需重试")

    # 如果提供了 Bug 报告目录，索引 Bug 报告
    if bug_reports_dir:
        bug_dir = Path(bug_reports_dir)
        if bug_dir.exists() and bug_dir.is_dir():
            try:
                count = knowledge_base.index_bug_reports(str(bug_dir))
                logger.info(f"已索引 {count} 个 Bug 报告: {bug_reports_dir}")
            except AttributeError:
                logger.warning("知识库不支持 RAG 模式，跳过 Bug 报告索引")
            except Exception as e:
                logger.warning(f"索引 Bug 报告失败: {e}")
        else:
            logger.warning(f"Bug 报告目录不存在: {bug_reports_dir}")

    logger.info("数据库和知识库已初始化")

    # 初始化提取器
    spec_extractor = SpecExtractor(llm_client)
    pattern_extractor = PatternExtractor(llm_client)

    # 初始化生成器
    mutant_generator = MutantGenerator(llm_client, knowledge_base)
    test_generator = TestGenerator(llm_client, knowledge_base)

    # 初始化 Java 运行时
    java_runtime_jar = "java-runtime/target/comet-runtime-1.0.0-jar-with-dependencies.jar"
    if not Path(java_runtime_jar).exists():
        logger.warning(
            f"Java 运行时 JAR 不存在: {java_runtime_jar}\n"
            "请先构建 Java 模块: cd java-runtime && mvn clean package"
        )

    java_executor = JavaExecutor(
        java_runtime_jar,
        java_cmd=runtime_java_cmd,
        test_timeout=config.execution.test_timeout,
        coverage_timeout=config.execution.coverage_timeout,
        env=runtime_env,
        target_java_home=target_java_home,
    )
    static_guard = StaticGuard(
        java_runtime_jar,
        javac_cmd=target_javac_cmd,
        mvn_cmd=mvn_cmd,
        env=target_env,
    )

    # 初始化沙箱和执行器
    sandbox_manager = SandboxManager(config.paths.sandbox)
    mutation_evaluator = MutationEvaluator(java_executor, sandbox_manager)
    metrics_collector = MetricsCollector()

    # 初始化 Agent
    tools = AgentTools()

    # 注入组件依赖到 AgentTools
    tools.project_path = ""  # 将在 run_evolution 中设置
    tools.db = db
    tools.java_executor = java_executor
    tools.mutant_generator = mutant_generator
    tools.test_generator = test_generator
    tools.static_guard = static_guard
    tools.mutation_evaluator = mutation_evaluator
    tools.metrics_collector = metrics_collector
    tools.knowledge_base = knowledge_base
    tools.pattern_extractor = pattern_extractor
    tools.sandbox_manager = sandbox_manager

    # 注入配置参数
    tools.config = config  # 注入系统配置（用于格式化配置等）
    try:
        tools.min_method_lines = config.evolution.min_method_lines
    except AttributeError:
        tools.min_method_lines = 5  # 默认值

    logger.info("Agent 工具集依赖注入完成")

    max_iterations = config.evolution.max_iterations
    budget = config.evolution.budget_llm_calls

    # 根据模式选择 Agent
    if parallel_mode or config.agent.parallel.enabled:
        logger.info("使用并行 Agent 模式")
        # 并行模式使用 ParallelPlannerAgent
        # 注意：ParallelPlannerAgent 需要更多参数，将在 run_evolution 中完成初始化
        planner = None  # 延迟初始化
        planner_type = "parallel"
    else:
        logger.info("使用标准 Agent 模式")
        planner = PlannerAgent(
            llm_client=llm_client,
            tools=tools,
            max_iterations=max_iterations,
            budget=budget,
            excellent_mutation_score=config.evolution.excellent_mutation_score,
            excellent_line_coverage=config.evolution.excellent_line_coverage,
            excellent_branch_coverage=config.evolution.excellent_branch_coverage,
        )
        planner_type = "standard"

    # 共享状态
    if planner:
        tools.state = planner.state

    # 初始化项目扫描器
    project_scanner = ProjectScanner(java_executor, db)

    logger.info("系统初始化完成")

    return {
        "config": config,
        "llm_client": llm_client,
        "db": db,
        "knowledge_base": knowledge_base,
        "spec_extractor": spec_extractor,
        "pattern_extractor": pattern_extractor,
        "mutant_generator": mutant_generator,
        "test_generator": test_generator,
        "java_executor": java_executor,
        "static_guard": static_guard,
        "sandbox_manager": sandbox_manager,
        "mutation_evaluator": mutation_evaluator,
        "metrics_collector": metrics_collector,
        "planner": planner,
        "planner_type": planner_type,
        "tools": tools,
        "project_scanner": project_scanner,
    }


def run_evolution(
    project_path: str, components: dict[str, Any], resume_state: Optional[str] = None
):
    """
    运行协同进化

    Args:
        project_path: 项目路径（原始项目）
        components: 系统组件
        resume_state: 恢复状态文件路径
    """
    logger.info(f"{'=' * 60}")
    logger.info("开始协同进化")
    logger.info(f"原项目路径: {project_path}")
    logger.info(f"{'=' * 60}")

    config = components["config"]
    sandbox_manager = components["sandbox_manager"]
    project_scanner = components["project_scanner"]
    planner_type = components.get("planner_type", "standard")
    runtime_snapshot_publisher = components.get("runtime_snapshot_publisher")

    # 扫描项目，建立类到文件的映射
    logger.info("扫描项目，建立类到文件的映射...")
    scan_result = project_scanner.scan_project(project_path, use_cache=True)
    logger.info(
        f"项目扫描完成: {scan_result['total_classes']} 个类, {scan_result['total_files']} 个文件"
    )

    # 创建工作空间沙箱
    logger.info("创建工作空间沙箱...")
    workspace_sandbox = sandbox_manager.create_workspace_sandbox(project_path)
    logger.info(f"工作空间沙箱: {workspace_sandbox}")

    # 根据模式创建/获取 planner
    if planner_type == "parallel":
        # 创建并行 Agent
        logger.info("初始化并行 Agent...")

        # 创建目标选择器
        target_selector = TargetSelector(
            project_path=workspace_sandbox,
            java_executor=components["java_executor"],
            database=components["db"],
            min_method_lines=config.evolution.min_method_lines,
        )

        # 获取并行配置
        parallel_config = config.agent.parallel

        planner = ParallelPlannerAgent(
            llm_client=components["llm_client"],
            tools=components["tools"],
            target_selector=target_selector,
            java_executor=components["java_executor"],
            sandbox_manager=sandbox_manager,
            database=components["db"],
            project_path=project_path,
            workspace_path=workspace_sandbox,
            max_parallel_targets=parallel_config.max_parallel_targets,
            max_eval_workers=parallel_config.max_eval_workers,
            max_iterations=config.evolution.max_iterations,
            budget=config.evolution.budget_llm_calls,
            timeout_per_target=parallel_config.timeout_per_target,
            excellent_mutation_score=config.evolution.excellent_mutation_score,
            excellent_line_coverage=config.evolution.excellent_line_coverage,
            excellent_branch_coverage=config.evolution.excellent_branch_coverage,
        )

        # 设置 tools 的状态
        components["tools"].state = planner.state
        components["tools"].project_path = workspace_sandbox
        components["tools"].original_project_path = project_path

        logger.info(
            f"并行 Agent 已初始化: "
            f"max_parallel_targets={parallel_config.max_parallel_targets}, "
            f"max_eval_workers={parallel_config.max_eval_workers}"
        )
    else:
        # 使用标准 Agent
        planner = components["planner"]

        # 设置 tools 使用沙箱路径
        if hasattr(planner, "tools") and hasattr(planner.tools, "project_path"):
            planner.tools.project_path = workspace_sandbox  # 工作路径（沙箱）
            planner.tools.original_project_path = project_path  # 保存原始路径
            logger.info(f"已设置沙箱路径到 AgentTools: {workspace_sandbox}")
            logger.info(f"原始项目路径: {project_path}")

    # 运行主循环（包括预处理）
    snapshot_stop_event = threading.Event()
    snapshot_thread: Optional[threading.Thread] = None
    current_phase = {"key": "running", "label": "Running"}

    def publish_runtime_snapshot(
        *, phase_key: Optional[str] = None, phase_label: Optional[str] = None
    ) -> None:
        if not callable(runtime_snapshot_publisher):
            return

        phase_payload = dict(current_phase)
        if phase_key is not None:
            phase_payload["key"] = phase_key
        if phase_label is not None:
            phase_payload["label"] = phase_label

        runtime_snapshot_publisher(state=planner.state, phase=phase_payload)

    def start_snapshot_publisher() -> None:
        nonlocal snapshot_thread
        if not callable(runtime_snapshot_publisher) or snapshot_thread is not None:
            return

        def publish_loop() -> None:
            while not snapshot_stop_event.wait(1.0):
                publish_runtime_snapshot()

        snapshot_thread = threading.Thread(
            target=publish_loop,
            daemon=True,
            name="comet-runtime-snapshot-publisher",
        )
        snapshot_thread.start()

    try:
        start_snapshot_publisher()

        # ===== 新增：并行预处理阶段 =====
        if not resume_state:  # 只在非恢复模式下执行预处理
            # 检查是否启用预处理
            try:
                preprocessing_enabled = config.preprocessing.enabled
            except AttributeError:
                preprocessing_enabled = True  # 默认启用

            if preprocessing_enabled:
                current_phase.update({"key": "preprocessing", "label": "Preprocessing"})
                publish_runtime_snapshot()
                logger.info("=" * 60)
                logger.info("开始并行预处理阶段")
                logger.info("=" * 60)

                try:
                    from comet.parallel_preprocessing import ParallelPreprocessor

                    preprocessor = ParallelPreprocessor(config, components)
                    preprocess_stats = preprocessor.run(project_path, workspace_sandbox)

                    logger.info("=" * 60)
                    logger.info("并行预处理完成")
                    logger.info(f"处理方法数: {preprocess_stats['total_methods']}")
                    logger.info(
                        f"成功: {preprocess_stats['success']}, 失败: {preprocess_stats['failed']}"
                    )
                    logger.info(f"总测试数: {preprocess_stats['total_tests']}")
                    logger.info(f"总变异体数: {preprocess_stats['total_mutants']}")
                    logger.info("=" * 60)

                    # 清理所有目标沙箱，释放资源
                    logger.info("清理并行预处理产生的临时沙箱...")
                    try:
                        sandbox_manager.cleanup_target_sandboxes()
                        sandbox_manager.cleanup_validation_sandboxes()
                        logger.info("临时沙箱清理完成")
                    except Exception as e:
                        logger.warning(f"清理临时沙箱失败（非致命错误）: {e}")

                    # 等待一小段时间，让系统回收文件描述符和进程资源
                    logger.info("等待系统回收资源...")
                    time.sleep(3)

                    # 运行初始覆盖率测试以生成JaCoCo报告（带重试）
                    logger.info("运行初始覆盖率测试以生成JaCoCo报告...")
                    java_executor = components["java_executor"]
                    max_retries = 3
                    coverage_result = None

                    for attempt in range(1, max_retries + 1):
                        try:
                            logger.info(f"尝试运行覆盖率测试 ({attempt}/{max_retries})...")
                            coverage_result = java_executor.run_tests_with_coverage(
                                workspace_sandbox
                            )

                            if coverage_result.get("success"):
                                logger.info("初始覆盖率测试成功")
                                if planner_type == "parallel":
                                    synced = planner.sync_workspace_coverage(wait_for_report=True)
                                    if not synced:
                                        logger.warning(
                                            "初始覆盖率报告同步失败，首批目标可能回退到默认选择策略"
                                        )
                                break
                            else:
                                # 提取详细错误信息
                                error_detail = (
                                    coverage_result.get("error")
                                    or coverage_result.get("output")
                                    or coverage_result.get("stderr")
                                    or coverage_result.get("stdout")
                                    or "Unknown error"
                                )
                                logger.warning(
                                    f"初始覆盖率测试失败 (尝试 {attempt}/{max_retries}): {error_detail[:500]}"
                                )

                                if attempt < max_retries:
                                    logger.info("等待 5 秒后重试...")
                                    time.sleep(5)
                                else:
                                    error_msg = f"初始覆盖率测试失败（已重试 {max_retries} 次）: {error_detail[:500]}"
                                    raise RuntimeError(error_msg)

                        except RuntimeError:
                            raise  # 重新抛出 RuntimeError（最后一次重试失败）
                        except Exception as e:
                            logger.warning(
                                f"运行初始覆盖率测试异常 (尝试 {attempt}/{max_retries}): {e}"
                            )
                            if attempt < max_retries:
                                logger.info("等待 5 秒后重试...")
                                time.sleep(5)
                            else:
                                raise RuntimeError(
                                    f"初始覆盖率测试异常（已重试 {max_retries} 次）: {e}"
                                )
                except KeyboardInterrupt:
                    # 中断信号会传播到外层处理
                    raise
                except RuntimeError as e:
                    # 初始覆盖率测试失败是关键错误，必须终止程序
                    logger.error(f"关键错误: {e}", exc_info=True)
                    raise
                except Exception as e:
                    logger.warning(f"并行预处理失败: {e}", exc_info=True)
                    logger.warning("将跳过预处理，继续正常流程")
            else:
                logger.info("并行预处理已禁用，跳过预处理阶段")

        current_phase.update({"key": "running", "label": "Running"})
        publish_runtime_snapshot()

        # 恢复状态（如果有）
        if resume_state and Path(resume_state).exists():
            logger.info(f"从状态恢复: {resume_state}")
            planner.load_state(resume_state)
            publish_runtime_snapshot()
        final_state = planner.run(
            stop_on_no_improvement_rounds=config.evolution.stop_on_no_improvement_rounds,
            min_improvement_threshold=config.evolution.min_improvement_threshold,
        )
        _ = final_state

        current_phase.update({"key": "completed", "label": "Completed"})
        publish_runtime_snapshot()

        # 保存最终状态
        state_file = f"{config.paths.output}/final_state.json"
        planner.save_state(state_file)
        logger.info(f"最终状态已保存: {state_file}")

        # 导出测试文件到原项目
        logger.info("=" * 60)
        logger.info("导出测试文件到原项目...")
        sandbox_manager.export_test_files("workspace", project_path)
        logger.info("=" * 60)

    except KeyboardInterrupt:
        logger.info("\n用户中断，保存当前状态...")
        state_file = f"{config.paths.output}/interrupted_state.json"
        planner.save_state(state_file)
        logger.info(f"状态已保存: {state_file}")
        publish_runtime_snapshot(phase_key="failed", phase_label="Interrupted")

        # 即使中断也导出测试文件
        logger.info("导出当前测试文件到原项目...")
        sandbox_manager.export_test_files("workspace", project_path)

        logger.info("可使用 --resume 参数恢复")

    except Exception as e:
        logger.error(f"运行出错: {e}", exc_info=True)
        publish_runtime_snapshot(phase_key="failed", phase_label="Failed")
        # 出错时也尝试导出已生成的测试
        try:
            logger.info("尝试导出已生成的测试文件...")
            sandbox_manager.export_test_files("workspace", project_path)
        except Exception:
            pass
        raise
    finally:
        snapshot_stop_event.set()
        if snapshot_thread is not None:
            snapshot_thread.join(timeout=1)


def main():
    """主函数"""
    args = parse_args()

    # 如果启用了debug模式，设置日志级别为DEBUG
    try:
        exit_code = run_cli(
            args,
            system_initializer=initialize_system,
            evolution_runner=run_evolution,
        )
        if exit_code != 0:
            sys.exit(exit_code)

    except Exception as e:
        logger.error(f"运行失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
