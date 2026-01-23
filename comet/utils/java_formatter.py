"""Java 代码格式化工具"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class JavaFormatter:
    """Java 代码格式化器 - 调用 google-java-format"""

    def __init__(
        self,
        java_runtime_jar: str,
        java_cmd: str = "java",
        style: str = "GOOGLE",
        timeout: int = 30,
    ):
        self.java_runtime_jar = java_runtime_jar
        self.java_cmd = java_cmd
        self.style = style
        self.timeout = timeout

        if not Path(java_runtime_jar).exists():
            logger.warning(f"Java runtime JAR 不存在: {java_runtime_jar}")

    def format_file(self, file_path: str) -> bool:
        """
        格式化 Java 文件（原地修改）

        Args:
            file_path: 文件路径

        Returns:
            是否成功
        """
        if not Path(file_path).exists():
            logger.error(f"文件不存在: {file_path}")
            return False

        cmd = [
            self.java_cmd,
            "-cp", self.java_runtime_jar,
            "com.comet.formatter.JavaFormatter",
            file_path,
            "--style", self.style,
            "--replace",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            if result.returncode == 0:
                logger.debug(f"格式化成功: {file_path}")
                return True
            else:
                logger.warning(f"格式化失败: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"格式化超时: {file_path}")
            return False
        except Exception as e:
            logger.error(f"格式化异常: {e}")
            return False

    def format_source(self, source: str, temp_file: Optional[str] = None) -> Optional[str]:
        """
        格式化 Java 代码字符串

        Args:
            source: 源代码
            temp_file: 临时文件路径（可选）

        Returns:
            格式化后的代码，失败返回 None
        """
        import tempfile

        if temp_file:
            file_path = Path(temp_file)
        else:
            fd, temp_path = tempfile.mkstemp(suffix=".java")
            file_path = Path(temp_path)
            import os
            os.close(fd)

        try:
            file_path.write_text(source, encoding="utf-8")

            if self.format_file(str(file_path)):
                return file_path.read_text(encoding="utf-8")
            return None

        finally:
            if not temp_file and file_path.exists():
                file_path.unlink()


def get_java_runtime_jar() -> str:
    """获取 Java Runtime JAR 路径"""
    project_root = Path(__file__).parent.parent.parent
    jar_path = project_root / "java-runtime" / "target" / "comet-runtime-1.0.0-jar-with-dependencies.jar"
    return str(jar_path)


def format_java_file(file_path: str, style: str = "GOOGLE") -> bool:
    """
    便捷函数：格式化 Java 文件

    Args:
        file_path: 文件路径
        style: 格式化风格 (GOOGLE 或 AOSP)

    Returns:
        是否成功
    """
    jar_path = get_java_runtime_jar()
    if not Path(jar_path).exists():
        logger.warning(f"Java Runtime JAR 不存在，跳过格式化: {jar_path}")
        return False

    formatter = JavaFormatter(jar_path, style=style)
    return formatter.format_file(file_path)
