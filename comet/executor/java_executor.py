"""Java 执行器接口"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class JavaExecutor:
    """Java 执行器 - Python 侧接口，调用 Java 模块"""

    def __init__(self, java_runtime_jar: str, java_cmd: str = "java"):
        """
        初始化 Java 执行器

        Args:
            java_runtime_jar: Java 运行时 JAR 路径
            java_cmd: Java 命令路径
        """
        self.java_runtime_jar = java_runtime_jar
        self.java_cmd = java_cmd

        # 检查 JAR 文件是否存在
        if not Path(java_runtime_jar).exists():
            logger.warning(f"Java runtime JAR 不存在: {java_runtime_jar}")

    def _run_java_command(
        self,
        main_class: str,
        args: list[str],
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """
        运行 Java 命令

        Args:
            main_class: 主类名
            args: 参数列表
            timeout: 超时时间（秒）

        Returns:
            结果字典
        """
        cmd = [
            self.java_cmd,
            "-cp",
            self.java_runtime_jar,
            main_class,
        ] + args

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            logger.error(f"命令超时: {' '.join(cmd)}")
            return {
                "success": False,
                "error": "Timeout",
            }
        except Exception as e:
            logger.error(f"命令执行失败: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    def analyze_code(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        分析 Java 代码文件

        Args:
            file_path: 文件路径

        Returns:
            分析结果
        """
        result = self._run_java_command(
            "com.comet.analyzer.CodeAnalyzer",
            ["analyze", file_path],
        )

        if result.get("success"):
            try:
                return json.loads(result["stdout"])
            except json.JSONDecodeError:
                logger.error(f"解析分析结果失败: {result['stdout']}")
                return None
        return None

    def get_public_methods(self, file_path: str) -> Optional[list]:
        """
        获取类的所有 public 方法

        Args:
            file_path: 文件路径

        Returns:
            方法列表
        """
        result = self._run_java_command(
            "com.comet.analyzer.CodeAnalyzer",
            ["publicMethods", file_path],
        )

        if result.get("success"):
            try:
                return json.loads(result["stdout"])
            except json.JSONDecodeError:
                return None
        return None

    def apply_mutation(
        self,
        source_file: str,
        patch_json: str,
        output_path: str,
    ) -> Dict[str, Any]:
        """
        应用变异

        Args:
            source_file: 源文件路径
            patch_json: 变异补丁 JSON
            output_path: 输出路径

        Returns:
            应用结果字典 {success: bool, error: str, stdout: str, stderr: str}
        """
        result = self._run_java_command(
            "com.comet.mutator.MutationApplier",
            [source_file, patch_json, output_path],
        )

        # 记录详细信息
        if not result.get("success", False):
            logger.error(f"变异应用失败:")
            logger.error(f"  源文件: {source_file}")
            logger.error(f"  输出路径: {output_path}")
            logger.error(f"  补丁: {patch_json[:200]}...")
            if result.get("stderr"):
                logger.error(f"  错误信息: {result['stderr']}")
            if result.get("stdout"):
                logger.error(f"  标准输出: {result['stdout']}")

        return result

    def compile_project(self, project_path: str) -> Dict[str, Any]:
        """
        编译项目

        Args:
            project_path: 项目路径

        Returns:
            编译结果
        """
        result = self._run_java_command(
            "com.comet.executor.MavenExecutor",
            ["compile", project_path],
        )

        if result.get("success"):
            try:
                return json.loads(result["stdout"])
            except json.JSONDecodeError:
                return {"success": False, "error": "Failed to parse output"}
        return result

    def compile_tests(self, project_path: str) -> Dict[str, Any]:
        """
        编译测试

        Args:
            project_path: 项目路径

        Returns:
            编译结果
        """
        result = self._run_java_command(
            "com.comet.executor.MavenExecutor",
            ["compileTests", project_path],
        )

        if result.get("success"):
            try:
                return json.loads(result["stdout"])
            except json.JSONDecodeError:
                # JSON 解析失败，但命令执行成功，可能是格式问题
                logger.warning("编译输出无法解析为 JSON，尝试使用原始输出")
                return {
                    "success": False,
                    "error": f"Failed to parse output: {result.get('stdout', '')[:500]}"
                }

        # 命令执行失败，提取详细错误信息
        error_msg = result.get("error", "")
        stderr = result.get("stderr", "")
        stdout = result.get("stdout", "")

        # 优先使用 stderr，如果没有则使用 stdout
        detailed_error = stderr if stderr else stdout
        if not detailed_error:
            detailed_error = error_msg

        return {
            "success": False,
            "error": detailed_error,
            "stderr": stderr,
            "stdout": stdout,
        }

    def run_tests(self, project_path: str) -> Dict[str, Any]:
        """
        运行测试

        Args:
            project_path: 项目路径

        Returns:
            测试结果
        """
        result = self._run_java_command(
            "com.comet.executor.MavenExecutor",
            ["test", project_path],
            timeout=600,  # 测试可能需要更长时间
        )

        if result.get("success"):
            try:
                return json.loads(result["stdout"])
            except json.JSONDecodeError:
                # 如果不是 JSON，返回原始输出
                return {
                    "success": True,
                    "raw_output": result["stdout"],
                }
        return result

    def run_tests_with_coverage(self, project_path: str) -> Dict[str, Any]:
        """
        运行测试并生成覆盖率报告

        Args:
            project_path: 项目路径

        Returns:
            测试和覆盖率结果
        """
        result = self._run_java_command(
            "com.comet.executor.MavenExecutor",
            ["testWithCoverage", project_path],
            timeout=600,
        )

        if result.get("success"):
            try:
                return json.loads(result["stdout"])
            except json.JSONDecodeError:
                return {
                    "success": True,
                    "raw_output": result["stdout"],
                }
        return result
