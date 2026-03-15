"""静态守护 - 过滤不合法的变异体"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from ..models import Mutant

logger = logging.getLogger(__name__)


class StaticGuard:
    """静态守护 - 验证变异体是否可以编译"""

    _RETRYABLE_JAVAC_MARKERS: tuple[str, ...] = (
        "package ",
        "程序包",
        "cannot find symbol",
        "找不到符号",
        "class file for",
        "无法访问",
        "cannot access",
    )

    _NON_RETRYABLE_JAVAC_MARKERS: tuple[str, ...] = (
        "';' expected",
        "需要';'",
        "not a statement",
        "illegal start of expression",
        "illegal start of type",
        "reached end of file while parsing",
        "')' expected",
        "'}' expected",
    )

    def __init__(
        self,
        java_runtime_jar: str,
        javac_cmd: str = "javac",
        mvn_cmd: str = "mvn",
        env: Optional[dict[str, str]] = None,
    ):
        """
        初始化静态守护

        Args:
            java_runtime_jar: Java 运行时 JAR 路径
        """
        self.java_runtime_jar: str = java_runtime_jar
        self.javac_cmd: str = javac_cmd
        self.mvn_cmd: str = mvn_cmd
        self.env: dict[str, str] | None = env
        self._classpath_cache: dict[str, Optional[str]] = {}

    def validate_mutant(self, mutant: Mutant, original_file: str) -> bool:
        """
        验证变异体是否合法（能够编译）

        Args:
            mutant: 变异体对象
            original_file: 原始文件路径

        Returns:
            是否合法
        """
        try:
            logger.debug(f"验证变异体 {mutant.id}")
            logger.debug(f"  原始代码: {mutant.patch.original_code[:100]}...")
            logger.debug(f"  变异代码: {mutant.patch.mutated_code[:100]}...")
            logger.debug(f"  行范围: {mutant.patch.line_start}-{mutant.patch.line_end}")

            # 提取类名以创建正确的文件名
            # 从原始文件中提取类名
            original_path = Path(original_file)
            class_file_name = original_path.name  # 例如: Calculator.java

            # 推断项目根目录和 classpath
            # 从 original_file 路径向上查找包含 pom.xml 的目录
            project_root = self._find_project_root(original_file)

            # 如果找不到 target/classes，尝试编译项目
            classpath = self._build_classpath(project_root)
            target_classes = project_root / "target" / "classes" if project_root else None
            if project_root and target_classes and not target_classes.exists():
                logger.info(f"未找到编译输出，尝试编译项目: {project_root}")
                if self._compile_project(project_root):
                    classpath = self._build_classpath(project_root)

            # 创建临时目录和文件（使用正确的类名）
            temp_dir = tempfile.mkdtemp()
            tmp_path = Path(temp_dir) / class_file_name

            # 创建临时文件应用变异
            with open(tmp_path, "w", encoding="utf-8") as tmp_file:
                # 读取原始文件
                with open(original_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                logger.debug(f"原始文件有 {len(lines)} 行")

                # 应用变异
                mutated_lines = []
                mutation_applied = False
                for i, line in enumerate(lines):
                    line_num = i + 1
                    if line_num < mutant.patch.line_start:
                        # 在变异范围之前，保留原行
                        mutated_lines.append(line)
                    elif line_num == mutant.patch.line_start:
                        # 在起始行，插入变异代码（只插入一次）
                        # 确保变异代码以换行符结尾
                        mutated_code = mutant.patch.mutated_code
                        if not mutated_code.endswith("\n"):
                            mutated_code += "\n"
                        mutated_lines.append(mutated_code)
                        mutation_applied = True
                    elif line_num > mutant.patch.line_start and line_num <= mutant.patch.line_end:
                        # 在变异范围内（起始行之后），跳过这些行
                        continue
                    else:
                        # 在变异范围之后，保留原行
                        mutated_lines.append(line)

                tmp_file.writelines(mutated_lines)

                if not mutation_applied:
                    logger.warning(f"变异体 {mutant.id} 未成功应用（行号范围可能不正确）")
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    return False

            logger.debug(f"创建临时文件: {tmp_path}")

            result = self._run_javac(tmp_path, classpath)

            if (
                result.returncode != 0
                and project_root
                and self._is_retryable_javac_failure(result.stderr)
                and self._compile_mutant_with_maven(mutant, original_file, project_root)
            ):
                shutil.rmtree(temp_dir, ignore_errors=True)
                mutant.compile_error = None
                mutant.status = "valid"
                logger.debug(f"变异体 {mutant.id} 通过 Maven 回退验证")
                return True

            # 清理临时文件和目录
            shutil.rmtree(temp_dir, ignore_errors=True)

            if result.returncode != 0:
                mutant.compile_error = result.stderr
                mutant.status = "invalid"
                logger.warning(f"变异体 {mutant.id} 编译失败:")
                logger.warning(f"  错误: {result.stderr[:200]}...")
                return False

            mutant.status = "valid"
            logger.debug(f"变异体 {mutant.id} 验证通过")
            return True

        except Exception as e:
            logger.warning(f"验证变异体失败: {e}")
            mutant.compile_error = str(e)
            mutant.status = "invalid"
            return False

    def filter_mutants(self, mutants: list[Mutant], original_file: str) -> list[Mutant]:
        """
        过滤变异体列表，只保留合法的

        Args:
            mutants: 变异体列表
            original_file: 原始文件路径

        Returns:
            合法的变异体列表
        """
        logger.info(f"开始过滤 {len(mutants)} 个变异体")
        logger.debug(f"原始文件: {original_file}")

        valid_mutants: list[Mutant] = []
        invalid_count = 0
        for idx, mutant in enumerate(mutants):
            logger.debug(f"处理变异体 {idx + 1}/{len(mutants)}")
            if self.validate_mutant(mutant, original_file):
                valid_mutants.append(mutant)
            else:
                invalid_count += 1
                logger.debug(f"过滤掉不合法的变异体: {mutant.id}")
                if mutant.compile_error:
                    logger.debug(f"  编译错误: {mutant.compile_error}")

        logger.info(f"过滤结果: {len(valid_mutants)}/{len(mutants)} 个合法变异体")
        if invalid_count > 0:
            logger.warning(f"过滤掉 {invalid_count} 个不合法的变异体")

        return valid_mutants

    def _find_project_root(self, file_path: str) -> Optional[Path]:
        """
        从文件路径向上查找包含 pom.xml 的项目根目录

        Args:
            file_path: 源文件路径

        Returns:
            项目根目录路径，如果找不到返回 None
        """
        current = Path(file_path).parent
        while current != current.parent:  # 到达文件系统根目录就停止
            if (current / "pom.xml").exists():
                logger.debug(f"找到项目根目录: {current}")
                return current
            current = current.parent
        logger.warning(f"未找到项目根目录 (pom.xml) for {file_path}")
        return None

    def _build_classpath(self, project_root: Optional[Path]) -> Optional[str]:
        """
        构建 javac 的 classpath

        Args:
            project_root: 项目根目录

        Returns:
            classpath 字符串，如果无法构建返回 None
        """
        if not project_root:
            return None

        cache_key = str(project_root.resolve())
        if cache_key in self._classpath_cache:
            return self._classpath_cache[cache_key]

        classpath_parts = []

        # 添加项目的编译输出目录
        target_classes = project_root / "target" / "classes"
        if target_classes.exists():
            classpath_parts.append(str(target_classes))
            logger.debug(f"添加 target/classes 到 classpath: {target_classes}")
        else:
            logger.warning(f"target/classes 不存在: {target_classes}")

        maven_classpath = self._resolve_maven_classpath(project_root)
        if maven_classpath:
            classpath_parts.append(maven_classpath)
        else:
            filesystem_classpath = self._build_filesystem_classpath(project_root)
            if filesystem_classpath:
                classpath_parts.append(filesystem_classpath)

        classpath = os.pathsep.join(part for part in classpath_parts if part)
        if not classpath:
            logger.warning("无法构建 classpath - 没有找到编译输出或依赖")
            self._classpath_cache[cache_key] = None
            return None

        logger.debug(f"构建的 classpath: {classpath[:200]}...")
        self._classpath_cache[cache_key] = classpath
        return classpath

    def _build_filesystem_classpath(self, project_root: Path) -> Optional[str]:
        classpath_parts = []

        target_deps = project_root / "target" / "dependency"
        if target_deps.exists():
            jar_files = list(target_deps.glob("*.jar"))
            for jar in jar_files:
                classpath_parts.append(str(jar))
            if jar_files:
                logger.debug(f"添加 {len(jar_files)} 个依赖 jar 到 classpath")

        if not classpath_parts:
            return None

        return os.pathsep.join(classpath_parts)

    def _resolve_maven_classpath(self, project_root: Path) -> Optional[str]:
        fd, output_path = tempfile.mkstemp(prefix="comet-classpath-", suffix=".txt")
        os.close(fd)
        output_file = Path(output_path)
        try:
            result = subprocess.run(
                [
                    self.mvn_cmd,
                    "-q",
                    "-DincludeScope=compile",
                    f"-Dmdep.pathSeparator={os.pathsep}",
                    "-Dmdep.outputAbsoluteArtifactFilename=true",
                    f"-Dmdep.outputFile={output_file}",
                    "dependency:build-classpath",
                ],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=120,
                env=self.env,
            )
            if result.returncode != 0:
                logger.debug(f"Maven classpath 解析失败: {result.stderr}")
                return None

            if not output_file.exists():
                return None

            content = output_file.read_text(encoding="utf-8").strip()
            return content or None
        except subprocess.TimeoutExpired:
            logger.warning(f"Maven classpath 解析超时: {project_root}")
            return None
        except Exception as e:
            logger.warning(f"Maven classpath 解析出错: {e}")
            return None
        finally:
            output_file.unlink(missing_ok=True)

    def _run_javac(
        self, file_path: Path, classpath: Optional[str]
    ) -> subprocess.CompletedProcess[str]:
        javac_cmd = [self.javac_cmd]
        if classpath:
            javac_cmd.extend(["-cp", classpath])
        javac_cmd.append(str(file_path))
        return subprocess.run(
            javac_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=self.env,
        )

    def _is_retryable_javac_failure(self, stderr: str) -> bool:
        message = stderr.lower()
        if any(marker.lower() in message for marker in self._NON_RETRYABLE_JAVAC_MARKERS):
            return False
        return any(marker.lower() in message for marker in self._RETRYABLE_JAVAC_MARKERS)

    def _compile_mutant_with_maven(
        self, mutant: Mutant, original_file: str, project_root: Path
    ) -> bool:
        original_path = Path(original_file)
        original_content = original_path.read_text(encoding="utf-8")
        mutated_content = self._build_mutated_source(mutant, original_content)
        if mutated_content is None:
            return False

        isolated_root = Path(tempfile.mkdtemp(prefix="comet-mutant-maven-"))
        try:
            self._copy_project_for_maven_validation(project_root, isolated_root)
            isolated_file = isolated_root / original_path.relative_to(project_root)
            isolated_file.parent.mkdir(parents=True, exist_ok=True)
            _ = isolated_file.write_text(mutated_content, encoding="utf-8")
            return self._compile_project(isolated_root)
        finally:
            shutil.rmtree(isolated_root, ignore_errors=True)

    def _copy_project_for_maven_validation(self, project_root: Path, isolated_root: Path) -> None:
        shutil.copytree(
            project_root,
            isolated_root,
            ignore=shutil.ignore_patterns("target", ".git", ".idea", ".gradle"),
            dirs_exist_ok=True,
        )

    def _build_mutated_source(self, mutant: Mutant, original_content: str) -> Optional[str]:
        lines = original_content.splitlines(keepends=True)
        mutated_lines = []
        mutation_applied = False

        for i, line in enumerate(lines):
            line_num = i + 1
            if line_num < mutant.patch.line_start:
                mutated_lines.append(line)
            elif line_num == mutant.patch.line_start:
                mutated_code = mutant.patch.mutated_code
                if not mutated_code.endswith("\n"):
                    mutated_code += "\n"
                mutated_lines.append(mutated_code)
                mutation_applied = True
            elif line_num > mutant.patch.line_start and line_num <= mutant.patch.line_end:
                continue
            else:
                mutated_lines.append(line)

        if not mutation_applied:
            logger.warning(f"变异体 {mutant.id} 未成功应用（行号范围可能不正确）")
            return None

        return "".join(mutated_lines)

    def _compile_project(self, project_root: Path) -> bool:
        """
        编译 Maven 项目

        Args:
            project_root: 项目根目录

        Returns:
            编译是否成功
        """
        try:
            logger.info(f"开始编译项目: {project_root}")
            result = subprocess.run(
                [self.mvn_cmd, "compile", "-q"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=300,  # 5分钟超时
                env=self.env,
            )

            if result.returncode == 0:
                logger.info(f"项目编译成功: {project_root}")
                return True
            else:
                logger.warning(f"项目编译失败: {project_root}")
                logger.debug(f"编译错误: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            logger.warning(f"项目编译超时: {project_root}")
            return False
        except Exception as e:
            logger.warning(f"项目编译出错: {e}")
            return False
