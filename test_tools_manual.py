"""
手动测试工具脚本

用法示例:
    python test_tools_manual.py --tool select_target --project examples/calculator-demo
    python test_tools_manual.py --tool generate_mutants --project examples/calculator-demo --class Calculator
    python test_tools_manual.py --tool generate_tests --project examples/calculator-demo --class Calculator --method add
    python test_tools_manual.py --tool run_evaluation --project examples/calculator-demo
"""

import argparse
import logging
import sys
from pathlib import Path

# 设置日志格式
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('test_tools_manual.log', mode='w', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)


def init_system(project_path: str, config_path: str = "config.yaml"):
    """初始化系统组件"""
    from comet.config.settings import Settings
    from comet.llm.client import LLMClient
    from comet.store.database import Database
    from comet.knowledge.knowledge_base import KnowledgeBase
    from comet.executor.java_executor import JavaExecutor
    from comet.generators.mutant_generator import MutantGenerator
    from comet.generators.test_generator import TestGenerator
    from comet.generators.static_guard import StaticGuard
    from comet.executor.mutation_evaluator import MutationEvaluator
    from comet.executor.metrics import MetricsCollector
    from comet.extractors.pattern_extractor import PatternExtractor
    from comet.agent.tools import AgentTools
    from comet.utils.sandbox import SandboxManager

    # 加载配置
    logger.info(f"加载配置: {config_path}")
    config = Settings.from_yaml(config_path)
    config.ensure_directories()  # 确保所有目录存在

    # 初始化 LLM 客户端
    llm_client = LLMClient(
        api_key=config.llm.api_key,
        model=config.llm.model,
        base_url=config.llm.base_url,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens
    )
    logger.info(f"LLM 客户端初始化: {config.llm.model}")

    # 初始化数据库
    db_path = f"{config.paths.output}/comet.db"
    db = Database(db_path)

    # 初始化知识库
    kb_path = f"{config.paths.output}/knowledge_base.json"
    kb = KnowledgeBase(kb_path)

    # 初始化 Java 执行器（这里仍使用默认路径，可以考虑加入到配置中）
    java_runtime_jar = './java-runtime/target/comet-runtime-1.0.0-jar-with-dependencies.jar'
    java_executor = JavaExecutor(java_runtime_jar)

    # 初始化沙箱管理器
    sandbox_manager = SandboxManager(config.paths.sandbox)

    # 初始化各个组件
    mutant_generator = MutantGenerator(llm_client, kb)
    test_generator = TestGenerator(llm_client, kb)
    static_guard = StaticGuard(java_runtime_jar)
    mutation_evaluator = MutationEvaluator(java_executor, sandbox_manager)
    metrics_collector = MetricsCollector()
    pattern_extractor = PatternExtractor(llm_client)

    # 初始化工具集
    tools = AgentTools()
    tools.project_path = project_path
    tools.db = db
    tools.java_executor = java_executor
    tools.mutant_generator = mutant_generator
    tools.test_generator = test_generator
    tools.static_guard = static_guard
    tools.mutation_evaluator = mutation_evaluator
    tools.metrics_collector = metrics_collector
    tools.knowledge_base = kb
    tools.pattern_extractor = pattern_extractor
    tools.sandbox_manager = sandbox_manager

    logger.info("系统初始化完成")
    return tools


def test_select_target(tools: 'AgentTools', criteria: str = 'coverage'):
    """测试选择目标工具"""
    logger.info("=" * 60)
    logger.info("测试: select_target")
    logger.info("=" * 60)

    result = tools.select_target(criteria=criteria)

    logger.info(f"结果: {result}")
    return result


def test_generate_mutants(tools: 'AgentTools', class_name: str, num_mutations: int = 5):
    """测试生成变异体工具"""
    logger.info("=" * 60)
    logger.info(f"测试: generate_mutants for {class_name}")
    logger.info("=" * 60)

    result = tools.generate_mutants(
        class_name=class_name,
        num_mutations=num_mutations
    )

    logger.info(f"结果: {result}")
    return result


def test_generate_tests(tools: 'AgentTools', class_name: str, method_name: str, num_tests: int = 3):
    """测试生成测试工具"""
    logger.info("=" * 60)
    logger.info(f"测试: generate_tests for {class_name}.{method_name}")
    logger.info("=" * 60)

    result = tools.generate_tests(
        class_name=class_name,
        method_name=method_name,
        num_tests=num_tests
    )

    logger.info(f"结果: {result}")
    return result


def test_run_evaluation(tools: 'AgentTools'):
    """测试运行评估工具"""
    logger.info("=" * 60)
    logger.info("测试: run_evaluation")
    logger.info("=" * 60)

    result = tools.run_evaluation()

    logger.info(f"结果: {result}")
    return result


def test_update_knowledge(tools: 'AgentTools', knowledge_type: str, data: dict):
    """测试更新知识库工具"""
    logger.info("=" * 60)
    logger.info(f"测试: update_knowledge ({knowledge_type})")
    logger.info("=" * 60)

    result = tools.update_knowledge(type=knowledge_type, data=data)

    logger.info(f"结果: {result}")
    return result


def main():
    parser = argparse.ArgumentParser(description='手动测试 COMET-L 工具')
    parser.add_argument('--tool', required=True,
                        choices=['select_target', 'generate_mutants', 'generate_tests',
                                'run_evaluation', 'update_knowledge', 'trigger_pitest'],
                        help='要测试的工具名称')
    parser.add_argument('--project', required=True, help='项目路径')
    parser.add_argument('--config', default='config.yaml', help='配置文件路径')

    # 工具特定参数
    parser.add_argument('--class', dest='class_name', help='类名（用于 generate_mutants, generate_tests）')
    parser.add_argument('--method', dest='method_name', help='方法名（用于 generate_tests）')
    parser.add_argument('--criteria', default='coverage', help='选择标准（用于 select_target）')
    parser.add_argument('--num-mutations', type=int, default=5, help='变异体数量')
    parser.add_argument('--num-tests', type=int, default=3, help='测试数量')

    args = parser.parse_args()

    try:
        # 初始化系统
        tools = init_system(args.project, args.config)

        # 根据工具名称调用对应的测试函数
        if args.tool == 'select_target':
            result = test_select_target(tools, args.criteria)

        elif args.tool == 'generate_mutants':
            if not args.class_name:
                logger.error("generate_mutants 需要 --class 参数")
                sys.exit(1)
            result = test_generate_mutants(tools, args.class_name, args.num_mutations)

        elif args.tool == 'generate_tests':
            if not args.class_name or not args.method_name:
                logger.error("generate_tests 需要 --class 和 --method 参数")
                sys.exit(1)
            result = test_generate_tests(tools, args.class_name, args.method_name, args.num_tests)

        elif args.tool == 'run_evaluation':
            result = test_run_evaluation(tools)

        elif args.tool == 'update_knowledge':
            logger.error("update_knowledge 需要通过 Python API 调用，暂不支持命令行")
            sys.exit(1)

        elif args.tool == 'trigger_pitest':
            logger.info("trigger_pitest 功能暂未实现")
            result = tools.trigger_pitest(args.project)

        else:
            logger.error(f"未知工具: {args.tool}")
            sys.exit(1)

        # 打印最终结果
        logger.info("=" * 60)
        logger.info("测试完成")
        logger.info("=" * 60)
        logger.info(f"工具: {args.tool}")
        logger.info(f"结果: {result}")
        logger.info("详细日志已保存到: test_tools_manual.log")

    except Exception as e:
        logger.error(f"测试失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
