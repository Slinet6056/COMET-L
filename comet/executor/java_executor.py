"""Java 执行器接口"""

import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class JavaExecutor:
    """Java 执行器 - Python 侧接口，调用 Java 模块"""

    def __init__(
        self,
        java_runtime_jar: str,
        java_cmd: str = "java",
        test_timeout: int = 30,
        coverage_timeout: int = 300,
    ):
        """
        初始化 Java 执行器

        Args:
            java_runtime_jar: Java 运行时 JAR 路径
            java_cmd: Java 命令路径
            test_timeout: 测试执行超时时间（秒），默认 30 秒
            coverage_timeout: 覆盖率收集超时时间（秒），默认 300 秒
        """
        self.java_runtime_jar = java_runtime_jar
        self.java_cmd = java_cmd
        self.test_timeout = test_timeout
        self.coverage_timeout = coverage_timeout

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
        运行 Java 命令，支持超时后清理整个进程树

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

        process = None
        try:
            # 使用 Popen 代替 run，以便更好地控制进程
            # start_new_session=True 会创建新的进程组，方便后续清理整个进程树
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # 创建新的会话，避免子进程成为孤儿
            )

            try:
                # 等待进程完成或超时
                stdout, stderr = process.communicate(timeout=timeout)

                return {
                    "success": process.returncode == 0,
                    "returncode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            except subprocess.TimeoutExpired as e:
                logger.error(f"命令超时 ({timeout}秒): {' '.join(cmd)}")

                # 超时后清理整个进程组
                self._kill_process_tree(process)

                # 从异常对象中获取已捕获的输出
                stdout = e.stdout or ""
                stderr = e.stderr or ""

                return {
                    "success": False,
                    "error": f"Timeout after {timeout} seconds",
                    "stdout": stdout,
                    "stderr": stderr,
                }
        except Exception as e:
            logger.error(f"命令执行失败: {e}")
            if process:
                self._kill_process_tree(process)
            return {
                "success": False,
                "error": str(e),
            }

    def _try_parse_json_stdout(
        self, result: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        尝试从 _run_java_command 的 stdout 解析 JSON。

        说明：
        - Java 侧有些命令（例如 MavenExecutor）会在失败时也把 JSON 打到 stdout，
          但进程 exit code 会是非 0，导致 _run_java_command 的 success 为 False。
        - 为了让上层拿到更有意义的 error/output 字段，这里尝试解析 stdout。
        """
        stdout = (result.get("stdout") or "").strip()
        if not stdout:
            return None

        # 快速过滤，避免把大量非 JSON 输出交给 json.loads
        if not (stdout.startswith("{") and stdout.endswith("}")):
            return None

        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            return None

        # 合并 python 侧的过程信息，便于排障
        if isinstance(parsed, dict):
            parsed.setdefault("returncode", result.get("returncode"))
            parsed.setdefault("stdout", result.get("stdout", ""))
            parsed.setdefault("stderr", result.get("stderr", ""))
        return parsed if isinstance(parsed, dict) else None

    def _kill_process_tree(self, process: subprocess.Popen) -> None:
        """
        杀死进程及其所有子进程

        Args:
            process: 要终止的进程
        """
        if process is None or process.poll() is not None:
            return

        try:
            # 获取进程组 ID
            pgid = os.getpgid(process.pid)
            logger.warning(f"终止进程组 {pgid} (PID {process.pid})")

            # 先尝试优雅终止 (SIGTERM)
            try:
                os.killpg(pgid, signal.SIGTERM)

                # 等待最多 3 秒
                for _ in range(30):
                    if process.poll() is not None:
                        logger.info(f"进程组 {pgid} 已优雅终止")
                        return
                    time.sleep(0.1)
            except ProcessLookupError:
                # 进程组已经不存在
                return

            # 如果还没终止，强制杀死 (SIGKILL)
            logger.warning(f"强制终止进程组 {pgid}")
            try:
                os.killpg(pgid, signal.SIGKILL)
                process.wait(timeout=2)
                logger.info(f"进程组 {pgid} 已强制终止")
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass

        except Exception as e:
            logger.error(f"清理进程树失败: {e}")
            # 最后尝试直接杀死进程
            try:
                process.kill()
                process.wait(timeout=1)
            except:
                pass

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

        parsed = self._try_parse_json_stdout(result)
        if parsed is not None:
            return parsed
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

        parsed = self._try_parse_json_stdout(result)
        if parsed is not None:
            return parsed

        if result.get("success"):
            try:
                return json.loads(result["stdout"])
            except json.JSONDecodeError:
                # JSON 解析失败，但命令执行成功，可能是 Java 警告污染了输出
                # 尝试提取 JSON 部分（通常在最后）
                stdout = result.get("stdout", "")
                logger.warning("编译输出无法解析为 JSON，尝试提取 JSON 部分")

                # 尝试找到最后一个 { 开始的 JSON
                last_brace = stdout.rfind("{")
                if last_brace != -1:
                    try:
                        json_part = stdout[last_brace:]
                        parsed = json.loads(json_part)
                        logger.info("成功从污染的输出中提取 JSON")
                        return parsed
                    except json.JSONDecodeError:
                        pass

                # 如果仍然失败，但 exitCode 是 0，假设编译成功
                logger.warning("无法解析 JSON，但命令执行成功，假设编译成功")
                return {
                    "success": True,
                    "exitCode": 0,
                    "note": "Compilation succeeded but output could not be parsed",
                }

        # 命令执行失败，提取详细错误信息
        error_msg = result.get("error", "")
        stderr = result.get("stderr", "")
        stdout = result.get("stdout", "")

        # 修复：如果所有输出都是空的，返回更详细的错误信息
        if not error_msg and not stderr and not stdout:
            logger.error(f"编译失败但没有任何输出信息，可能是进程被中断或超时")
            logger.error(f"  项目路径: {project_path}")
            return {
                "success": False,
                "error": "Compilation failed with no output (possible timeout or interruption)",
                "output": "",
            }

        # 优先使用 stderr，如果没有则使用 stdout
        detailed_error = stderr if stderr else stdout
        if not detailed_error:
            detailed_error = error_msg

        return {
            "success": False,
            "error": detailed_error,
            "stderr": stderr,
            "stdout": stdout,
            "output": detailed_error,  # 添加output字段以保持一致性
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
            timeout=self.test_timeout,  # 使用配置的超时时间
        )

        parsed = self._try_parse_json_stdout(result)
        if parsed is not None:
            return parsed

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
            timeout=self.coverage_timeout,  # 使用覆盖率专用的超时时间（更长）
        )

        parsed = self._try_parse_json_stdout(result)
        if parsed is not None:
            return parsed

        if result.get("success"):
            try:
                return json.loads(result["stdout"])
            except json.JSONDecodeError:
                return {
                    "success": True,
                    "raw_output": result["stdout"],
                }
        return result

    def run_single_test_method(
        self, project_path: str, test_class: str, test_method: str
    ) -> Dict[str, Any]:
        """
        运行单个测试方法

        Args:
            project_path: 项目路径
            test_class: 测试类名（完整类名，如 com.example.CalculatorTest）
            test_method: 测试方法名

        Returns:
            测试结果
        """
        # Maven 的 test 参数格式: ClassName#methodName
        test_pattern = f"{test_class}#{test_method}"

        result = self._run_java_command(
            "com.comet.executor.MavenExecutor",
            ["singleTest", project_path, test_pattern],
            timeout=self.test_timeout,
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
