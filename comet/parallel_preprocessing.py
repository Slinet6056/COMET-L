"""并行预处理模块 - 并行为所有方法生成测试和变异体"""

import logging
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ParallelPreprocessor:
    """并行预处理器 - 并行为所有公共方法生成测试用例和变异体"""

    def __init__(self, config, components: Dict[str, Any]):
        """
        初始化并行预处理器

        Args:
            config: 系统配置对象
            components: 系统组件字典（包含所有必要的工具和执行器）
        """
        self.config = config
        self.components = components

        # 提取关键组件
        self.sandbox_manager = components["sandbox_manager"]
        self.java_executor = components["java_executor"]
        self.test_generator = components["test_generator"]
        self.mutant_generator = components["mutant_generator"]
        self.static_guard = components["static_guard"]
        self.mutation_evaluator = components["mutation_evaluator"]
        self.db = components["db"]
        self.project_scanner = components["project_scanner"]

        # 线程安全：文件级别的互斥锁
        self._file_locks = defaultdict(threading.Lock)

        # 统计信息
        self._stats = {
            "total_methods": 0,
            "success": 0,
            "failed": 0,
            "total_tests": 0,
            "total_mutants": 0,
            "processing_times": [],
        }
        self._stats_lock = threading.Lock()

        # 获取并发配置
        try:
            preprocess_config = config.preprocessing
            self.max_workers = preprocess_config.max_workers
            self.timeout_per_method = preprocess_config.timeout_per_method
        except AttributeError:
            # 如果配置中没有preprocessing字段，使用默认值
            self.max_workers = None
            self.timeout_per_method = 300

        # 如果未指定max_workers，使用默认值
        if self.max_workers is None:
            import multiprocessing

            cpu_count = multiprocessing.cpu_count()
            self.max_workers = cpu_count

        logger.info(f"并行预处理器初始化完成，最大并发数: {self.max_workers}")

    def run(self, project_path: str, workspace_sandbox: str) -> Dict[str, Any]:
        """
        运行并行预处理

        Args:
            project_path: 原始项目路径
            workspace_sandbox: 工作空间沙箱路径

        Returns:
            预处理统计信息
        """
        logger.info("=" * 60)
        logger.info("开始并行预处理")
        logger.info("=" * 60)

        start_time = time.time()

        # 保存项目路径，供创建独立沙箱使用
        self.project_path = project_path
        self.workspace_sandbox = workspace_sandbox

        # 1. 获取所有目标方法
        logger.info("步骤 1/3: 扫描项目，获取所有公共方法...")
        all_methods = self._get_all_target_methods()

        if not all_methods:
            logger.warning("未找到任何公共方法，跳过预处理")
            return self._stats

        self._stats["total_methods"] = len(all_methods)
        logger.info(f"找到 {len(all_methods)} 个公共方法")

        # 2. 并行处理所有方法
        logger.info(f"步骤 2/3: 并行处理所有方法（并发数: {self.max_workers}）...")
        try:
            self._parallel_process_methods(all_methods)
        except KeyboardInterrupt:
            logger.warning("\n并行预处理被用户中断")
            logger.info("正在保存已处理的结果...")
            # 即使中断也尝试合并已处理的结果
            try:
                self._merge_results_to_workspace()
            except Exception as e:
                logger.warning(f"合并结果时出错: {e}")
            raise  # 重新抛出，让上层处理

        # 3. 合并结果到workspace沙箱
        logger.info("步骤 3/3: 合并结果到workspace沙箱...")
        self._merge_results_to_workspace()

        # 统计耗时
        elapsed_time = time.time() - start_time
        avg_time = (
            sum(self._stats["processing_times"]) / len(self._stats["processing_times"])
            if self._stats["processing_times"]
            else 0
        )

        logger.info("=" * 60)
        logger.info("并行预处理完成")
        logger.info(f"总方法数: {self._stats['total_methods']}")
        logger.info(f"成功: {self._stats['success']}, 失败: {self._stats['failed']}")
        logger.info(f"总测试数: {self._stats['total_tests']}")
        logger.info(f"总变异体数: {self._stats['total_mutants']}")
        logger.info(f"总耗时: {elapsed_time:.2f}秒")
        logger.info(f"平均每个方法: {avg_time:.2f}秒")
        logger.info("=" * 60)

        return self._stats

    def _get_all_target_methods(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        """
        获取项目中所有的公共方法

        Returns:
            (class_name, method_name, method_info) 的列表
        """
        from .utils.project_utils import get_all_java_classes, find_java_file

        all_methods = []

        # 获取所有Java类（传入数据库以获取所有类，包括同一文件中的多个类）
        all_classes = get_all_java_classes(self.workspace_sandbox, db=self.db)
        logger.info(f"找到 {len(all_classes)} 个Java类")

        for class_name in all_classes:
            # 获取该类的所有公共方法（传入数据库以支持多类文件）
            file_path = find_java_file(self.workspace_sandbox, class_name, db=self.db)
            if not file_path:
                logger.warning(f"未找到类文件: {class_name}")
                continue

            try:
                methods = self.java_executor.get_public_methods(str(file_path))
                if methods:
                    for method in methods:
                        if isinstance(method, dict):
                            # 只保留属于当前类的方法
                            method_class = method.get("className")
                            if method_class == class_name:
                                method_name = method.get("name")
                                if method_name:
                                    all_methods.append(
                                        (class_name, method_name, method)
                                    )
                        else:
                            # 如果是字符串（旧格式），直接使用
                            all_methods.append((class_name, method, {}))
            except Exception as e:
                logger.warning(f"获取类 {class_name} 的公共方法失败: {e}")
                continue

        return all_methods

    def _parallel_process_methods(
        self, all_methods: List[Tuple[str, str, Dict[str, Any]]]
    ) -> None:
        """
        并行处理所有方法

        Args:
            all_methods: 方法列表
        """
        completed_count = 0
        interrupted = False

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有任务
            future_to_method = {
                executor.submit(
                    self._process_method_with_timeout,
                    class_name,
                    method_name,
                    method_info,
                ): (class_name, method_name)
                for class_name, method_name, method_info in all_methods
            }

            # 处理完成的任务
            try:
                for future in as_completed(future_to_method):
                    class_name, method_name = future_to_method[future]
                    completed_count += 1

                    try:
                        result = future.result(timeout=5)  # 获取结果时再加一个超时保护
                        if result["success"]:
                            logger.info(
                                f"[{completed_count}/{len(all_methods)}] ✓ {class_name}.{method_name} "
                                f"(测试: {result['tests']}, 变异体: {result['mutants']}, "
                                f"耗时: {result['elapsed']:.2f}s)"
                            )
                        else:
                            logger.warning(
                                f"[{completed_count}/{len(all_methods)}] ✗ {class_name}.{method_name} "
                                f"失败: {result.get('error', 'Unknown')}"
                            )
                    except TimeoutError:
                        logger.error(
                            f"[{completed_count}/{len(all_methods)}] ✗ {class_name}.{method_name} 超时"
                        )
                    except Exception as e:
                        logger.error(
                            f"[{completed_count}/{len(all_methods)}] ✗ {class_name}.{method_name} 异常: {e}"
                        )
            except KeyboardInterrupt:
                interrupted = True
                logger.warning("\n收到中断信号，正在取消未完成的任务...")

                # 取消所有未完成的任务
                pending_count = 0
                for future in future_to_method:
                    if not future.done():
                        future.cancel()
                        pending_count += 1

                logger.warning(f"已取消 {pending_count} 个未完成的任务")

                # 等待正在执行的任务完成（最多等待5秒）
                logger.info("等待正在执行的任务完成...")
                import time

                wait_start = time.time()
                for future in future_to_method:
                    if not future.done() and not future.cancelled():
                        try:
                            future.result(
                                timeout=max(0, 5 - (time.time() - wait_start))
                            )
                        except:
                            pass

                # 重新抛出中断异常，让上层处理
                raise

    def _process_method_with_timeout(
        self, class_name: str, method_name: str, method_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        带超时控制的方法处理（包装函数）

        注意：超时不包括等待文件锁的时间，只计算实际处理时间

        Args:
            class_name: 类名
            method_name: 方法名
            method_info: 方法信息

        Returns:
            处理结果
        """
        from datetime import datetime

        # 记录开始时间，用于后续清理
        start_time = datetime.now()

        # 直接调用处理方法（不使用额外的线程池包装）
        # 超时控制在 _process_method 内部通过检查时间来实现
        try:
            result = self._process_method(class_name, method_name, method_info, start_time)
            return result
        except Exception as e:
            logger.error(f"方法 {class_name}.{method_name} 处理异常: {e}")
            with self._stats_lock:
                self._stats["failed"] += 1
            return {"success": False, "error": str(e)}

    def _cleanup_timeout_data(
        self, class_name: str, method_name: str, start_time
    ) -> None:
        """
        清理超时任务产生的数据库数据

        Args:
            class_name: 类名
            method_name: 方法名
            start_time: 任务开始时间
        """
        from datetime import timedelta

        try:
            # 计算时间窗口（超时时间 + 10秒缓冲）
            time_threshold = start_time - timedelta(seconds=10)

            # 清理在本次处理期间创建的、针对该方法的测试用例
            test_cases = self.db.get_tests_by_target_method(class_name, method_name)
            for tc in test_cases:
                # 检查是否是在本次处理期间创建的
                if tc.created_at and tc.created_at >= time_threshold:
                    # 检查是否编译失败
                    if not tc.compile_success:
                        logger.warning(f"清理超时任务创建的失败测试用例: {tc.id}")
                        self.db.delete_test_case(tc.id)

            # 清理在本次处理期间创建的、针对该方法的变异体
            mutants = self.db.get_mutants_by_method(
                class_name, method_name, status=None
            )
            for mutant in mutants:
                # 检查是否是在本次处理期间创建的
                if mutant.created_at and mutant.created_at >= time_threshold:
                    # 检查是否还未完成评估
                    if mutant.status == "pending" or mutant.evaluated_at is None:
                        logger.warning(f"清理超时任务创建的未完成变异体: {mutant.id}")
                        self.db.delete_mutant(mutant.id)

        except Exception as e:
            logger.warning(f"清理超时任务数据时出错: {e}")

    def _process_method(
        self, class_name: str, method_name: str, method_info: Dict[str, Any], task_start_time=None
    ) -> Dict[str, Any]:
        """
        处理单个方法：生成测试、变异体并评估

        Args:
            class_name: 类名
            method_name: 方法名
            method_info: 方法信息
            task_start_time: 任务开始时间（用于清理超时数据），如果未提供则使用当前时间

        Returns:
            处理结果字典
        """
        from .utils.project_utils import find_java_file, write_test_file
        from .utils.code_utils import extract_class_from_file
        from datetime import datetime

        # 如果未提供任务开始时间，使用当前时间
        if task_start_time is None:
            task_start_time = datetime.now()

        start_time = time.time()
        sandbox_path = None
        sandbox_id = None
        result = {
            "success": False,
            "tests": 0,
            "mutants": 0,
            "elapsed": 0,
        }

        try:
            # 创建独立沙箱
            sandbox_path = self.sandbox_manager.create_target_sandbox(
                self.project_path, class_name, method_name
            )
            sandbox_id = Path(sandbox_path).name

            # 获取文件路径和文件锁（传入数据库以支持多类文件）
            file_path = find_java_file(self.workspace_sandbox, class_name, db=self.db)
            if not file_path:
                logger.error(f"未找到类文件: {class_name}")
                return result

            # 在文件锁保护下执行操作
            file_lock = self._file_locks[str(file_path)]

            with file_lock:
                # 重置超时计时起点：从获取锁后开始计算处理时间
                processing_start_time = time.time()
                timeout_deadline = processing_start_time + self.timeout_per_method
                # 检查超时（在获取锁之后的第一个检查点）
                if time.time() > timeout_deadline:
                    logger.error(
                        f"方法 {class_name}.{method_name} 处理超时 ({self.timeout_per_method}s)"
                    )
                    self._cleanup_timeout_data(class_name, method_name, task_start_time)
                    with self._stats_lock:
                        self._stats["failed"] += 1
                    return {
                        "success": False,
                        "error": f"Timeout after {self.timeout_per_method}s",
                    }

                # 1. 生成测试
                class_code = extract_class_from_file(str(file_path))
                method_signature = method_info.get(
                    "signature", f"public void {method_name}()"
                )

                # 获取现有测试（用于参考）
                existing_tests = self.db.get_tests_by_target_class(class_name)

                test_case = self.test_generator.generate_tests_for_method(
                    class_name=class_name,
                    method_signature=method_signature,
                    class_code=class_code,
                    existing_tests=existing_tests,
                )

                if not test_case:
                    logger.warning(f"测试生成失败: {class_name}.{method_name}")
                    return result

                # 检查超时
                if time.time() > timeout_deadline:
                    logger.error(
                        f"方法 {class_name}.{method_name} 处理超时 ({self.timeout_per_method}s) - 在测试生成后"
                    )
                    self._cleanup_timeout_data(class_name, method_name, task_start_time)
                    with self._stats_lock:
                        self._stats["failed"] += 1
                    return {
                        "success": False,
                        "error": f"Timeout after {self.timeout_per_method}s",
                    }

                # 写入测试文件到沙箱（传入数据库以支持多类文件）
                sandbox_file_path = find_java_file(sandbox_path, class_name, db=self.db)
                test_file = write_test_file(
                    project_path=sandbox_path,
                    package_name=test_case.package_name,
                    test_code=test_case.full_code,
                    test_class_name=test_case.class_name,
                )

                if not test_file:
                    logger.error(f"写入测试文件失败: {class_name}.{method_name}")
                    return result

                # 验证和修复测试
                # 检查超时
                if time.time() > timeout_deadline:
                    logger.error(
                        f"方法 {class_name}.{method_name} 处理超时 ({self.timeout_per_method}s) - 在写入测试文件后"
                    )
                    self._cleanup_timeout_data(class_name, method_name, task_start_time)
                    with self._stats_lock:
                        self._stats["failed"] += 1
                    return {
                        "success": False,
                        "error": f"Timeout after {self.timeout_per_method}s",
                    }

                from .agent.tools import AgentTools

                tools = AgentTools()
                tools.project_path = sandbox_path
                tools.java_executor = self.java_executor
                tools.test_generator = self.test_generator
                tools.db = self.db  # 必须设置db，否则_rebuild_test_file_from_db会失败

                test_case = tools._verify_and_fix_tests(
                    test_case=test_case,
                    class_code=class_code,
                    max_compile_retries=3,
                    max_test_retries=3,
                )

                if not test_case.compile_success:
                    logger.warning(f"测试编译失败: {class_name}.{method_name}")
                    return result

                # 检查超时
                if time.time() > timeout_deadline:
                    logger.error(
                        f"方法 {class_name}.{method_name} 处理超时 ({self.timeout_per_method}s) - 在测试验证后"
                    )
                    self._cleanup_timeout_data(class_name, method_name, task_start_time)
                    with self._stats_lock:
                        self._stats["failed"] += 1
                    return {
                        "success": False,
                        "error": f"Timeout after {self.timeout_per_method}s",
                    }

                # 额外验证：检查测试方法代码中是否包含明显的错误模式
                from .utils.code_utils import validate_test_methods

                invalid_methods = validate_test_methods(test_case.methods, class_code)
                if invalid_methods:
                    logger.warning(
                        f"发现 {len(invalid_methods)} 个包含潜在错误的测试方法，将移除"
                    )
                    test_case.methods = [
                        m
                        for m in test_case.methods
                        if m.method_name not in invalid_methods
                    ]

                    if not test_case.methods:
                        logger.error(
                            f"所有测试方法都包含错误: {class_name}.{method_name}"
                        )
                        return result

                    # 重新构建测试代码
                    from .utils.code_utils import build_test_class

                    method_codes = [m.code for m in test_case.methods]
                    test_case.full_code = build_test_class(
                        test_class_name=test_case.class_name,
                        target_class=test_case.target_class,
                        package_name=test_case.package_name,
                        imports=test_case.imports,
                        test_methods=method_codes,
                    )

                # 保存到数据库
                self.db.save_test_case(test_case)
                result["tests"] = len(test_case.methods)

                # 2. 生成变异体
                mutants = self.mutant_generator.generate_mutants(
                    class_name=class_name,
                    class_code=class_code,
                    num_mutations=5,
                    target_method=method_name,
                )

                if not mutants:
                    logger.warning(f"未生成任何变异体: {class_name}.{method_name}")
                else:
                    # 检查超时
                    if time.time() > timeout_deadline:
                        logger.error(
                            f"方法 {class_name}.{method_name} 处理超时 ({self.timeout_per_method}s) - 在生成变异体后"
                        )
                        self._cleanup_timeout_data(class_name, method_name, task_start_time)
                        with self._stats_lock:
                            self._stats["failed"] += 1
                        return {
                            "success": False,
                            "error": f"Timeout after {self.timeout_per_method}s",
                        }
                    # 静态过滤
                    valid_mutants = self.static_guard.filter_mutants(
                        mutants, str(sandbox_file_path)
                    )

                    # 保存到数据库
                    for mutant in valid_mutants:
                        mutant.patch.file_path = str(
                            file_path
                        )  # 使用workspace的文件路径
                        self.db.save_mutant(mutant)

                    result["mutants"] = len(valid_mutants)

                # 3. 运行初始评估（在沙箱中）
                if result["tests"] > 0 and result["mutants"] > 0:
                    try:
                        # 获取保存的变异体（从数据库）
                        saved_mutants = self.db.get_mutants_by_method(
                            class_name=class_name,
                            method_name=method_name,
                            status="valid",
                        )
                        saved_tests = [test_case]

                        if saved_mutants and saved_tests:
                            # 构建击杀矩阵
                            kill_matrix = self.mutation_evaluator.build_kill_matrix(
                                mutants=saved_mutants,
                                test_cases=saved_tests,
                                project_path=sandbox_path,
                            )

                            # 保存变异体状态
                            for mutant in saved_mutants:
                                self.db.save_mutant(mutant)

                            logger.debug(f"完成评估: {class_name}.{method_name}")
                    except Exception as e:
                        logger.warning(f"评估失败 {class_name}.{method_name}: {e}")

                result["success"] = True

        except Exception as e:
            logger.error(f"处理方法失败 {class_name}.{method_name}: {e}", exc_info=True)
            result["error"] = str(e)

        finally:
            # 清理沙箱
            if sandbox_id:
                try:
                    self.sandbox_manager.cleanup_sandbox(sandbox_id)
                except Exception as e:
                    logger.warning(f"清理沙箱失败 {sandbox_id}: {e}")

            # 记录统计信息
            elapsed = time.time() - start_time
            result["elapsed"] = elapsed

            with self._stats_lock:
                if result["success"]:
                    self._stats["success"] += 1
                    self._stats["total_tests"] += result["tests"]
                    self._stats["total_mutants"] += result["mutants"]
                else:
                    self._stats["failed"] += 1

                self._stats["processing_times"].append(elapsed)

        return result

    def _merge_results_to_workspace(self) -> None:
        """
        将各个独立沙箱中生成的测试文件合并到workspace沙箱
        合并后会验证编译，如果失败则逐个排除有问题的方法
        """
        from .utils.project_utils import write_test_file
        from .utils.code_utils import build_test_class

        # 使用已有的 java_executor
        java_executor = self.java_executor

        # 从数据库获取所有测试用例
        all_test_cases = self.db.get_all_test_cases()

        if not all_test_cases:
            logger.info("没有测试用例需要合并")
            return

        # 按目标类分组
        tests_by_class = defaultdict(list)
        for test_case in all_test_cases:
            tests_by_class[test_case.target_class].append(test_case)

        logger.info(f"合并 {len(tests_by_class)} 个类的测试文件到workspace沙箱...")

        # 为每个目标类重建完整的测试文件
        for target_class, test_cases in tests_by_class.items():
            try:
                # 使用字典去重：{method_name: (TestMethod, updated_at)}
                # 保留更新时间最新的方法（同名方法只保留一个）
                unique_methods = {}

                for tc in test_cases:
                    for method in tc.methods:
                        # 检查是否已存在同名方法
                        if method.method_name in unique_methods:
                            existing_method, existing_updated = unique_methods[
                                method.method_name
                            ]
                            # 比较更新时间，保留更新的
                            method_updated = method.updated_at or method.created_at
                            if (
                                method_updated
                                and existing_updated
                                and method_updated > existing_updated
                            ):
                                unique_methods[method.method_name] = (
                                    method,
                                    method_updated,
                                )
                            # 如果时间相同，比较版本号
                            elif method.version > existing_method.version:
                                unique_methods[method.method_name] = (
                                    method,
                                    method_updated,
                                )
                        else:
                            method_updated = method.updated_at or method.created_at
                            unique_methods[method.method_name] = (
                                method,
                                method_updated,
                            )

                if not unique_methods:
                    continue

                # 提取去重后的方法列表
                all_methods = [m for m, _ in unique_methods.values()]

                # 使用第一个测试用例的元数据
                first_tc = test_cases[0]

                # 构建完整的测试类代码并写入
                valid_methods = self._write_and_validate_merged_test(
                    java_executor=java_executor,
                    test_class_name=first_tc.class_name,
                    target_class=first_tc.target_class,
                    package_name=first_tc.package_name,
                    imports=first_tc.imports,
                    all_methods=all_methods,
                )

                if valid_methods:
                    logger.debug(
                        f"已合并测试文件: {first_tc.class_name} ({len(valid_methods)} 个方法，验证通过)"
                    )

                    # 更新数据库中的测试用例，保存合并后的完整代码
                    # 构建合并后的完整代码
                    from .utils.code_utils import build_test_class

                    method_codes = [m.code for m in valid_methods]
                    merged_full_code = build_test_class(
                        test_class_name=first_tc.class_name,
                        target_class=first_tc.target_class,
                        package_name=first_tc.package_name,
                        imports=first_tc.imports,
                        test_methods=method_codes,
                    )

                    # 更新所有相关测试用例的 full_code
                    for tc in test_cases:
                        tc.full_code = merged_full_code
                        tc.compile_success = True
                        tc.compile_error = None
                        # 更新方法列表为验证通过的方法
                        # 只保留属于当前测试用例的方法
                        tc.methods = [
                            m
                            for m in valid_methods
                            if any(
                                orig_m.method_name == m.method_name
                                for orig_m in tc.methods
                            )
                        ]
                        self.db.save_test_case(tc)

                    logger.info(
                        f"已更新数据库中 {len(test_cases)} 个测试用例的 full_code"
                    )
                else:
                    logger.warning(f"合并后所有方法都编译失败: {first_tc.class_name}")

            except Exception as e:
                logger.error(f"合并测试文件失败 {target_class}: {e}")

    def _write_and_validate_merged_test(
        self,
        java_executor,
        test_class_name: str,
        target_class: str,
        package_name: str,
        imports: list,
        all_methods: list,
        max_iterations: int = 100,
    ) -> list:
        """
        写入合并的测试文件并验证编译和测试，循环排除有问题的方法直到全部通过

        Args:
            java_executor: Java执行器
            test_class_name: 测试类名
            target_class: 目标类名
            package_name: 包名
            imports: 导入语句
            all_methods: 所有测试方法
            max_iterations: 最大迭代次数（防止无限循环）

        Returns:
            验证通过的方法列表
        """
        from .utils.project_utils import write_test_file
        from .utils.code_utils import build_test_class
        import os

        valid_methods = list(all_methods)
        iteration = 0

        # 计算测试文件路径
        if package_name:
            package_path = package_name.replace(".", os.sep)
            test_file_path = os.path.join(
                self.workspace_sandbox,
                "src",
                "test",
                "java",
                package_path,
                f"{test_class_name}.java",
            )
        else:
            test_file_path = os.path.join(
                self.workspace_sandbox, "src", "test", "java", f"{test_class_name}.java"
            )

        while valid_methods and iteration < max_iterations:
            iteration += 1

            # 构建完整的测试类代码
            method_codes = [m.code for m in valid_methods]
            full_code = build_test_class(
                test_class_name=test_class_name,
                target_class=target_class,
                package_name=package_name,
                imports=imports,
                test_methods=method_codes,
            )

            # 写入workspace沙箱
            write_test_file(
                project_path=self.workspace_sandbox,
                package_name=package_name,
                test_code=full_code,
                test_class_name=test_class_name,
                merge=False,
            )

            # 步骤1: 验证编译
            compile_result = java_executor.compile_tests(self.workspace_sandbox)

            if not compile_result.get("success"):
                # 编译失败，分析错误并排除有问题的方法
                error_output = compile_result.get("output", "")
                failed_methods = self._identify_failed_methods_from_compile_error(
                    error_output, test_file_path, valid_methods
                )

                if not failed_methods:
                    # 无法识别具体哪个方法失败，尝试二分查找
                    if len(valid_methods) > 1:
                        # 删除后半部分方法尝试
                        half = len(valid_methods) // 2
                        removed_methods = valid_methods[half:]
                        valid_methods = valid_methods[:half]
                        logger.warning(
                            f"无法识别失败方法，尝试二分排除 {len(removed_methods)} 个方法"
                        )
                        for m in removed_methods:
                            self._delete_test_method_from_db(
                                target_class, m.method_name
                            )
                    elif valid_methods:
                        # 只剩一个方法，删除它
                        removed = valid_methods.pop()
                        logger.warning(
                            f"无法识别失败方法，删除最后的方法: {removed.method_name}"
                        )
                        self._delete_test_method_from_db(
                            target_class, removed.method_name
                        )
                else:
                    # 删除识别出的失败方法
                    for method_name in failed_methods:
                        valid_methods = [
                            m for m in valid_methods if m.method_name != method_name
                        ]
                        logger.warning(f"删除编译失败的方法: {method_name}")
                        self._delete_test_method_from_db(target_class, method_name)

                # 继续下一次迭代
                continue

            # 步骤2: 编译成功后，运行测试
            logger.info(
                f"✓ 合并后编译成功: {test_class_name} ({len(valid_methods)} 个方法)"
            )
            logger.info("开始运行测试，识别失败的测试方法...")

            test_result = java_executor.run_tests(self.workspace_sandbox)

            if test_result.get("success"):
                logger.info(f"✓ 所有测试方法都通过: {test_class_name}")
                return valid_methods

            # 步骤3: 测试失败，解析Surefire报告，识别失败的测试方法
            failed_test_methods = self._identify_failed_test_methods(test_class_name)

            if not failed_test_methods:
                # 无法识别失败的测试方法，但测试确实失败了
                # 尝试二分排除
                if len(valid_methods) > 1:
                    half = len(valid_methods) // 2
                    removed_methods = valid_methods[half:]
                    valid_methods = valid_methods[:half]
                    logger.warning(
                        f"无法识别失败的测试方法，尝试二分排除 {len(removed_methods)} 个方法"
                    )
                    for m in removed_methods:
                        self._delete_test_method_from_db(target_class, m.method_name)
                    continue
                else:
                    logger.warning("无法识别失败的测试方法，保留所有方法")
                    return valid_methods

            # 从文件和数据库中删除失败的测试方法
            logger.warning(
                f"识别到 {len(failed_test_methods)} 个失败的测试方法，将删除"
            )
            for method_name in failed_test_methods:
                valid_methods = [
                    m for m in valid_methods if m.method_name != method_name
                ]
                logger.warning(f"删除测试失败的方法: {method_name}")
                self._delete_test_method_from_db(target_class, method_name)

            if not valid_methods:
                logger.error("所有测试方法都失败了")
                return []

            # 重新构建和验证（确保删除后的方法仍然可以编译和运行）
            logger.info(f"重新验证剩余的 {len(valid_methods)} 个方法...")
            # 继续下一次循环进行验证

        if iteration >= max_iterations:
            logger.error(f"达到最大迭代次数 {max_iterations}，停止验证")

        return valid_methods

    def _delete_test_method_from_db(self, target_class: str, method_name: str) -> None:
        """
        从数据库中删除指定的测试方法（包括 test_methods 表中的所有版本）

        Args:
            target_class: 目标类名
            method_name: 方法名
        """
        try:
            # 获取该类的所有测试用例
            test_cases = self.db.get_tests_by_target_class(target_class)
            deleted_from_any = False

            for test_case in test_cases:
                # 使用新的 delete_test_method 方法直接删除 test_methods 表中的记录
                if self.db.delete_test_method(test_case.id, method_name):
                    deleted_from_any = True
                    logger.debug(f"从数据库删除方法: {test_case.id}.{method_name}")

                    # 重新加载测试用例以检查是否还有方法
                    updated_test_case = self.db.get_test_case(test_case.id)
                    if updated_test_case and not updated_test_case.methods:
                        logger.debug(
                            f"测试用例 {test_case.id} 没有方法了，删除整个测试用例"
                        )
                        self.db.delete_test_case(test_case.id)

            if not deleted_from_any:
                logger.debug(f"未找到需要删除的方法: {target_class}.{method_name}")

        except Exception as e:
            logger.error(f"从数据库删除测试方法失败: {e}")

    def _identify_failed_test_methods(self, test_class_name: str) -> set:
        """
        从Surefire报告中识别失败的测试方法

        Args:
            test_class_name: 测试类名

        Returns:
            失败的测试方法名集合
        """
        from comet.executor.surefire_parser import SurefireParser
        import os

        failed_methods = set()

        try:
            reports_dir = os.path.join(
                self.workspace_sandbox, "target", "surefire-reports"
            )

            if not os.path.exists(reports_dir):
                logger.warning(f"Surefire报告目录不存在: {reports_dir}")
                return failed_methods

            parser = SurefireParser()
            test_results = parser.parse_surefire_reports(reports_dir)

            for suite_result in test_results:
                # 检查是否是当前测试类
                if suite_result.name != test_class_name:
                    # 尝试匹配全限定名
                    if not suite_result.name.endswith(f".{test_class_name}"):
                        continue

                # 添加失败和错误的测试方法
                for test_case in suite_result.test_cases:
                    if not test_case.passed and not test_case.skipped:
                        failed_methods.add(test_case.method_name)
                        logger.debug(f"识别到失败的测试方法: {test_case.method_name}")

            return failed_methods
        except Exception as e:
            logger.error(f"解析Surefire报告失败: {e}")
            return failed_methods

    def _get_method_line_ranges_by_regex(
        self, test_file_path: str, methods: list
    ) -> Dict[str, Tuple[int, int]]:
        """
        使用正则表达式从文件中提取方法行号范围（当 javalang 解析失败时的备用策略）

        Args:
            test_file_path: 测试文件路径
            methods: 测试方法列表（用于获取方法名）

        Returns:
            方法名到 (起始行, 结束行) 的映射
        """
        import re

        method_ranges: Dict[str, Tuple[int, int]] = {}
        method_names = {m.method_name for m in methods}

        try:
            with open(test_file_path, "r", encoding="utf-8") as f:
                source_lines = f.readlines()

            # 查找所有 @Test 注解后面的方法声明
            # 模式: @Test 后面跟着 void methodName(
            method_pattern = re.compile(r"void\s+(\w+)\s*\(")

            methods_with_positions = []
            for i, line in enumerate(source_lines, start=1):
                match = method_pattern.search(line)
                if match:
                    method_name = match.group(1)
                    if method_name in method_names:
                        methods_with_positions.append((method_name, i))

            # 按行号排序
            methods_with_positions.sort(key=lambda x: x[1])

            # 计算每个方法的结束行
            total_lines = len(source_lines)
            for i, (method_name, start_line) in enumerate(methods_with_positions):
                if i + 1 < len(methods_with_positions):
                    end_line = methods_with_positions[i + 1][1] - 1
                else:
                    # 最后一个方法，查找类的结束括号
                    end_line = total_lines
                    for line_idx in range(start_line, total_lines):
                        if source_lines[line_idx].strip() == "}":
                            end_line = line_idx + 1
                            break

                method_ranges[method_name] = (start_line, end_line)
                logger.debug(f"正则匹配方法 {method_name}: 行 {start_line}-{end_line}")

        except Exception as e:
            logger.warning(f"正则解析测试文件失败: {e}")

        return method_ranges

    def _get_method_line_ranges_from_file(
        self, test_file_path: str
    ) -> Dict[str, Tuple[int, int]]:
        """
        使用 javalang 解析测试文件，获取每个方法的精确行号范围

        Args:
            test_file_path: 测试文件路径

        Returns:
            方法名到 (起始行, 结束行) 的映射
        """
        import javalang

        method_ranges: Dict[str, Tuple[int, int]] = {}

        try:
            with open(test_file_path, "r", encoding="utf-8") as f:
                source_code = f.read()
                source_lines = source_code.split("\n")
                total_lines = len(source_lines)

            tree = javalang.parse.parse(source_code)

            # 收集所有方法声明及其起始行
            methods_with_positions = []
            for path, node in tree.filter(javalang.tree.MethodDeclaration):  # type: ignore
                if node.position:  # type: ignore
                    methods_with_positions.append((node.name, node.position.line))  # type: ignore

            # 按行号排序
            methods_with_positions.sort(key=lambda x: x[1])

            # 计算每个方法的结束行（下一个方法的开始行 - 1，或文件结束）
            for i, (method_name, start_line) in enumerate(methods_with_positions):
                if i + 1 < len(methods_with_positions):
                    # 下一个方法的开始行 - 1
                    end_line = methods_with_positions[i + 1][1] - 1
                else:
                    # 最后一个方法，结束行是文件结束或类结束
                    # 简单处理：找到方法开始后第一个只有 '}' 的行作为结束
                    end_line = total_lines
                    for line_idx in range(start_line, total_lines):
                        line = source_lines[line_idx].strip()
                        if line == "}":
                            # 检查是否是方法结束（简单的启发式）
                            end_line = line_idx + 1
                            break

                method_ranges[method_name] = (start_line, end_line)
                logger.debug(f"方法 {method_name}: 行 {start_line}-{end_line}")

        except javalang.parser.JavaSyntaxError as e:
            logger.warning(
                f"javalang 解析失败（语法错误），尝试从错误位置提取信息: {e}"
            )
            # 如果解析失败，返回空字典，让调用者使用备用策略
        except Exception as e:
            logger.warning(f"解析测试文件失败: {e}")

        return method_ranges

    def _identify_failed_methods_from_compile_error(
        self, error_output: str, test_file_path: str, methods: list
    ) -> set:
        """
        从编译错误输出中识别失败的方法

        使用 javalang 解析测试文件获取精确的方法行号范围，
        然后与 Maven 编译错误中的行号进行匹配。

        Args:
            error_output: 编译错误输出
            test_file_path: 测试文件路径
            methods: 所有测试方法

        Returns:
            失败的方法名集合
        """
        import re

        failed_methods = set()
        method_names = {m.method_name for m in methods}

        # 策略1: 从错误输出中直接匹配方法名
        for method_name in method_names:
            if re.search(rf"\b{re.escape(method_name)}\b", error_output):
                failed_methods.add(method_name)
                logger.debug(f"从错误输出中直接识别到失败方法: {method_name}")

        if failed_methods:
            return failed_methods

        # 策略2: 从 Maven 编译错误中提取行号
        # Maven 错误格式: [ERROR] /path/to/File.java:[210,23] error message
        # 或者: File.java:[210,23]
        line_pattern = re.compile(r"\.java:\[(\d+),\d+\]")
        error_lines = set()
        for match in line_pattern.finditer(error_output):
            error_lines.add(int(match.group(1)))

        if not error_lines:
            logger.debug("无法从编译错误中提取行号")
            return failed_methods

        logger.debug(f"从编译错误中提取到行号: {sorted(error_lines)}")

        # 策略3: 使用 javalang 获取精确的方法行号范围
        method_ranges = self._get_method_line_ranges_from_file(test_file_path)

        if method_ranges:
            # 使用精确的行号范围匹配
            for method_name, (start, end) in method_ranges.items():
                for error_line in error_lines:
                    if start <= error_line <= end:
                        failed_methods.add(method_name)
                        logger.info(
                            f"根据行号 {error_line} 精确识别到失败方法: {method_name} (行范围: {start}-{end})"
                        )
                        break
        else:
            # 备用策略：使用正则表达式从文件中提取方法位置
            logger.debug("使用备用策略：通过正则表达式从文件中提取方法位置")
            method_ranges = self._get_method_line_ranges_by_regex(
                test_file_path, methods
            )

            if method_ranges:
                for method_name, (start, end) in method_ranges.items():
                    for error_line in error_lines:
                        if start <= error_line <= end:
                            failed_methods.add(method_name)
                            logger.info(
                                f"根据行号 {error_line} (正则匹配) 识别到失败方法: {method_name} (行范围: {start}-{end})"
                            )
                            break
            else:
                # 最后的备用策略：使用近似的行号范围
                logger.debug("使用最后备用策略：根据方法代码估算行号范围")
                current_line = 20  # 大约的起始行（package、imports 等）

                for method in methods:
                    method_lines = method.code.count("\n") + 1
                    start_line = current_line
                    end_line = current_line + method_lines + 2

                    for error_line in error_lines:
                        if start_line <= error_line <= end_line:
                            failed_methods.add(method.method_name)
                            logger.debug(
                                f"根据行号 {error_line} 估算识别到失败方法: {method.method_name}"
                            )
                            break

                    current_line = end_line + 1

        return failed_methods
