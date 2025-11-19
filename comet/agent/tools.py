"""Agent 工具集"""

import logging
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ToolMetadata:
    """工具元数据"""
    name: str
    description: str
    params: Dict[str, Any] = field(default_factory=dict)  # 支持任意类型的参数值
    when_to_use: str = ""
    notes: List[str] = field(default_factory=list)


class AgentTools:
    """Agent 工具集 - 封装各个管线为标准接口"""

    def __init__(self):
        """初始化工具集"""
        self.tools: Dict[str, Callable] = {}
        self.metadata: Dict[str, ToolMetadata] = {}

        # 组件依赖（将在 main.py 中注入）
        self.project_path: Optional[str] = None
        self.db = None
        self.java_executor = None
        self.mutant_generator = None
        self.test_generator = None
        self.static_guard = None
        self.mutation_evaluator = None
        self.metrics_collector = None
        self.knowledge_base = None
        self.pattern_extractor = None
        self.sandbox_manager = None
        self.state = None

        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册默认工具"""
        # 注册 select_target
        self.register(
            name="select_target",
            func=self.select_target,
            metadata=ToolMetadata(
                name="select_target",
                description="选择要处理的类/方法",
                params={},  # 无参数（空对象 {}）
                when_to_use="当前没有选中目标时",
                notes=[]
            )
        )

        # 注册 generate_mutants
        self.register(
            name="generate_mutants",
            func=self.generate_mutants,
            metadata=ToolMetadata(
                name="generate_mutants",
                description="生成变异体",
                params={
                    "class_name": "类名",
                    "method_name": "方法名"
                },
                when_to_use="已有目标但变异体数量为 0 时",
                notes=["如果有当前选中的目标方法，必须传递 method_name 参数"]
            )
        )

        # 注册 generate_tests
        self.register(
            name="generate_tests",
            func=self.generate_tests,
            metadata=ToolMetadata(
                name="generate_tests",
                description="生成测试",
                params={
                    "class_name": "类名",
                    "method_name": "方法名"
                },
                when_to_use="已有目标但测试数量为 0 或较少时",
                notes=[]
            )
        )

        # 注册 run_evaluation
        self.register(
            name="run_evaluation",
            func=self.run_evaluation,
            metadata=ToolMetadata(
                name="run_evaluation",
                description="执行评估",
                params={},  # 无参数（空对象 {}）
                when_to_use="已有变异体和测试后",
                notes=[]
            )
        )

        # 注册 update_knowledge
        self.register(
            name="update_knowledge",
            func=self.update_knowledge,
            metadata=ToolMetadata(
                name="update_knowledge",
                description="更新知识库",
                params={
                    "type": "knowledge类型",
                    "data": {"具体数据字段": "..."}
                },
                when_to_use="评估完成后，从结果学习（暂时可选，系统会自动学习）",
                notes=[]
            )
        )

        # 注册 trigger_pitest
        self.register(
            name="trigger_pitest",
            func=self.trigger_pitest,
            metadata=ToolMetadata(
                name="trigger_pitest",
                description="调用传统 PIT 变异",
                params={
                    "class_name": "类名"
                },
                when_to_use="可选，需要传统变异测试时",
                notes=[]
            )
        )

    def register(self, name: str, func: Callable, metadata: Optional[ToolMetadata] = None) -> None:
        """
        注册工具

        Args:
            name: 工具名称
            func: 工具函数
            metadata: 工具元数据（可选）
        """
        self.tools[name] = func
        if metadata:
            self.metadata[name] = metadata
        logger.debug(f"注册工具: {name}")

    def call(self, name: str, **params) -> Any:
        """
        调用工具

        Args:
            name: 工具名称
            **params: 工具参数

        Returns:
            工具执行结果
        """
        if name not in self.tools:
            raise ValueError(f"未知工具: {name}")

        logger.info(f"调用工具: {name} with {params}")
        return self.tools[name](**params)

    def get_tools_metadata(self) -> List[ToolMetadata]:
        """
        获取所有工具的元数据

        Returns:
            工具元数据列表
        """
        return list(self.metadata.values())

    def get_tools_description(self) -> str:
        """
        生成工具描述文本（用于 LLM 提示词）

        Returns:
            格式化的工具描述文本
        """
        lines = []
        for i, meta in enumerate(self.metadata.values(), 1):
            lines.append(f"{i}. **{meta.name}** - {meta.description}")

            # 参数说明
            if meta.params:
                import json
                params_str = json.dumps(meta.params, ensure_ascii=False, indent=2)
                lines.append(f"   参数：{params_str}")
            else:
                lines.append(f"   参数：无（空对象 {{}}）")

            # 使用时机
            if meta.when_to_use:
                lines.append(f"   使用时机：{meta.when_to_use}")

            # 注意事项
            for note in meta.notes:
                lines.append(f"   **注意**：{note}")

            lines.append("")  # 空行分隔

        return "\n".join(lines)

    # 工具实现

    def select_target(self, criteria: str = "coverage") -> Dict[str, Any]:
        """选择目标类和方法"""
        if not self.project_path or not self.java_executor or not self.db:
            logger.error("select_target: 缺少必要组件")
            return {"class_name": None, "method_name": None}

        from .target_selector import TargetSelector
        selector = TargetSelector(self.project_path, self.java_executor, self.db)
        target = selector.select(criteria)

        # 保存到状态
        if self.state and target.get("class_name"):
            self.state.current_target = target

        logger.info(f"已选择目标: {target.get('class_name')}.{target.get('method_name')}")
        return target

    def generate_mutants(self, class_name: str, method_name: Optional[str] = None, num_mutations: int = 5) -> Dict[str, Any]:
        """
        生成变异体

        Args:
            class_name: 类名
            method_name: 目标方法名（可选，如果指定则只生成该方法的变异体）
            num_mutations: 变异体数量
        """
        if not all([self.project_path, self.mutant_generator, self.static_guard, self.db]):
            logger.error("generate_mutants: 缺少必要组件")
            return {"generated": 0}

        from ..utils.project_utils import find_java_file
        from ..utils.code_utils import extract_class_from_file

        # 查找源文件
        file_path = find_java_file(self.project_path, class_name)
        if not file_path:
            logger.error(f"未找到类文件: {class_name}")
            return {"generated": 0}

        # 读取源代码
        class_code = extract_class_from_file(str(file_path))

        # 生成变异体（如果指定了目标方法，在生成时聚焦该方法）
        mutants = self.mutant_generator.generate_mutants(
            class_name=class_name,
            class_code=class_code,
            num_mutations=num_mutations,
            target_method=method_name,  # 传递目标方法
        )

        if not mutants:
            logger.warning(f"未生成任何变异体: {class_name}")
            return {"generated": 0}

        # 静态过滤
        valid_mutants = self.static_guard.filter_mutants(mutants, str(file_path))

        # 保存到数据库
        for mutant in valid_mutants:
            mutant.patch.file_path = str(file_path)
            self.db.save_mutant(mutant)

        logger.info(f"成功生成并保存 {len(valid_mutants)} 个变异体")
        return {
            "generated": len(valid_mutants),
            "mutant_ids": [m.id for m in valid_mutants],
        }

    def generate_tests(
        self,
        class_name: str,
        method_name: str,
        num_tests: int = 3
    ) -> Dict[str, Any]:
        """生成测试"""
        if not all([self.project_path, self.test_generator, self.java_executor, self.db]):
            logger.error("generate_tests: 缺少必要组件")
            return {"generated": 0}

        from ..utils.project_utils import find_java_file, write_test_file
        from ..utils.code_utils import extract_class_from_file, extract_method_signature

        # 读取源代码
        file_path = find_java_file(self.project_path, class_name)
        if not file_path:
            logger.error(f"未找到类文件: {class_name}")
            return {"generated": 0}

        class_code = extract_class_from_file(str(file_path))
        method_sig = extract_method_signature(class_code, method_name) if method_name else None

        # 获取幸存变异体和覆盖缺口
        all_mutants = self.db.get_all_mutants()
        survived = self.metrics_collector.get_survived_mutants_for_method(
            class_name, method_name, all_mutants
        ) if self.metrics_collector else []

        gaps = {}  # 简化实现，暂不使用覆盖缺口

        # 生成测试
        test_case = self.test_generator.generate_tests_for_method(
            class_name=class_name,
            method_signature=method_sig or f"public void {method_name}()",
            class_code=class_code,
            survived_mutants=survived,
            coverage_gaps=gaps,
            num_tests=num_tests,
        )

        if not test_case:
            logger.warning(f"测试生成失败: {class_name}.{method_name}")
            return {"generated": 0}

        # 写入测试文件
        test_file = write_test_file(
            project_path=self.project_path,
            package_name=test_case.package_name,
            test_code=test_case.full_code,
            test_class_name=test_case.class_name,
        )

        if not test_file:
            logger.error("写入测试文件失败")
            return {"generated": 0}

        # 验证编译
        compile_result = self.java_executor.compile_tests(self.project_path)
        if compile_result.get("success"):
            test_case.compile_success = True
            logger.info(f"测试编译成功: {test_case.class_name}")
        else:
            compile_error = compile_result.get('error', 'Unknown error')
            logger.warning(f"测试编译失败: {compile_error}")
            test_case.compile_error = str(compile_error)

            # 尝试自动修复
            logger.info("尝试自动修复编译错误...")
            fixed_test_case = self.test_generator.regenerate_with_feedback(
                test_case=test_case,
                compile_error=compile_error,
                max_retries=2,
            )

            if fixed_test_case:
                # 重新写入修复后的测试文件
                from ..utils.project_utils import write_test_file
                test_file = write_test_file(
                    project_path=self.project_path,
                    package_name=fixed_test_case.package_name,
                    test_code=fixed_test_case.full_code,
                    test_class_name=fixed_test_case.class_name,
                )

                # 重新编译验证
                compile_result2 = self.java_executor.compile_tests(self.project_path)
                if compile_result2.get("success"):
                    fixed_test_case.compile_success = True
                    fixed_test_case.compile_error = None
                    logger.info(f"修复成功！测试现在可以编译: {fixed_test_case.class_name}")
                    test_case = fixed_test_case
                else:
                    logger.error(f"修复后仍无法编译: {compile_result2.get('error', 'Unknown error')}")
                    fixed_test_case.compile_error = str(compile_result2.get('error'))
                    test_case = fixed_test_case
            else:
                logger.error("自动修复失败")

        # 保存
        self.db.save_test_case(test_case)

        return {
            "generated": len(test_case.methods),
            "test_id": test_case.id,
            "compile_success": test_case.compile_success,
        }

    def run_evaluation(self) -> Dict[str, Any]:
        """运行评估并构建击杀矩阵"""
        if not all([self.project_path, self.mutation_evaluator, self.java_executor, self.db]):
            logger.error("run_evaluation: 缺少必要组件")
            return {"evaluated": 0}

        # 获取所有待评估的变异体和测试
        mutants = self.db.get_valid_mutants()  # 获取有效的变异体
        test_cases = self.db.get_all_tests()

        if not mutants:
            logger.warning("没有变异体需要评估")
            return {"evaluated": 0, "killed": 0, "mutation_score": 0.0}

        if not test_cases:
            logger.warning("没有测试用例")
            return {"evaluated": len(mutants), "killed": 0, "mutation_score": 0.0}

        logger.info(f"开始评估 {len(mutants)} 个变异体 和 {len(test_cases)} 个测试")

        # 构建击杀矩阵
        kill_matrix = self.mutation_evaluator.build_kill_matrix(
            mutants=mutants,
            test_cases=test_cases,
            project_path=self.project_path,
        )

        # 运行覆盖率分析
        coverage_data = None
        try:
            coverage_result = self.java_executor.run_tests_with_coverage(self.project_path)
            if coverage_result.get("success"):
                coverage_data = coverage_result
        except Exception as e:
            logger.warning(f"覆盖率分析失败: {e}")

        # 更新度量指标
        if self.metrics_collector:
            self.metrics_collector.update_from_evaluation(
                mutants=mutants,
                test_cases=test_cases,
                kill_matrix=kill_matrix,
                coverage_data=coverage_data,
            )

        killed_count = len([m for m in mutants if not m.survived])
        mutation_score = killed_count / len(mutants) if mutants else 0.0

        logger.info(f"评估完成: {killed_count}/{len(mutants)} 个变异体被击杀")

        return {
            "evaluated": len(mutants),
            "killed": killed_count,
            "survived": len(mutants) - killed_count,
            "mutation_score": mutation_score,
        }

    def update_knowledge(self, type: str = None, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """更新知识库"""
        if not self.knowledge_base:
            logger.error("update_knowledge: 知识库未初始化")
            return {"updated": False}

        # 如果没有提供参数，返回成功但不做任何操作
        if type is None or data is None:
            logger.info("update_knowledge: 无具体参数，跳过知识库更新")
            return {"updated": True, "message": "No specific knowledge to update"}

        try:
            if type == "pattern":
                from ..models import Pattern
                pattern = Pattern(**data)
                self.knowledge_base.add_pattern(pattern)
                logger.info(f"添加模式: {pattern.name}")
                return {"updated": True, "pattern_id": pattern.id}

            elif type == "contract":
                from ..models import Contract
                contract = Contract(**data)
                self.knowledge_base.add_contract(contract)
                logger.info(f"添加契约: {contract.class_name}.{contract.method_name}")
                return {"updated": True, "contract_id": contract.id}

            elif type == "survived_mutant":
                # 从幸存变异体学习新模式
                mutant_id = data.get("mutant_id")
                if not mutant_id or not self.db or not self.pattern_extractor:
                    return {"updated": False, "error": "缺少必要信息"}

                mutant = self.db.get_mutant(mutant_id)
                if not mutant:
                    return {"updated": False, "error": "变异体不存在"}

                pattern = self.pattern_extractor.extract_from_surviving_mutant(
                    mutant_code=mutant.patch.mutated_code,
                    original_code=mutant.patch.original_code,
                    semantic_intent=mutant.semantic_intent,
                )

                if pattern:
                    self.knowledge_base.add_pattern(pattern)
                    logger.info(f"从幸存变异体学习到新模式: {pattern.name}")
                    return {"updated": True, "pattern_id": pattern.id}
                else:
                    return {"updated": False, "error": "模式提取失败"}

            else:
                logger.warning(f"未知的知识类型: {type}")
                return {"updated": False, "error": f"未知类型: {type}"}

        except Exception as e:
            logger.error(f"更新知识库失败: {e}")
            return {"updated": False, "error": str(e)}

    def trigger_pitest(self, project_path: str) -> Dict[str, Any]:
        """触发 PIT 测试（可选功能，暂未实现）"""
        logger.info("trigger_pitest: 功能暂未实现")
        return {"success": False, "message": "PIT integration not implemented yet"}
