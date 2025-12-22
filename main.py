#!/usr/bin/env python3
"""COMET-L 主程序入口"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from comet.config import Settings
from comet.llm import LLMClient
from comet.store import Database, KnowledgeStore
from comet.knowledge import KnowledgeBase
from comet.extractors import SpecExtractor, PatternExtractor
from comet.generators import MutantGenerator, TestGenerator, StaticGuard
from comet.executor import JavaExecutor, MutationEvaluator, MetricsCollector
from comet.agent import PlannerAgent, AgentTools, AgentState
from comet.utils import SandboxManager, ProjectScanner


class ColoredFormatter(logging.Formatter):
    """为终端输出添加 ANSI 颜色的格式化器"""

    RESET = "\033[0m"
    COLORS = {
        logging.DEBUG: "\033[36m",  # 青色
        logging.INFO: "\033[32m",  # 绿色
        logging.WARNING: "\033[33m",  # 黄色
        logging.ERROR: "\033[31m",  # 红色
        logging.CRITICAL: "\033[35m",  # 洋红
    }

    def format(self, record: logging.LogRecord) -> str:
        original_levelname = record.levelname
        color = self.COLORS.get(record.levelno, "")
        if color:
            record.levelname = f"{color}{record.levelname}{self.RESET}"
        try:
            return super().format(record)
        finally:
            # 恢复，避免影响其他 handler（例如文件）
            record.levelname = original_levelname


LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# 配置日志：文件使用纯文本，终端使用彩色输出
file_handler = logging.FileHandler("comet.log", encoding="utf-8")
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(ColoredFormatter(LOG_FORMAT))

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[file_handler, console_handler],
)

logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="COMET-L: 基于 LLM 的测试变异协同进化系统"
    )

    parser.add_argument(
        "--project-path", type=str, required=True, help="目标 Java Maven 项目路径"
    )

    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="配置文件路径（默认: config.yaml）",
    )

    parser.add_argument(
        "--max-iterations", type=int, default=None, help="最大迭代次数（覆盖配置文件）"
    )

    parser.add_argument(
        "--budget", type=int, default=None, help="LLM 调用预算（覆盖配置文件）"
    )

    parser.add_argument(
        "--resume", type=str, default=None, help="从保存的状态恢复（状态文件路径）"
    )

    parser.add_argument(
        "--output-dir", type=str, default=None, help="输出目录（覆盖配置文件）"
    )

    parser.add_argument(
        "--debug", action="store_true", help="启用调试日志（DEBUG级别）"
    )

    return parser.parse_args()


def initialize_system(config: Settings):
    """
    初始化系统组件

    Args:
        config: 配置对象

    Returns:
        初始化后的组件字典
    """
    logger.info("初始化 COMET-L 系统...")

    # 确保目录存在
    config.ensure_directories()

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
        verbosity=config.llm.verbosity,
    )
    logger.info(f"LLM 客户端初始化: {config.llm.model} (timeout={config.llm.timeout}s)")

    # 初始化存储
    db = Database(db_path=f"{config.paths.cache}/comet.db")
    knowledge_store = KnowledgeStore(db_path=f"{config.paths.cache}/knowledge.db")
    knowledge_base = KnowledgeBase(knowledge_store)
    logger.info("数据库和知识库已初始化")

    # 初始化提取器
    spec_extractor = SpecExtractor(llm_client)
    pattern_extractor = PatternExtractor(llm_client)

    # 初始化生成器
    mutant_generator = MutantGenerator(llm_client, knowledge_base)
    test_generator = TestGenerator(llm_client, knowledge_base)

    # 初始化 Java 运行时
    java_runtime_jar = (
        "java-runtime/target/comet-runtime-1.0.0-jar-with-dependencies.jar"
    )
    if not Path(java_runtime_jar).exists():
        logger.warning(
            f"Java 运行时 JAR 不存在: {java_runtime_jar}\n"
            "请先构建 Java 模块: cd java-runtime && mvn clean package"
        )

    java_executor = JavaExecutor(
        java_runtime_jar,
        test_timeout=config.execution.test_timeout,
        coverage_timeout=config.execution.coverage_timeout,
    )
    static_guard = StaticGuard(java_runtime_jar)

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
    try:
        tools.min_method_lines = config.evolution.min_method_lines
    except AttributeError:
        tools.min_method_lines = 5  # 默认值

    logger.info("Agent 工具集依赖注入完成")

    max_iterations = config.evolution.max_iterations
    budget = config.evolution.budget_llm_calls

    planner = PlannerAgent(
        llm_client=llm_client,
        tools=tools,
        max_iterations=max_iterations,
        budget=budget,
        excellent_mutation_score=config.evolution.excellent_mutation_score,
        excellent_line_coverage=config.evolution.excellent_line_coverage,
        excellent_branch_coverage=config.evolution.excellent_branch_coverage,
    )

    # 共享状态
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
        "project_scanner": project_scanner,
    }


def run_evolution(
    project_path: str, components: dict, resume_state: Optional[str] = None
):
    """
    运行协同进化

    Args:
        project_path: 项目路径（原始项目）
        components: 系统组件
        resume_state: 恢复状态文件路径
    """
    logger.info(f"{'='*60}")
    logger.info("开始协同进化")
    logger.info(f"原项目路径: {project_path}")
    logger.info(f"{'='*60}")

    planner = components["planner"]
    config = components["config"]
    sandbox_manager = components["sandbox_manager"]
    project_scanner = components["project_scanner"]

    # 扫描项目，建立类到文件的映射
    logger.info("扫描项目，建立类到文件的映射...")
    scan_result = project_scanner.scan_project(project_path, use_cache=True)
    logger.info(
        f"项目扫描完成: {scan_result['total_classes']} 个类, "
        f"{scan_result['total_files']} 个文件"
    )

    # 创建工作空间沙箱
    logger.info("创建工作空间沙箱...")
    workspace_sandbox = sandbox_manager.create_workspace_sandbox(project_path)
    logger.info(f"工作空间沙箱: {workspace_sandbox}")

    # 设置 tools 使用沙箱路径
    if hasattr(planner, "tools") and hasattr(planner.tools, "project_path"):
        planner.tools.project_path = workspace_sandbox  # 工作路径（沙箱）
        planner.tools.original_project_path = project_path  # 保存原始路径
        logger.info(f"已设置沙箱路径到 AgentTools: {workspace_sandbox}")
        logger.info(f"原始项目路径: {project_path}")

    # 运行主循环（包括预处理）
    try:
        # ===== 新增：并行预处理阶段 =====
        if not resume_state:  # 只在非恢复模式下执行预处理
            # 检查是否启用预处理
            try:
                preprocessing_enabled = config.preprocessing.enabled
            except AttributeError:
                preprocessing_enabled = True  # 默认启用

            if preprocessing_enabled:
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
                    import time

                    logger.info("等待系统回收资源...")
                    time.sleep(3)

                    # 运行初始覆盖率测试以生成JaCoCo报告（带重试）
                    logger.info("运行初始覆盖率测试以生成JaCoCo报告...")
                    java_executor = components["java_executor"]
                    max_retries = 3
                    coverage_result = None

                    for attempt in range(1, max_retries + 1):
                        try:
                            logger.info(
                                f"尝试运行覆盖率测试 ({attempt}/{max_retries})..."
                            )
                            coverage_result = java_executor.run_tests_with_coverage(
                                workspace_sandbox
                            )

                            if coverage_result.get("success"):
                                logger.info("初始覆盖率测试成功")
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
                                logger.error(
                                    f"初始覆盖率测试失败 (尝试 {attempt}/{max_retries}): {error_detail[:500]}"
                                )

                                if attempt < max_retries:
                                    logger.info(f"等待 5 秒后重试...")
                                    time.sleep(5)
                                else:
                                    error_msg = f"初始覆盖率测试失败（已重试 {max_retries} 次）: {error_detail[:500]}"
                                    raise RuntimeError(error_msg)

                        except RuntimeError:
                            raise  # 重新抛出 RuntimeError（最后一次重试失败）
                        except Exception as e:
                            logger.error(
                                f"运行初始覆盖率测试异常 (尝试 {attempt}/{max_retries}): {e}"
                            )
                            if attempt < max_retries:
                                logger.info(f"等待 5 秒后重试...")
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
                    logger.error(f"并行预处理失败: {e}", exc_info=True)
                    logger.warning("将跳过预处理，继续正常流程")
            else:
                logger.info("并行预处理已禁用，跳过预处理阶段")

        # 恢复状态（如果有）
        if resume_state and Path(resume_state).exists():
            logger.info(f"从状态恢复: {resume_state}")
            planner.load_state(resume_state)
        final_state = planner.run(
            stop_on_no_improvement_rounds=config.evolution.stop_on_no_improvement_rounds,
            min_improvement_threshold=config.evolution.min_improvement_threshold,
        )

        # 保存最终状态
        state_file = f"{config.paths.output}/final_state.json"
        planner.save_state(state_file)
        logger.info(f"最终状态已保存: {state_file}")

        # 导出测试文件到原项目
        logger.info("=" * 60)
        logger.info("导出测试文件到原项目...")
        sandbox_manager.export_test_files("workspace", project_path)
        logger.info("=" * 60)

        # 输出摘要
        print_summary(final_state, components["metrics_collector"])

    except KeyboardInterrupt:
        logger.info("\n用户中断，保存当前状态...")
        state_file = f"{config.paths.output}/interrupted_state.json"
        planner.save_state(state_file)
        logger.info(f"状态已保存: {state_file}")

        # 即使中断也导出测试文件
        logger.info("导出当前测试文件到原项目...")
        sandbox_manager.export_test_files("workspace", project_path)

        logger.info("可使用 --resume 参数恢复")

    except Exception as e:
        logger.error(f"运行出错: {e}", exc_info=True)
        # 出错时也尝试导出已生成的测试
        try:
            logger.info("尝试导出已生成的测试文件...")
            sandbox_manager.export_test_files("workspace", project_path)
        except:
            pass
        raise


def print_summary(state: AgentState, metrics_collector: MetricsCollector):
    """打印运行摘要"""
    # 从 metrics_collector 获取历史趋势
    summary = metrics_collector.get_summary()

    # 从 state 获取最终的准确状态（更可靠）
    final_mutation_score = state.mutation_score
    final_line_coverage = state.line_coverage
    total_iterations = state.iteration
    total_tests = state.total_tests
    llm_calls = state.llm_calls

    # 从 metrics_collector 获取初始值（如果有）
    initial_mutation_score = summary.get("initial_mutation_score", 0.0)
    initial_coverage = summary.get("initial_coverage", 0.0)

    # 如果 metrics_collector 没有历史记录，使用 state 的当前值作为最终值
    if not metrics_collector.history:
        initial_mutation_score = 0.0
        initial_coverage = 0.0

    print("\n" + "=" * 60)
    print("运行摘要")
    print("=" * 60)
    print(f"总迭代次数: {total_iterations}")
    print(f"变异分数: {initial_mutation_score:.3f} -> {final_mutation_score:.3f}")
    print(f"行覆盖率: {initial_coverage:.3f} -> {final_line_coverage:.3f}")
    print(f"总测试数: {total_tests}")
    print(f"LLM 调用次数: {llm_calls}")
    print("=" * 60)


def main():
    """主函数"""
    args = parse_args()

    # 如果启用了debug模式，设置日志级别为DEBUG
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("已启用调试模式 (DEBUG 日志)")

    try:
        # 加载配置
        logger.info(f"加载配置: {args.config}")
        config = Settings.from_yaml_or_default(args.config)

        # 覆盖配置
        if args.max_iterations:
            config.evolution.max_iterations = args.max_iterations
        if args.budget:
            config.evolution.budget_llm_calls = args.budget
        if args.output_dir:
            config.paths.output = args.output_dir

        # 验证项目路径
        project_path = Path(args.project_path)
        if not project_path.exists():
            logger.error(f"项目路径不存在: {args.project_path}")
            sys.exit(1)

        if not (project_path / "pom.xml").exists():
            logger.error(f"不是有效的 Maven 项目: {args.project_path}")
            sys.exit(1)

        # 初始化系统
        components = initialize_system(config)

        # 运行协同进化
        run_evolution(
            project_path=str(project_path),
            components=components,
            resume_state=args.resume,
        )

        logger.info("COMET-L 运行完成")

    except Exception as e:
        logger.error(f"运行失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
