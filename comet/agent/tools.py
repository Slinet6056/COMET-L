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
        self.project_path: Optional[str] = None  # 工作路径（可能是沙箱）
        self.original_project_path: Optional[str] = None  # 原始项目路径（用于创建变异沙箱）
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
                description="生成测试（数量由LLM自主决定）",
                params={
                    "class_name": "类名",
                    "method_name": "方法名"
                },
                when_to_use="已有目标但还没有测试时",
                notes=["生成的测试数量由LLM根据方法复杂度决定"]
            )
        )

        # 注册 refine_tests
        self.register(
            name="refine_tests",
            func=self.refine_tests,
            metadata=ToolMetadata(
                name="refine_tests",
                description="完善现有测试（改进或补充）",
                params={
                    "class_name": "类名",
                    "method_name": "方法名"
                },
                when_to_use="已有测试但效果不佳时（如：变异分数低、有幸存变异体、覆盖率低）",
                notes=["可以改进现有测试或补充新测试，由LLM自主决定策略"]
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

        # 注册 refine_mutants
        self.register(
            name="refine_mutants",
            func=self.refine_mutants,
            metadata=ToolMetadata(
                name="refine_mutants",
                description="基于现有测试生成更有针对性的变异体（分析测试弱点）",
                params={
                    "class_name": "类名",
                    "method_name": "方法名",
                    "num_mutations": "变异体数量（默认5）"
                },
                when_to_use="根据项目复杂度和测试质量自主判断（可选工具）",
                notes=["简单项目高杀死率是正常的，不必强制使用此工具"]
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

        # 获取目标方法的覆盖率
        if target.get("class_name") and target.get("method_name"):
            coverage = self.db.get_method_coverage(target["class_name"], target["method_name"])
            if coverage:
                target["method_coverage"] = coverage.line_coverage_rate
                logger.info(f"目标方法覆盖率: {coverage.line_coverage_rate:.1%}")
            else:
                target["method_coverage"] = 0.0

        # 保存到状态并处理目标切换
        if self.state and target.get("class_name"):
            # 使用 update_target 方法，自动追踪上一个目标
            previous = self.state.update_target(target)

            # 如果目标切换了，将上一个目标的变异体标记为 outdated
            if previous and previous.get("class_name") and previous.get("method_name"):
                old_class = previous["class_name"]
                old_method = previous["method_name"]
                outdated_count = self.db.mark_mutants_outdated(old_class, old_method)
                logger.info(
                    f"目标已切换，将 {old_class}.{old_method} 的 "
                    f"{outdated_count} 个变异体标记为 outdated"
                )

            # 更新当前方法覆盖率
            if "method_coverage" in target:
                self.state.current_method_coverage = target["method_coverage"]

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

        # 如果指定了目标方法，将该方法的旧变异体标记为 outdated
        if method_name:
            outdated_count = self.db.mark_mutants_outdated(class_name, method_name)
            if outdated_count > 0:
                logger.info(f"已将 {outdated_count} 个旧变异体标记为 outdated")

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
    ) -> Dict[str, Any]:
        """生成测试（数量由LLM自主决定）"""
        if not all([self.project_path, self.test_generator, self.java_executor, self.db]):
            logger.error("generate_tests: 缺少必要组件")
            return {"generated": 0}

        from ..utils.project_utils import find_java_file, write_test_file
        from ..utils.code_utils import extract_class_from_file

        # 读取源代码
        file_path = find_java_file(self.project_path, class_name)
        if not file_path:
            logger.error(f"未找到类文件: {class_name}")
            return {"generated": 0}

        class_code = extract_class_from_file(str(file_path))

        # 如果指定了方法名，使用 JavaExecutor 获取准确的方法签名
        method_sig = None
        if method_name:
            methods = self.java_executor.get_public_methods(str(file_path))
            if methods:
                # 从方法列表中找到指定方法
                for m in methods:
                    if m.get('name') == method_name:
                        method_sig = m.get('signature')
                        break
            if not method_sig:
                # Fallback: 如果找不到，使用默认签名
                logger.warning(f"未找到方法 {method_name} 的签名，使用默认值")
                method_sig = f"public void {method_name}()"

        # 获取现有测试（用于参考，避免重复生成）
        existing_tests = self.db.get_tests_by_target_class(class_name)


        # 生成测试
        test_case = self.test_generator.generate_tests_for_method(
            class_name=class_name,
            method_signature=method_sig or f"public void {method_name}()",
            class_code=class_code,
            existing_tests=existing_tests,
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

        # 验证编译和运行
        compile_result = self.java_executor.compile_tests(self.project_path)
        if compile_result.get("success"):
            test_case.compile_success = True
            logger.info(f"测试编译成功: {test_case.class_name}")

            # 运行测试以确保没有运行时错误
            logger.info("运行测试验证...")
            test_result = self.java_executor.run_tests(self.project_path)

            if not test_result.get("success"):
                # 测试运行失败（可能是测试断言错误）
                test_error = test_result.get('output', 'Unknown test failure')
                logger.warning(f"测试运行失败，尝试修复...")
                logger.debug(f"测试失败详情: {test_error[:500]}")

                # 尝试自动修复测试失败
                fixed_test_case = self.test_generator.regenerate_with_feedback(
                    test_case=test_case,
                    compile_error=f"测试运行失败:\n{test_error}",
                    max_retries=2,
                )

                if fixed_test_case:
                    # 重新写入并验证
                    test_file = write_test_file(
                        project_path=self.project_path,
                        package_name=fixed_test_case.package_name,
                        test_code=fixed_test_case.full_code,
                        test_class_name=fixed_test_case.class_name,
                    )

                    compile_result2 = self.java_executor.compile_tests(self.project_path)
                    if compile_result2.get("success"):
                        test_result2 = self.java_executor.run_tests(self.project_path)
                        if test_result2.get("success"):
                            fixed_test_case.compile_success = True
                            fixed_test_case.compile_error = None
                            logger.info(f"修复成功！测试现在可以正常运行")
                            test_case = fixed_test_case
                        else:
                            logger.warning(f"修复后测试仍然失败")
                            fixed_test_case.compile_error = "Test execution failed after fix"
                            test_case = fixed_test_case
                    else:
                        logger.error(f"修复后无法编译")
                        fixed_test_case.compile_error = str(compile_result2.get('error'))
                        test_case = fixed_test_case
                else:
                    logger.error("自动修复失败")
                    test_case.compile_error = "Test execution failed"
            else:
                logger.info("测试运行成功！")
        else:
            compile_error = compile_result.get('error', 'Unknown error')
            logger.warning(f"测试编译失败: {compile_error}")
            test_case.compile_error = str(compile_error)

            # 尝试自动修复编译错误
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

                # 重新编译和运行验证
                compile_result2 = self.java_executor.compile_tests(self.project_path)
                if compile_result2.get("success"):
                    test_result2 = self.java_executor.run_tests(self.project_path)
                    if test_result2.get("success"):
                        fixed_test_case.compile_success = True
                        fixed_test_case.compile_error = None
                        logger.info(f"修复成功！测试现在可以编译和运行")
                        test_case = fixed_test_case
                    else:
                        logger.warning(f"修复后测试运行失败")
                        fixed_test_case.compile_error = "Test execution failed after fix"
                        test_case = fixed_test_case
                else:
                    logger.error(f"修复后仍无法编译: {compile_result2.get('error', 'Unknown error')}")
                    fixed_test_case.compile_error = str(compile_result2.get('error'))
                    test_case = fixed_test_case
            else:
                logger.error("自动修复失败")

        # 保存
        logger.debug(f"准备保存新生成的测试用例: ID={test_case.id}, version={test_case.version}")
        self.db.save_test_case(test_case)

        return {
            "generated": len(test_case.methods),
            "test_id": test_case.id,
            "compile_success": test_case.compile_success,
        }

    def refine_tests(
        self,
        class_name: str,
        method_name: str,
    ) -> Dict[str, Any]:
        """完善现有测试（改进或补充）"""
        if not all([self.project_path, self.test_generator, self.java_executor, self.db]):
            logger.error("refine_tests: 缺少必要组件")
            return {"refined": 0}

        from ..utils.project_utils import find_java_file, write_test_file
        from ..utils.code_utils import extract_class_from_file

        # 获取现有测试
        existing_tests = self.db.get_tests_by_target_class(class_name)
        if not existing_tests:
            logger.warning(f"没有找到 {class_name} 的现有测试，无法完善")
            return {"refined": 0, "message": "No existing tests found"}

        # 选择最新的测试用例
        test_case = existing_tests[0]
        logger.info(f"将完善测试用例: {test_case.class_name} (共 {len(test_case.methods)} 个测试方法)")
        logger.debug(f"选中的测试用例: ID={test_case.id}, version={test_case.version}")

        # 读取被测类代码
        file_path = find_java_file(self.project_path, class_name)
        if not file_path:
            logger.error(f"未找到类文件: {class_name}")
            return {"refined": 0}

        class_code = extract_class_from_file(str(file_path))

        # 获取幸存变异体和覆盖缺口
        all_mutants = self.db.get_all_mutants()
        survived = self.metrics_collector.get_survived_mutants_for_method(
            class_name, method_name, all_mutants
        ) if self.metrics_collector else []

        # 获取覆盖率信息
        current_method_coverage = None
        coverage = self.db.get_method_coverage(class_name, method_name)
        if coverage:
            gaps = {
                "coverage_rate": coverage.line_coverage_rate,
                "total_lines": coverage.total_lines,
                "covered_lines": len(coverage.covered_lines),
                "uncovered_lines": coverage.missed_lines,  # 现在是行号列表
            }
            current_method_coverage = coverage.line_coverage_rate
            logger.info(
                f"覆盖率信息: {class_name}.{method_name} - "
                f"{coverage.line_coverage_rate:.1%} "
                f"({len(coverage.covered_lines)}/{coverage.total_lines} 行)"
            )
            if coverage.missed_lines:
                logger.debug(f"  未覆盖行: {coverage.missed_lines}")
        else:
            gaps = {}
            logger.debug(f"没有找到 {class_name}.{method_name} 的覆盖率信息")

        # 构建评估反馈
        evaluation_feedback = None
        if self.metrics_collector and survived:
            evaluation_feedback = f"当前有 {len(survived)} 个幸存变异体需要击杀"

        # 完善测试
        refined_test_case = self.test_generator.refine_tests(
            test_case=test_case,
            class_code=class_code,
            target_method=method_name,
            survived_mutants=survived,
            coverage_gaps=gaps,
            evaluation_feedback=evaluation_feedback,
        )

        if not refined_test_case:
            logger.warning(f"测试完善失败: {class_name}.{method_name}")
            return {"refined": 0}

        # 写入测试文件
        test_file = write_test_file(
            project_path=self.project_path,
            package_name=refined_test_case.package_name,
            test_code=refined_test_case.full_code,
            test_class_name=refined_test_case.class_name,
        )

        if not test_file:
            logger.error("写入测试文件失败")
            return {"refined": 0}

        # 验证编译和运行
        compile_result = self.java_executor.compile_tests(self.project_path)
        if compile_result.get("success"):
            refined_test_case.compile_success = True
            logger.info(f"完善后的测试编译成功: {refined_test_case.class_name}")

            # 运行测试以确保没有运行时错误
            logger.info("运行测试验证...")
            test_result = self.java_executor.run_tests(self.project_path)

            if not test_result.get("success"):
                # 测试运行失败（可能是测试断言错误）
                test_error = test_result.get('output', 'Unknown test failure')
                logger.warning(f"完善后的测试运行失败，尝试修复...")
                logger.debug(f"测试失败详情: {test_error[:500]}")

                # 尝试自动修复测试失败
                fixed_test_case = self.test_generator.regenerate_with_feedback(
                    test_case=refined_test_case,
                    compile_error=f"测试运行失败:\n{test_error}",
                    max_retries=2,
                )

                if fixed_test_case:
                    # 重新写入并验证
                    test_file = write_test_file(
                        project_path=self.project_path,
                        package_name=fixed_test_case.package_name,
                        test_code=fixed_test_case.full_code,
                        test_class_name=fixed_test_case.class_name,
                    )

                    compile_result2 = self.java_executor.compile_tests(self.project_path)
                    if compile_result2.get("success"):
                        test_result2 = self.java_executor.run_tests(self.project_path)
                        if test_result2.get("success"):
                            fixed_test_case.compile_success = True
                            fixed_test_case.compile_error = None
                            logger.info(f"修复成功！测试现在可以正常运行")
                            refined_test_case = fixed_test_case
                        else:
                            logger.warning(f"修复后测试仍然失败")
                            fixed_test_case.compile_error = "Test execution failed after fix"
                            refined_test_case = fixed_test_case
                    else:
                        logger.error(f"修复后无法编译")
                        fixed_test_case.compile_error = str(compile_result2.get('error'))
                        refined_test_case = fixed_test_case
                else:
                    logger.error("自动修复失败")
                    refined_test_case.compile_error = "Test execution failed"
            else:
                logger.info("完善后的测试运行成功！")
        else:
            compile_error = compile_result.get('error', 'Unknown error')
            logger.warning(f"完善后的测试编译失败: {compile_error}")
            refined_test_case.compile_error = str(compile_error)

            # 尝试自动修复编译错误
            logger.info("尝试自动修复编译错误...")
            fixed_test_case = self.test_generator.regenerate_with_feedback(
                test_case=refined_test_case,
                compile_error=compile_error,
                max_retries=2,
            )

            if fixed_test_case:
                # 重新写入
                test_file = write_test_file(
                    project_path=self.project_path,
                    package_name=fixed_test_case.package_name,
                    test_code=fixed_test_case.full_code,
                    test_class_name=fixed_test_case.class_name,
                )

                # 重新编译和运行
                compile_result2 = self.java_executor.compile_tests(self.project_path)
                if compile_result2.get("success"):
                    test_result2 = self.java_executor.run_tests(self.project_path)
                    if test_result2.get("success"):
                        fixed_test_case.compile_success = True
                        fixed_test_case.compile_error = None
                        logger.info(f"修复成功！测试现在可以编译和运行")
                        refined_test_case = fixed_test_case
                    else:
                        logger.warning(f"修复后测试运行失败")
                        fixed_test_case.compile_error = "Test execution failed after fix"
                        refined_test_case = fixed_test_case
                else:
                    logger.error(f"修复后仍无法编译")
                    fixed_test_case.compile_error = str(compile_result2.get('error'))
                    refined_test_case = fixed_test_case
            else:
                logger.error("自动修复失败")

        # 保存
        logger.debug(f"准备保存完善后的测试用例: ID={refined_test_case.id}, version={refined_test_case.version}")
        self.db.save_test_case(refined_test_case)

        result = {
            "refined": len(refined_test_case.methods),
            "test_id": refined_test_case.id,
            "compile_success": refined_test_case.compile_success,
            "previous_count": len(test_case.methods),
        }

        # 添加当前方法的覆盖率信息
        if current_method_coverage is not None:
            result["method_coverage"] = current_method_coverage
            logger.info(f"当前方法 {method_name} 覆盖率: {current_method_coverage:.1%}")

        return result

    def run_evaluation(self) -> Dict[str, Any]:
        """运行评估并构建击杀矩阵（只评估当前目标方法的变异体）"""
        if not all([self.project_path, self.mutation_evaluator, self.java_executor, self.db]):
            logger.error("run_evaluation: 缺少必要组件")
            return {"evaluated": 0}

        # 获取当前目标方法
        current_target = self.state.current_target if self.state else None

        # 获取变异体：优先获取当前目标方法的变异体
        if current_target and current_target.get("class_name") and current_target.get("method_name"):
            class_name = current_target["class_name"]
            method_name = current_target["method_name"]
            mutants = self.db.get_mutants_by_method(
                class_name=class_name,
                method_name=method_name,
                status="valid"
            )
            logger.info(f"评估目标方法 {class_name}.{method_name} 的变异体")
        else:
            # 如果没有当前目标，评估所有有效变异体
            mutants = self.db.get_valid_mutants()
            logger.info("评估所有有效变异体（未指定目标方法）")

        test_cases = self.db.get_all_tests()

        if not mutants:
            logger.warning("没有变异体需要评估")
            return {"evaluated": 0, "killed": 0, "mutation_score": 0.0}

        if not test_cases:
            logger.warning("没有测试用例")
            return {"evaluated": len(mutants), "killed": 0, "mutation_score": 0.0}

        logger.info(f"开始评估 {len(mutants)} 个变异体 和 {len(test_cases)} 个测试")

        # 构建击杀矩阵（使用工作路径，因为测试文件在工作沙箱中）
        kill_matrix = self.mutation_evaluator.build_kill_matrix(
            mutants=mutants,
            test_cases=test_cases,
            project_path=self.project_path,
        )

        # 保存更新后的变异体状态到数据库
        for mutant in mutants:
            self.db.save_mutant(mutant)
        logger.debug(f"已保存 {len(mutants)} 个变异体的评估状态")

        # 运行覆盖率分析（在工作沙箱上，针对原始代码）
        coverage_data = None
        try:
            logger.info("收集覆盖率数据（针对原始代码）...")
            coverage_result = self.java_executor.run_tests_with_coverage(self.project_path)

            if coverage_result.get("success"):
                coverage_data = coverage_result

                # 解析覆盖率报告
                from pathlib import Path
                from ..executor.coverage_parser import CoverageParser

                parser = CoverageParser()
                jacoco_path = Path(self.project_path) / "target" / "site" / "jacoco" / "jacoco.xml"

                if jacoco_path.exists():
                    logger.info(f"解析 JaCoCo 报告: {jacoco_path}")
                    method_coverages = parser.parse_jacoco_xml_with_lines(str(jacoco_path))

                    # 保存到数据库
                    iteration = self.state.iteration if self.state else 0
                    for cov in method_coverages:
                        self.db.save_method_coverage(cov, iteration)

                    logger.info(f"已保存 {len(method_coverages)} 个方法的覆盖率数据")

                    # 计算聚合覆盖率数据用于 metrics
                    # 注意：详细的行号信息已保存到数据库，这里只传递统计数据给 metrics
                    if method_coverages:
                        total_lines = sum(c.total_lines for c in method_coverages)
                        covered_lines_count = sum(len(c.covered_lines) for c in method_coverages)
                        total_branches = sum(c.total_branches for c in method_coverages)
                        covered_branches = sum(c.covered_branches for c in method_coverages)

                        coverage_data = {
                            "line_coverage": covered_lines_count / total_lines if total_lines > 0 else 0.0,
                            "branch_coverage": covered_branches / total_branches if total_branches > 0 else 0.0,
                            "total_lines": total_lines,
                            "covered_lines": [],  # MetricsCollector 只需要百分比，不需要具体行号
                            "covered_branches": covered_branches,
                            "total_branches": total_branches,
                        }
                else:
                    logger.warning(f"JaCoCo 报告不存在: {jacoco_path}")
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

    def refine_mutants(
        self,
        class_name: str,
        method_name: str,
        num_mutations: int = 5,
    ) -> Dict[str, Any]:
        """
        基于现有测试生成更具针对性的变异体

        Args:
            class_name: 类名
            method_name: 方法名
            num_mutations: 变异体数量

        Returns:
            结果字典
        """
        if not all([self.project_path, self.mutant_generator, self.static_guard, self.db]):
            logger.error("refine_mutants: 缺少必要组件")
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

        # 获取现有变异体
        existing_mutants = self.db.get_mutants_by_method(
            class_name=class_name,
            method_name=method_name,
            status=None  # 获取所有状态的变异体
        )

        # 获取测试用例
        test_cases = self.db.get_tests_by_target_class(class_name)

        if not test_cases:
            logger.warning(f"没有找到 {class_name} 的测试用例，无法完善变异体")
            return {"generated": 0, "message": "No test cases found"}

        # 计算击杀率
        valid_mutants = [m for m in existing_mutants if m.status == 'valid']
        if valid_mutants:
            killed_count = len([m for m in valid_mutants if not m.survived])
            kill_rate = killed_count / len(valid_mutants) if valid_mutants else 0.0
        else:
            kill_rate = 0.0

        logger.info(
            f"开始完善变异体: {class_name}.{method_name}, "
            f"现有 {len(existing_mutants)} 个变异体, 击杀率 {kill_rate:.1%}"
        )

        # 调用变异生成器的 refine_mutants 方法
        mutants = self.mutant_generator.refine_mutants(
            class_name=class_name,
            class_code=class_code,
            existing_mutants=existing_mutants,
            test_cases=test_cases,
            kill_rate=kill_rate,
            target_method=method_name,
            num_mutations=num_mutations,
        )

        if not mutants:
            logger.warning(f"未生成任何完善变异体: {class_name}.{method_name}")
            return {"generated": 0}

        # 静态过滤
        valid_mutants = self.static_guard.filter_mutants(mutants, str(file_path))

        # 保存到数据库
        for mutant in valid_mutants:
            mutant.patch.file_path = str(file_path)
            self.db.save_mutant(mutant)

        logger.info(f"成功完善并保存 {len(valid_mutants)} 个变异体")
        return {
            "generated": len(valid_mutants),
            "mutant_ids": [m.id for m in valid_mutants],
            "kill_rate": kill_rate,
        }

    def trigger_pitest(self, project_path: str) -> Dict[str, Any]:
        """触发 PIT 测试（可选功能，暂未实现）"""
        logger.info("trigger_pitest: 功能暂未实现")
        return {"success": False, "message": "PIT integration not implemented yet"}
