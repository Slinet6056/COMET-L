"""Agent 工具集"""

import logging
import os
from datetime import datetime
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass, field
from pathlib import Path

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
        self.project_path: str = ""  # 工作路径（可能是沙箱）
        self.original_project_path: str = ""  # 原始项目路径（用于创建变异沙箱）
        self.db: Any = None
        self.java_executor: Any = None
        self.mutant_generator: Any = None
        self.test_generator: Any = None
        self.static_guard: Any = None
        self.mutation_evaluator: Any = None
        self.metrics_collector: Any = None
        self.knowledge_base: Any = None
        self.pattern_extractor: Any = None
        self.sandbox_manager: Any = None
        self.state: Any = None

        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """注册默认工具"""
        # 注册 select_target
        self.register(
            name="select_target",
            func=self.select_target,
            metadata=ToolMetadata(
                name="select_target",
                description="选择要处理的类/方法（支持多种选择策略）",
                params={
                    "criteria": "选择策略（可选）：coverage（默认）、killrate、mutations、priority、random"
                },
                when_to_use="当前没有选中目标时，或需要切换目标时",
                notes=[
                    "criteria 参数说明：",
                    "- coverage: 优先选择覆盖率低的方法（适合提升整体覆盖率）",
                    "- killrate: 优先选择杀死率低的方法（适合改进测试质量，当有较多幸存变异体时）",
                    "- mutations: 优先选择变异体少的类（适合为缺少变异体的类生成测试）",
                    "- priority: 综合评分策略（平衡覆盖率和变异体数量）",
                    "- random: 随机选择（用于探索性测试）",
                    "如果不指定 criteria，默认使用 coverage 策略"
                ]
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

    # 辅助方法

    def _rebuild_test_file_from_db(
        self,
        test_case,
        discarded_methods: set,
    ):
        """
        从数据库重建完整的测试文件，确保丢弃的方法被删除，同时保留其他TestCase的方法

        Args:
            test_case: 当前TestCase
            discarded_methods: 需要丢弃的方法名集合
        """
        from ..utils.project_utils import write_test_file
        from ..utils.code_utils import build_test_class

        # 从数据库获取所有针对同一个目标类的TestCase
        all_test_cases = self.db.get_tests_by_target_class(test_case.target_class)

        # 使用字典去重：{method_name: (TestMethod, updated_at)}
        # 保留更新时间最新的方法（同名方法只保留一个）
        unique_methods = {}

        # 先处理当前 test_case（优先使用最新的内存中的版本）
        for method in test_case.methods:
            # 跳过需要丢弃的方法
            if method.method_name in discarded_methods:
                continue

            method_updated = method.updated_at or method.created_at
            unique_methods[method.method_name] = (method, method_updated)

        # 再处理数据库中的其他 TestCase
        for tc in all_test_cases:
            # 跳过当前 test_case（已经处理过了）
            if tc.id == test_case.id:
                continue

            for method in tc.methods:
                # 跳过需要丢弃的方法
                if method.method_name in discarded_methods:
                    continue

                # 检查是否已存在同名方法
                if method.method_name in unique_methods:
                    existing_method, existing_updated = unique_methods[method.method_name]
                    # 比较更新时间，保留更新的
                    method_updated = method.updated_at or method.created_at
                    if method_updated and existing_updated:
                        if method_updated > existing_updated:
                            unique_methods[method.method_name] = (method, method_updated)
                        elif method_updated == existing_updated and method.version > existing_method.version:
                            # 时间相同时比较版本号
                            unique_methods[method.method_name] = (method, method_updated)
                    # 如果没有时间信息，只比较版本号
                    elif method.version > existing_method.version:
                        unique_methods[method.method_name] = (method, method_updated)
                else:
                    method_updated = method.updated_at or method.created_at
                    unique_methods[method.method_name] = (method, method_updated)

        if not unique_methods:
            logger.warning("没有有效的测试方法，跳过写入")
            return

        # 提取去重后的方法列表
        all_valid_methods = [m for m, _ in unique_methods.values()]

        # 构建完整的测试类代码
        method_codes = [m.code for m in all_valid_methods]
        full_code = build_test_class(
            test_class_name=test_case.class_name,
            target_class=test_case.target_class,
            package_name=test_case.package_name,
            imports=test_case.imports,
            test_methods=method_codes,
        )

        # 写入文件（不使用merge，因为我们已经有了完整的代码）
        write_test_file(
            project_path=self.project_path,
            package_name=test_case.package_name,
            test_code=full_code,
            test_class_name=test_case.class_name,
            merge=False,
        )

        logger.info(f"从数据库重建测试文件: 总共 {len(all_valid_methods)} 个方法（去重后）")

    def _restore_test_file_from_db(self, original_test_case):
        """
        从数据库恢复测试文件（用于验证失败后回滚）

        Args:
            original_test_case: 原始的 TestCase（数据库中的版本）
        """
        from ..utils.project_utils import write_test_file
        from ..utils.code_utils import build_test_class

        # 从数据库获取所有针对同一个目标类的TestCase
        all_test_cases = self.db.get_tests_by_target_class(original_test_case.target_class)

        # 使用字典去重：{method_name: (TestMethod, updated_at)}
        # 保留更新时间最新的方法（同名方法只保留一个）
        unique_methods = {}

        for tc in all_test_cases:
            for method in tc.methods:
                # 检查是否已存在同名方法
                if method.method_name in unique_methods:
                    existing_method, existing_updated = unique_methods[method.method_name]
                    # 比较更新时间，保留更新的
                    method_updated = method.updated_at or method.created_at
                    if method_updated and existing_updated and method_updated > existing_updated:
                        unique_methods[method.method_name] = (method, method_updated)
                    # 如果时间相同，比较版本号
                    elif method.version > existing_method.version:
                        unique_methods[method.method_name] = (method, method_updated)
                else:
                    method_updated = method.updated_at or method.created_at
                    unique_methods[method.method_name] = (method, method_updated)

        if not unique_methods:
            logger.warning("数据库中没有有效的测试方法，无法恢复")
            return

        # 提取去重后的方法列表
        all_methods = [m for m, _ in unique_methods.values()]

        # 构建完整的测试类代码
        method_codes = [m.code for m in all_methods]
        full_code = build_test_class(
            test_class_name=original_test_case.class_name,
            target_class=original_test_case.target_class,
            package_name=original_test_case.package_name,
            imports=original_test_case.imports,
            test_methods=method_codes,
        )

        # 写入文件
        write_test_file(
            project_path=self.project_path,
            package_name=original_test_case.package_name,
            test_code=full_code,
            test_class_name=original_test_case.class_name,
            merge=False,
        )

        logger.info(f"已从数据库恢复测试文件: {original_test_case.class_name} (共 {len(all_methods)} 个方法，去重后)")

    def _get_test_file_path(self, test_case) -> str:
        """
        获取测试文件的完整路径

        Args:
            test_case: TestCase 对象

        Returns:
            测试文件的完整路径
        """
        if test_case.package_name:
            package_path = test_case.package_name.replace(".", os.sep)
            return os.path.join(
                self.project_path,
                "src", "test", "java",
                package_path,
                f"{test_case.class_name}.java"
            )
        else:
            return os.path.join(
                self.project_path,
                "src", "test", "java",
                f"{test_case.class_name}.java"
            )

    def _read_actual_test_file(self, test_case) -> Optional[str]:
        """
        读取磁盘上实际的测试文件内容

        在使用 merge 模式写入测试时，磁盘上的文件可能包含合并后的所有测试方法，
        而 test_case.full_code 只包含最新生成的测试方法。
        修复编译错误时需要使用实际的文件内容，否则 LLM 无法定位到错误的代码行。

        Args:
            test_case: TestCase 对象

        Returns:
            测试文件内容，如果文件不存在则返回 None
        """
        test_file_path = self._get_test_file_path(test_case)

        if os.path.exists(test_file_path):
            try:
                with open(test_file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception as e:
                logger.warning(f"读取测试文件失败: {test_file_path}, 错误: {e}")
                return None
        else:
            logger.debug(f"测试文件不存在: {test_file_path}")
            return None

    def _sync_fixed_code_to_test_case(self, original_test_case, fixed_full_code: str):
        """
        将修复后的完整代码同步到 TestCase

        当使用实际文件内容（可能包含多个 TestCase 的方法）进行修复时，
        需要从修复后的代码中提取属于当前 TestCase 的方法，
        以保持 test_case.methods 和 test_case.full_code 的一致性。

        Args:
            original_test_case: 原始的 TestCase 对象
            fixed_full_code: 修复后的完整测试文件代码

        Returns:
            更新后的 TestCase 对象
        """
        from ..utils.project_utils import _extract_test_methods
        from ..utils.code_utils import build_test_class
        import re

        def _extract_methods_for_class(code: str, class_name: str) -> dict:
            """
            仅提取指定测试类内的测试方法，避免不同类的同名方法互相覆盖
            """
            pattern = re.compile(rf"\bclass\s+{re.escape(class_name)}\b")
            lines = code.splitlines()

            class_start = None
            brace_count = 0
            class_end = None

            for idx, line in enumerate(lines):
                if class_start is None and pattern.search(line):
                    class_start = idx
                    brace_count = line.count("{") - line.count("}")
                    # 继续查找对应的闭合大括号
                    continue

                if class_start is not None:
                    brace_count += line.count("{") - line.count("}")
                    if brace_count <= 0:
                        class_end = idx
                        break

            if class_start is None:
                logger.warning(f"未在修复后的代码中找到测试类: {class_name}")
                return {}

            if class_end is None:
                class_end = len(lines) - 1

            class_code = "\n".join(lines[class_start : class_end + 1])
            return _extract_test_methods(class_code)

        # 从修复后的代码中提取所有测试方法
        fixed_methods_dict = _extract_methods_for_class(
            fixed_full_code, original_test_case.class_name
        )

        if not fixed_methods_dict:
            raise ValueError(
                f"修复后的代码中未找到类 {original_test_case.class_name} 的测试方法"
            )

        # 获取当前 TestCase 中的方法名列表
        original_method_names = {m.method_name for m in original_test_case.methods}

        # 更新 original_test_case 中对应方法的代码
        updated_methods = []
        missing_methods = []
        for method in original_test_case.methods:
            if method.method_name in fixed_methods_dict:
                # 使用修复后的代码更新方法
                method.code = fixed_methods_dict[method.method_name]
                logger.debug(f"更新测试方法代码: {method.method_name}")
                updated_methods.append(method)
            else:
                missing_methods.append(method.method_name)

        if missing_methods:
            missing_list = ", ".join(missing_methods)
            logger.warning(
                f"修复后的测试文件缺少以下方法，无法同步到 TestCase: {missing_list}"
            )
            raise ValueError(
                f"修复后的测试文件缺少以下方法: {missing_list}"
            )

        original_test_case.methods = updated_methods

        # 重新构建 full_code（只包含当前 TestCase 的方法）
        method_codes = [m.code for m in updated_methods]
        original_test_case.full_code = build_test_class(
            test_class_name=original_test_case.class_name,
            target_class=original_test_case.target_class,
            package_name=original_test_case.package_name,
            imports=original_test_case.imports,
            test_methods=method_codes,
        )

        logger.debug(f"同步修复后的代码到 TestCase: {len(updated_methods)} 个方法")
        return original_test_case

    def _delete_test_file(self, test_case):
        """
        删除磁盘上的测试文件

        Args:
            test_case: TestCase 对象
        """
        test_file_path = self._get_test_file_path(test_case)

        if os.path.exists(test_file_path):
            try:
                os.remove(test_file_path)
                logger.info(f"已删除测试文件: {test_file_path}")
            except OSError as e:
                logger.warning(f"删除测试文件失败: {test_file_path}, 错误: {e}")
        else:
            logger.debug(f"测试文件不存在，无需删除: {test_file_path}")

    def _verify_and_fix_tests(
        self,
        test_case,
        class_code: str,
        max_compile_retries: int = 3,
        max_test_retries: int = 3,
    ):
        """
        验证并修复测试方法

        流程：
        1. 编译测试，如果失败最多重试max_compile_retries次
        2. 如果编译通过，运行测试
        3. 如果有失败的测试方法，单独重新生成这些方法（最多max_test_retries次）
        4. 如果方法重新生成失败，从测试用例中移除

        Args:
            test_case: 测试用例
            class_code: 被测类代码
            max_compile_retries: 编译失败时的最大重试次数
            max_test_retries: 测试失败时的最大重试次数

        Returns:
            修复后的测试用例
        """
        from ..executor.surefire_parser import SurefireParser
        from ..utils.project_utils import write_test_file
        from ..utils.code_utils import build_test_class

        surefire_parser = SurefireParser()
        reports_dir = os.path.join(self.project_path, "target", "surefire-reports")

        # ===== 步骤1: 编译测试，最多重试max_compile_retries次 =====
        compile_retry_count = 0
        while compile_retry_count < max_compile_retries:
            logger.debug(f"编译测试（第 {compile_retry_count + 1}/{max_compile_retries} 次尝试）...")
            compile_result = self.java_executor.compile_tests(self.project_path)

            if compile_result.get("success"):
                logger.info(f"✓ 测试编译成功: {test_case.class_name}")
                test_case.compile_success = True
                break

            # 编译失败
            compile_error = compile_result.get('error', 'Unknown error')
            compile_retry_count += 1

            if compile_retry_count >= max_compile_retries:
                logger.error(f"✗ 编译失败且已达到最大重试次数（{max_compile_retries}次）")
                test_case.compile_success = False
                test_case.compile_error = f"编译失败（已重试{max_compile_retries}次）: {compile_error}"
                return test_case

            # 尝试修复编译错误
            logger.warning(f"编译失败（第 {compile_retry_count} 次），尝试修复...")
            logger.debug(f"编译错误: {compile_error[:500]}")  # 只记录前500字符

            # 读取 sandbox 中实际的测试文件内容（可能包含合并的其他测试方法）
            # 这是修复编译错误的关键：需要用实际的文件内容，而不是 test_case.full_code
            actual_test_code = self._read_actual_test_file(test_case)
            use_actual_file = actual_test_code and actual_test_code != test_case.full_code

            if use_actual_file:
                # 此处分支确保 actual_test_code 不为 None
                assert actual_test_code is not None
                logger.debug(f"检测到实际测试文件与 test_case.full_code 不一致，使用实际文件内容进行修复")
                logger.debug(f"实际文件行数: {len(actual_test_code.splitlines())}, full_code 行数: {len(test_case.full_code.splitlines()) if test_case.full_code else 0}")
                # 创建一个临时的 TestCase 副本用于修复，避免污染原始对象
                from copy import deepcopy
                temp_test_case = deepcopy(test_case)
                temp_test_case.full_code = actual_test_code
                fix_target = temp_test_case
            else:
                fix_target = test_case

            fixed_test_case = self.test_generator.regenerate_with_feedback(
                test_case=fix_target,
                compile_error=compile_error,
                class_code=class_code,
                max_retries=1,  # 每次只重新生成一次
            )

            if not fixed_test_case:
                logger.error("LLM 未能生成修复后的测试代码")
                test_case.compile_success = False
                test_case.compile_error = f"编译失败（已重试{compile_retry_count}次）且无法修复"
                return test_case

            # 如果使用了临时副本（包含完整的合并后测试文件），需要特殊处理
            if use_actual_file:
                # 1. 先写入完整的修复后代码（不使用 merge，因为 LLM 修复的是完整文件）
                #    这确保所有方法（包括其他 TestCase 的）都被正确修复
                test_file = write_test_file(
                    project_path=self.project_path,
                    package_name=fixed_test_case.package_name,
                    test_code=fixed_test_case.full_code,
                    test_class_name=fixed_test_case.class_name,
                    merge=False,  # 不使用 merge，直接覆盖
                )

                # 2. 然后从修复后的代码中提取当前 TestCase 的方法，同步回 test_case
                #    这确保数据库中的 test_case.methods 和 test_case.full_code 保持一致
                try:
                    fixed_test_case = self._sync_fixed_code_to_test_case(
                        original_test_case=test_case,
                        fixed_full_code=fixed_test_case.full_code,
                    )
                except ValueError as sync_error:
                    logger.error(f"同步修复后的测试方法失败: {sync_error}")
                    test_case.compile_success = False
                    test_case.compile_error = str(sync_error)
                    return test_case
            else:
                # 普通情况：写入修复后的测试文件（使用合并模式，保留其他测试方法）
                test_file = write_test_file(
                    project_path=self.project_path,
                    package_name=fixed_test_case.package_name,
                    test_code=fixed_test_case.full_code,
                    test_class_name=fixed_test_case.class_name,
                    merge=True,
                )

            if not test_file:
                logger.error("写入修复后的测试文件失败")
                test_case.compile_success = False
                test_case.compile_error = "写入测试文件失败"
                return test_case

            test_case = fixed_test_case

        # ===== 步骤2: 运行测试 =====
        logger.info("运行测试验证...")
        test_result = self.java_executor.run_tests(self.project_path)

        # 如果所有测试通过，直接返回
        if test_result.get("success"):
            logger.info("✓ 所有测试方法都通过了！")
            test_case.compile_error = None
            return test_case

        # ===== 步骤3: 处理测试失败 =====
        # 检查是否超时（匹配 "Timeout after X seconds" 格式）
        if test_result.get("error", "").startswith("Timeout"):
            logger.error("测试运行超时，开始逐个测试方法以识别超时方法...")

            # 逐个运行测试方法来识别超时的方法
            timeout_methods = self._identify_timeout_methods(test_case)

            if timeout_methods:
                logger.warning(f"识别到 {len(timeout_methods)} 个超时或失败的方法，将移除它们")
                # 保留没有超时的方法
                valid_methods = [m for m in test_case.methods if m.method_name not in timeout_methods]

                if not valid_methods:
                    logger.error("所有测试方法都超时或失败")
                    test_case.compile_success = False
                    timeout_value = self.java_executor.test_timeout if self.java_executor else 30
                    test_case.compile_error = f"所有测试方法都超时或失败（>{timeout_value}秒）"
                    test_case.methods = []
                    return test_case

                # 更新测试用例，只保留有效的方法
                test_case.methods = valid_methods
                method_codes = [m.code for m in valid_methods]
                test_case.full_code = build_test_class(
                    test_class_name=test_case.class_name,
                    target_class=test_case.target_class,
                    package_name=test_case.package_name,
                    imports=test_case.imports,
                    test_methods=method_codes,
                )

                # 写入更新后的测试文件（从数据库重建完整测试类，确保失败方法被删除）
                self._rebuild_test_file_from_db(
                    test_case=test_case,
                    discarded_methods=timeout_methods,
                )

                logger.info(f"保留了 {len(valid_methods)} 个有效的测试方法，开始验证...")

                # 重新编译和测试，确保剩余方法一起工作正常
                compile_result = self.java_executor.compile_tests(self.project_path)
                if not compile_result.get("success"):
                    logger.error("过滤后的测试用例编译失败")
                    test_case.compile_success = False
                    test_case.compile_error = "Filtered test case compilation failed"
                    test_case.methods = []
                    return test_case

                test_result = self.java_executor.run_tests(self.project_path)
                if test_result.get("success"):
                    logger.info(f"✓ 过滤后的测试用例验证成功，保留 {len(valid_methods)} 个方法")
                    test_case.compile_success = True
                    test_case.compile_error = None
                    return test_case
                else:
                    logger.error("过滤后的测试用例运行失败，可能存在测试间依赖")
                    test_case.compile_success = False
                    test_case.compile_error = "Filtered test case execution failed"
                    test_case.methods = []
                    return test_case
            else:
                logger.error("无法识别超时方法")
                test_case.compile_success = False
                timeout_value = self.java_executor.test_timeout if self.java_executor else 30
                test_case.compile_error = f"测试运行超时但无法识别具体方法（>{timeout_value}秒）"
                test_case.methods = []
                return test_case

        if test_result.get("success"):
            logger.info("所有测试方法都通过了！")
            test_case.compile_error = None
            return test_case

        # 3. 解析 Surefire 报告，识别失败的方法
        logger.warning("部分测试方法失败，开始精确识别...")
        suite_results = surefire_parser.parse_surefire_reports(reports_dir)

        if not suite_results:
            logger.error("无法解析 Surefire 报告")
            test_case.compile_success = False
            test_case.compile_error = "测试运行失败且无法解析报告"
            return test_case

        # 收集失败的方法
        failed_methods = {}  # {method_name: error_message}
        passed_methods = set()

        for suite in suite_results:
            for test in suite.test_cases:
                if test.passed:
                    passed_methods.add(test.method_name)
                else:
                    error_msg = test.error_message or test.failure_message or "Unknown error"
                    failed_methods[test.method_name] = error_msg

        logger.info(f"测试结果: {len(passed_methods)} 个通过, {len(failed_methods)} 个失败")

        if failed_methods:
            for method_name, error in failed_methods.items():
                logger.info(f"  失败: {method_name}")
                logger.debug(f"    错误: {error[:200]}")

        # 4. 尝试修复失败的方法
        fixed_methods = {}  # {method_name: fixed_code}
        discarded_methods = set()

        for method_name, error_message in failed_methods.items():
            # 对于超时错误，直接丢弃，不尝试修复
            if "timeout" in error_message.lower() or "timed out" in error_message.lower():
                logger.warning(f"方法 {method_name} 超时，直接丢弃")
                discarded_methods.add(method_name)
                continue

            logger.info(f"开始修复方法: {method_name}")

            # 从 TestCase.methods 中查找方法代码
            method_code = None
            for method in test_case.methods:
                if method.method_name == method_name:
                    method_code = method.code
                    break

            if not method_code:
                logger.warning(f"无法找到方法代码: {method_name}，将丢弃该方法")
                discarded_methods.add(method_name)
                continue

            # 尝试修复
            fixed_code = self.test_generator.fix_single_method(
                method_name=method_name,
                method_code=method_code,
                class_code=class_code,
                error_message=error_message,
                max_retries=max_test_retries,
            )

            if fixed_code:
                # 验证修复后的方法
                # 临时构建只包含这个方法的测试类
                temp_methods = [fixed_code]
                temp_full_code = build_test_class(
                    test_class_name=test_case.class_name,
                    target_class=test_case.target_class,
                    package_name=test_case.package_name,
                    imports=test_case.imports,
                    test_methods=temp_methods,
                )

                # 写入并测试（不合并，确保只测试当前修复的方法）
                write_test_file(
                    project_path=self.project_path,
                    package_name=test_case.package_name,
                    test_code=temp_full_code,
                    test_class_name=test_case.class_name,
                    merge=False,
                )

                compile_res = self.java_executor.compile_tests(self.project_path)
                if compile_res.get("success"):
                    test_res = self.java_executor.run_tests(self.project_path)
                    if test_res.get("success"):
                        logger.info(f"✓ 方法 {method_name} 修复成功")
                        fixed_methods[method_name] = fixed_code
                    else:
                        logger.warning(f"✗ 方法 {method_name} 修复后仍然失败，将丢弃")
                        discarded_methods.add(method_name)
                else:
                    logger.warning(f"✗ 方法 {method_name} 修复后无法编译，将丢弃")
                    discarded_methods.add(method_name)
            else:
                logger.warning(f"✗ 无法修复方法 {method_name}，将丢弃")
                discarded_methods.add(method_name)

        # 5. 重建测试用例（保留通过的方法 + 修复成功的方法）
        final_methods = []

        for method in test_case.methods:
            method_name = method.method_name

            if method_name in passed_methods:
                # 保留原来通过的方法
                final_methods.append(method)
                logger.debug(f"保留通过的方法: {method_name}")
            elif method_name in fixed_methods:
                # 使用修复后的代码
                method.code = fixed_methods[method_name]
                final_methods.append(method)
                logger.debug(f"使用修复后的方法: {method_name}")
            elif method_name in discarded_methods:
                logger.warning(f"丢弃失败的方法: {method_name}")
            else:
                # 不在失败列表中，可能是新方法，保留
                final_methods.append(method)

        if not final_methods:
            logger.error("所有测试方法都失败了，无有效测试")
            test_case.compile_success = False
            test_case.compile_error = "所有测试方法都失败"
            return test_case

        # 更新测试用例
        test_case.methods = final_methods
        method_codes = [m.code for m in final_methods]
        test_case.full_code = build_test_class(
            test_class_name=test_case.class_name,
            target_class=test_case.target_class,
            package_name=test_case.package_name,
            imports=test_case.imports,
            test_methods=method_codes,
        )

        # 最后写入并验证（从数据库重建完整测试类，确保丢弃的方法被删除）
        self._rebuild_test_file_from_db(
            test_case=test_case,
            discarded_methods=discarded_methods,
        )

        final_compile = self.java_executor.compile_tests(self.project_path)
        if final_compile.get("success"):
            final_test = self.java_executor.run_tests(self.project_path)
            if final_test.get("success"):
                logger.info(f"✓ 最终测试验证成功！保留 {len(final_methods)} 个方法")
                test_case.compile_success = True
                test_case.compile_error = None
            else:
                logger.warning("最终测试运行失败（但这不应该发生）")
                test_case.compile_success = False
                test_case.compile_error = "Final test run failed"
        else:
            logger.error("最终编译失败（但这不应该发生）")
            test_case.compile_success = False
            test_case.compile_error = "Final compilation failed"

        logger.info(f"测试验证完成: 丢弃了 {len(discarded_methods)} 个方法, 保留了 {len(final_methods)} 个方法")

        return test_case

    def _identify_timeout_methods(self, test_case) -> set:
        """
        通过逐个运行测试方法来识别导致超时的方法

        Args:
            test_case: TestCase 对象

        Returns:
            导致超时的方法名集合
        """
        from comet.utils import write_test_file, build_test_class

        timeout_methods = set()

        # 构建完整的测试类名（包含包名）
        if test_case.package_name:
            full_class_name = f"{test_case.package_name}.{test_case.class_name}"
        else:
            full_class_name = test_case.class_name

        logger.info(f"开始逐个测试 {len(test_case.methods)} 个方法以识别超时方法...")

        for method in test_case.methods:
            method_name = method.method_name
            logger.debug(f"测试方法: {method_name}")

            # 构建只包含这个方法的测试类
            temp_methods = [method.code]
            temp_full_code = build_test_class(
                test_class_name=test_case.class_name,
                target_class=test_case.target_class,
                package_name=test_case.package_name,
                imports=test_case.imports,
                test_methods=temp_methods,
            )

            # 写入测试文件（不合并，确保只测试当前方法）
            write_test_file(
                project_path=self.project_path,
                package_name=test_case.package_name,
                test_code=temp_full_code,
                test_class_name=test_case.class_name,
                merge=False,
            )

            # 编译测试
            compile_result = self.java_executor.compile_tests(self.project_path)
            if not compile_result.get("success"):
                logger.warning(f"方法 {method_name} 编译失败，标记为有问题")
                timeout_methods.add(method_name)
                continue

            # 运行单个测试方法
            test_result = self.java_executor.run_single_test_method(
                self.project_path,
                full_class_name,
                method_name
            )

            # 检查是否超时（匹配 "Timeout after X seconds" 格式）
            if test_result.get("error", "").startswith("Timeout"):
                logger.warning(f"✗ 方法 {method_name} 超时")
                timeout_methods.add(method_name)
            elif not test_result.get("success"):
                logger.warning(f"✗ 方法 {method_name} 运行失败")
                timeout_methods.add(method_name)
            else:
                logger.debug(f"✓ 方法 {method_name} 正常")

        logger.info(f"识别完成: {len(timeout_methods)} 个方法超时或失败")
        return timeout_methods

    # 工具实现

    def select_target(self, criteria: str = "coverage") -> Dict[str, Any]:
        """选择目标类和方法（跳过黑名单中的目标）"""
        if not self.project_path or not self.java_executor or not self.db:
            logger.error("select_target: 缺少必要组件")
            return {"class_name": None, "method_name": None}

        from .target_selector import TargetSelector
        selector = TargetSelector(self.project_path, self.java_executor, self.db)

        # 获取黑名单
        blacklist = set()
        if self.state and self.state.failed_targets:
            blacklist = {ft.get("target") for ft in self.state.failed_targets if ft.get("target")}
            logger.debug(f"黑名单中有 {len(blacklist)} 个失败的目标")

        target = selector.select(criteria, blacklist=blacklist)

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

        # 使用新的验证和修复流程（编译失败最多重试3次）
        logger.info("开始验证和修复测试方法...")
        test_case = self._verify_and_fix_tests(
            test_case=test_case,
            class_code=class_code,
            max_compile_retries=3,
            max_test_retries=3,
        )

        # 静态校验：检查测试方法代码中是否包含明显的错误模式
        if test_case.compile_success:
            from ..utils.code_utils import validate_test_methods, build_test_class
            invalid_methods = validate_test_methods(test_case.methods, class_code)
            if invalid_methods:
                logger.warning(f"静态校验发现 {len(invalid_methods)} 个包含潜在错误的测试方法，将移除")
                test_case.methods = [m for m in test_case.methods if m.method_name not in invalid_methods]

                if not test_case.methods:
                    logger.error(f"静态校验后所有测试方法都被移除: {class_name}.{method_name}")
                    test_case.compile_success = False
                    test_case.compile_error = "All test methods removed by static validation"
                else:
                    # 重新构建测试代码
                    method_codes = [m.code for m in test_case.methods]
                    test_case.full_code = build_test_class(
                        test_class_name=test_case.class_name,
                        target_class=test_case.target_class,
                        package_name=test_case.package_name,
                        imports=test_case.imports,
                        test_methods=method_codes,
                    )
                    # 重新写入测试文件
                    write_test_file(
                        project_path=self.project_path,
                        package_name=test_case.package_name,
                        test_code=test_case.full_code,
                        test_class_name=test_case.class_name,
                    )
                    logger.info(f"静态校验后保留 {len(test_case.methods)} 个有效测试方法")

        # 只有编译成功才保存
        if not test_case.compile_success:
            logger.error(f"✗ 测试用例编译失败（已重试3次），不保存到数据库")
            logger.error(f"编译错误: {test_case.compile_error}")

            # 如果已有其他测试用例，从数据库恢复测试文件
            if existing_tests:
                logger.info("从数据库恢复原有测试文件...")
                self._restore_test_file_from_db(existing_tests[0])

            # 将这个目标添加到失败黑名单
            if self.state:
                target_key = f"{class_name}.{method_name}" if method_name else class_name
                if not any(ft.get("target") == target_key for ft in self.state.failed_targets):
                    self.state.failed_targets.append({
                        "target": target_key,
                        "class_name": class_name,
                        "method_name": method_name,
                        "reason": "测试生成后编译失败（已重试3次）",
                        "error": test_case.compile_error[:500] if test_case.compile_error else "Unknown",
                        "timestamp": datetime.now().isoformat()
                    })
                    logger.warning(f"已将 {target_key} 添加到失败黑名单")
                    # 如果当前目标是被加入黑名单的目标，清除当前目标选中
                    if self.state.current_target:
                        current_class = self.state.current_target.get("class_name")
                        current_method = self.state.current_target.get("method_name", "")
                        current_target_key = f"{current_class}.{current_method}" if current_method and current_class else (current_class if current_class else None)
                        if current_target_key == target_key:
                            logger.info(f"当前目标 {target_key} 已被加入黑名单，清除目标选中")
                            self.state.update_target(None)

            return {
                "generated": 0,
                "test_id": test_case.id,
                "compile_success": False,
                "error": test_case.compile_error,
                "message": "测试编译失败（已重试3次），已加入黑名单"
            }

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

        # 获取现有测试（按方法查询，确保针对的是当前目标方法）
        existing_tests = self.db.get_tests_by_target_method(class_name, method_name)
        if not existing_tests:
            logger.warning(f"没有找到 {class_name}.{method_name} 的现有测试，无法完善")
            logger.info(f"提示：应该先使用 generate_tests 为 {class_name}.{method_name} 生成测试")
            return {"refined": 0, "message": f"No existing tests found for {class_name}.{method_name}"}

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

        # 使用新的验证和修复流程（编译失败最多重试3次）
        logger.info("开始验证和修复完善后的测试方法...")
        refined_test_case = self._verify_and_fix_tests(
            test_case=refined_test_case,
            class_code=class_code,
            max_compile_retries=3,
            max_test_retries=3,
        )

        # 静态校验：检查测试方法代码中是否包含明显的错误模式
        if refined_test_case.compile_success:
            from ..utils.code_utils import validate_test_methods, build_test_class
            invalid_methods = validate_test_methods(refined_test_case.methods, class_code)
            if invalid_methods:
                logger.warning(f"静态校验发现 {len(invalid_methods)} 个包含潜在错误的测试方法，将移除")
                refined_test_case.methods = [m for m in refined_test_case.methods if m.method_name not in invalid_methods]

                if not refined_test_case.methods:
                    logger.error(f"静态校验后所有测试方法都被移除: {class_name}.{method_name}")
                    refined_test_case.compile_success = False
                    refined_test_case.compile_error = "All test methods removed by static validation"
                else:
                    # 重新构建测试代码
                    method_codes = [m.code for m in refined_test_case.methods]
                    refined_test_case.full_code = build_test_class(
                        test_class_name=refined_test_case.class_name,
                        target_class=refined_test_case.target_class,
                        package_name=refined_test_case.package_name,
                        imports=refined_test_case.imports,
                        test_methods=method_codes,
                    )
                    # 重新写入测试文件
                    write_test_file(
                        project_path=self.project_path,
                        package_name=refined_test_case.package_name,
                        test_code=refined_test_case.full_code,
                        test_class_name=refined_test_case.class_name,
                    )
                    logger.info(f"静态校验后保留 {len(refined_test_case.methods)} 个有效测试方法")

        # 只有编译成功才保存
        if not refined_test_case.compile_success:
            logger.error(f"✗ 测试用例编译失败（已重试3次），不保存到数据库")
            logger.error(f"编译错误: {refined_test_case.compile_error}")

            # 恢复原始测试文件（从数据库中的原始数据）
            logger.info("从数据库恢复原始测试文件...")
            self._restore_test_file_from_db(test_case)

            # 将这个目标添加到失败黑名单
            if self.state:
                target_key = f"{class_name}.{method_name}" if method_name else class_name
                if not any(ft.get("target") == target_key for ft in self.state.failed_targets):
                    self.state.failed_targets.append({
                        "target": target_key,
                        "class_name": class_name,
                        "method_name": method_name,
                        "reason": "测试完善后编译失败（已重试3次）",
                        "error": refined_test_case.compile_error[:500] if refined_test_case.compile_error else "Unknown",
                        "timestamp": datetime.now().isoformat()
                    })
                    logger.warning(f"已将 {target_key} 添加到失败黑名单")
                    # 如果当前目标是被加入黑名单的目标，清除当前目标选中
                    if self.state.current_target:
                        current_class = self.state.current_target.get("class_name")
                        current_method = self.state.current_target.get("method_name", "")
                        current_target_key = f"{current_class}.{current_method}" if current_method and current_class else (current_class if current_class else None)
                        if current_target_key == target_key:
                            logger.info(f"当前目标 {target_key} 已被加入黑名单，清除目标选中")
                            self.state.update_target(None)

            return {
                "refined": 0,
                "test_id": refined_test_case.id,
                "compile_success": False,
                "error": refined_test_case.compile_error,
                "message": "测试编译失败（已重试3次），已加入黑名单"
            }

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

        # ===== 步骤1: 预验证测试用例 =====
        logger.info("步骤1: 预验证测试用例...")

        # 首先检查编译是否成功
        logger.debug("检查测试编译...")
        compile_result = self.java_executor.compile_tests(self.project_path)
        if not compile_result.get("success"):
            compile_error = compile_result.get('error', 'Unknown error')
            logger.error(f"✗ 测试编译失败，无法进行评估")
            logger.error(f"编译错误: {compile_error[:500]}")

            # 删除整个类的所有测试用例
            if current_target and current_target.get("class_name"):
                target_class = current_target["class_name"]
                logger.warning(f"删除 {target_class} 类的所有测试用例...")

                # 获取该类的所有测试用例
                class_tests = self.db.get_tests_by_target_class(target_class)
                deleted_count = 0
                for test_case in class_tests:
                    # 同时删除磁盘上的测试文件
                    self._delete_test_file(test_case)
                    self.db.delete_test_case(test_case.id)
                    deleted_count += 1

                logger.warning(f"已删除 {deleted_count} 个测试用例")

                # 将目标添加到黑名单
                if self.state:
                    method_name = current_target.get("method_name", "")
                    target_key = f"{target_class}.{method_name}" if method_name else target_class
                    if not any(ft.get("target") == target_key for ft in self.state.failed_targets):
                        self.state.failed_targets.append({
                            "target": target_key,
                            "class_name": target_class,
                            "method_name": method_name,
                            "reason": "评估时编译失败，已删除所有测试",
                            "error": compile_error[:500],
                            "timestamp": datetime.now().isoformat()
                        })
                        logger.warning(f"已将 {target_key} 添加到失败黑名单")
                        # 如果当前目标是被加入黑名单的目标，清除当前目标选中
                        if self.state.current_target:
                            current_class = self.state.current_target.get("class_name")
                            current_method = self.state.current_target.get("method_name", "")
                            current_target_key = f"{current_class}.{current_method}" if current_method and current_class else (current_class if current_class else None)
                            if current_target_key == target_key:
                                logger.info(f"当前目标 {target_key} 已被加入黑名单，清除目标选中")
                                self.state.update_target(None)

            return {"evaluated": len(mutants), "killed": 0, "mutation_score": 0.0, "error": "Compilation failed, tests deleted"}

        test_result = self.java_executor.run_tests(self.project_path)

        # 检查是否超时或失败（匹配 "Timeout after X seconds" 格式）
        if test_result.get("error", "").startswith("Timeout"):
            logger.error("测试运行超时，开始逐个测试方法以识别超时方法...")

            # 超时时不应该解析 Surefire 报告，因为测试没有完成，报告可能不存在或不完整
            # 应该逐个运行测试方法来识别哪些方法导致超时
            all_timeout_methods = set()

            for test_case in list(test_cases):
                # 逐个测试这个测试用例中的方法
                timeout_methods = self._identify_timeout_methods(test_case)

                if timeout_methods:
                    logger.warning(f"测试用例 {test_case.class_name} 中有 {len(timeout_methods)} 个超时方法")

                    # 构建完整方法名并添加到全局集合
                    for method_name in timeout_methods:
                        if test_case.package_name:
                            full_name = f"{test_case.package_name}.{test_case.class_name}.{method_name}"
                        else:
                            full_name = f"{test_case.class_name}.{method_name}"
                        all_timeout_methods.add(full_name)

                    # 从测试用例中移除超时的方法
                    methods_to_remove = [m for m in test_case.methods if m.method_name in timeout_methods]
                    for method in methods_to_remove:
                        test_case.methods.remove(method)
                        logger.warning(f"删除超时的测试方法: {test_case.class_name}.{method.method_name}")

                    # 如果测试用例没有方法了，从列表中移除
                    if not test_case.methods:
                        logger.warning(f"测试用例 {test_case.class_name} 的所有方法都超时，删除整个测试用例")
                        test_cases.remove(test_case)
                        self._delete_test_file(test_case)
                        self.db.delete_test_case(test_case.id)
                    else:
                        # 更新测试用例的代码
                        from comet.utils import build_test_class, write_test_file
                        method_codes = [m.code for m in test_case.methods]
                        test_case.full_code = build_test_class(
                            test_class_name=test_case.class_name,
                            target_class=test_case.target_class,
                            package_name=test_case.package_name,
                            imports=test_case.imports,
                            test_methods=method_codes,
                        )
                        # 写入更新后的测试文件（不合并，完全替换）
                        write_test_file(
                            project_path=self.project_path,
                            package_name=test_case.package_name,
                            test_code=test_case.full_code,
                            test_class_name=test_case.class_name,
                            merge=False,
                        )

                        # 更新数据库（稍后会统一验证）
                        self.db.save_test_case(test_case)

            if all_timeout_methods:
                logger.info(f"总共识别并删除了 {len(all_timeout_methods)} 个超时的测试方法")

                # 重新检查是否还有测试用例
                if not test_cases:
                    logger.error("所有测试用例都因超时被删除")
                    return {"evaluated": len(mutants), "killed": 0, "mutation_score": 0.0}

                # 统一验证所有过滤后的测试用例
                logger.info("重新编译和验证所有过滤后的测试用例...")
                compile_result = self.java_executor.compile_tests(self.project_path)
                if not compile_result.get("success"):
                    logger.error("过滤后的测试用例编译失败")
                    return {"evaluated": len(mutants), "killed": 0, "mutation_score": 0.0}

                verify_result = self.java_executor.run_tests(self.project_path)
                if verify_result.get("error") == "Timeout":
                    logger.error("过滤后的测试用例仍然超时，放弃评估")
                    return {"evaluated": len(mutants), "killed": 0, "mutation_score": 0.0}
                elif not verify_result.get("success"):
                    logger.warning("过滤后的测试用例仍有失败，继续处理失败的测试方法")
                    # 直接处理失败的测试方法（不能依赖 elif，因为已经在 if 块中）
                    test_result = verify_result
                    # 继续到下面的失败处理逻辑
                else:
                    logger.info("✓ 所有过滤后的测试用例验证通过")
                    # 验证通过，跳过失败处理
                    test_result = verify_result
            else:
                # 测试整体超时，但逐个测试无法识别具体超时方法
                # 这可能是测试间依赖或资源竞争导致的，无法处理
                logger.error("✗ 测试整体超时，但无法识别具体的超时方法（可能是测试间依赖或资源问题）")

                # 删除整个类的所有测试用例
                if current_target and current_target.get("class_name"):
                    target_class = current_target["class_name"]
                    logger.warning(f"删除 {target_class} 类的所有测试用例...")

                    # 获取该类的所有测试用例
                    class_tests = self.db.get_tests_by_target_class(target_class)
                    deleted_count = 0
                    for test_case in class_tests:
                        self._delete_test_file(test_case)
                        self.db.delete_test_case(test_case.id)
                        deleted_count += 1

                    logger.warning(f"已删除 {deleted_count} 个测试用例")

                    # 将目标添加到黑名单
                    if self.state:
                        method_name = current_target.get("method_name", "")
                        target_key = f"{target_class}.{method_name}" if method_name else target_class
                        if not any(ft.get("target") == target_key for ft in self.state.failed_targets):
                            self.state.failed_targets.append({
                                "target": target_key,
                                "class_name": target_class,
                                "method_name": method_name,
                                "reason": "测试整体超时但无法识别具体方法（可能测试间依赖）",
                                "error": "Timeout without identifiable method",
                                "timestamp": datetime.now().isoformat()
                            })
                            logger.warning(f"已将 {target_key} 添加到失败黑名单")
                            # 如果当前目标是被加入黑名单的目标，清除当前目标选中
                            if self.state.current_target:
                                current_class = self.state.current_target.get("class_name")
                                current_method = self.state.current_target.get("method_name", "")
                                current_target_key = f"{current_class}.{current_method}" if current_method and current_class else (current_class if current_class else None)
                                if current_target_key == target_key:
                                    logger.info(f"当前目标 {target_key} 已被加入黑名单，清除目标选中")
                                    self.state.update_target(None)

                return {"evaluated": len(mutants), "killed": 0, "mutation_score": 0.0, "error": "Timeout without identifiable method"}

        # 处理测试失败（可能来自初始运行，也可能来自超时过滤后的验证）
        if not test_result.get("success") and test_result.get("error") != "Timeout":
            logger.warning("部分测试失败，识别有问题的测试方法...")
            from ..executor.surefire_parser import SurefireParser
            surefire_parser = SurefireParser()
            reports_dir = os.path.join(self.project_path, "target", "surefire-reports")
            failed_test_names = surefire_parser.get_failed_test_names(reports_dir)

            if failed_test_names:
                logger.warning(f"发现 {len(failed_test_names)} 个失败的测试")
                # 从数据库中删除失败的测试方法
                for test_case in list(test_cases):
                    methods_to_remove = []
                    for method in test_case.methods:
                        # 构建完整的测试名称
                        if test_case.package_name:
                            full_name = f"{test_case.package_name}.{test_case.class_name}.{method.method_name}"
                        else:
                            full_name = f"{test_case.class_name}.{method.method_name}"

                        if full_name in failed_test_names:
                            logger.warning(f"删除失败的测试方法: {full_name}")
                            methods_to_remove.append(method)

                    # 从测试用例中移除失败的方法
                    for method in methods_to_remove:
                        test_case.methods.remove(method)

                    # 如果测试用例没有方法了，从列表中移除
                    if not test_case.methods:
                        logger.warning(f"测试用例 {test_case.class_name} 的所有方法都失败，删除整个测试用例")
                        test_cases.remove(test_case)
                        self._delete_test_file(test_case)
                        self.db.delete_test_case(test_case.id)
                    else:
                        # 更新数据库中的测试用例
                        self.db.save_test_case(test_case)
        else:
            logger.info("所有测试通过验证")

        # 重新检查是否还有可用的测试用例
        if not test_cases:
            logger.error("预验证后没有可用的测试用例")
            return {"evaluated": len(mutants), "killed": 0, "mutation_score": 0.0}

        logger.info(f"预验证完成，剩余 {len(test_cases)} 个测试用例")

        # ===== 步骤2: 收集覆盖率信息 =====
        logger.info("步骤2: 收集覆盖率信息...")
        coverage_data = None
        try:
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

                    # 直接从 XML 计算全局覆盖率（最准确的方式）
                    coverage_data = parser.aggregate_global_coverage_from_xml(str(jacoco_path))
                    logger.info(
                        f"全局覆盖率（从 XML）: 行覆盖率 {coverage_data['line_coverage']:.1%}, "
                        f"分支覆盖率 {coverage_data['branch_coverage']:.1%}"
                    )

                    # 更新 state 中的全局覆盖率
                    if self.state:
                        self.state.line_coverage = coverage_data['line_coverage']
                        self.state.branch_coverage = coverage_data['branch_coverage']
                        logger.debug(f"已更新 state 中的全局覆盖率: 行 {self.state.line_coverage:.1%}, 分支 {self.state.branch_coverage:.1%}")
                else:
                    logger.warning(f"JaCoCo 报告不存在: {jacoco_path}")
        except Exception as e:
            logger.warning(f"覆盖率分析失败: {e}", exc_info=True)

        # ===== 步骤3: 构建击杀矩阵 =====
        logger.info("步骤3: 构建击杀矩阵...")
        kill_matrix = self.mutation_evaluator.build_kill_matrix(
            mutants=mutants,
            test_cases=test_cases,
            project_path=self.project_path,
        )

        # 保存更新后的变异体状态到数据库
        for mutant in mutants:
            self.db.save_mutant(mutant)
        logger.debug(f"已保存 {len(mutants)} 个变异体的评估状态")

        # ===== 步骤4: 更新度量指标 =====
        logger.info("步骤4: 更新度量指标...")
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

    def update_knowledge(self, type: Optional[str] = None, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
