"""静态守护 - 过滤不合法的变异体"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

from ..models import Mutant

logger = logging.getLogger(__name__)


class StaticGuard:
    """静态守护 - 验证变异体是否可以编译"""

    def __init__(self, java_runtime_jar: str):
        """
        初始化静态守护

        Args:
            java_runtime_jar: Java 运行时 JAR 路径
        """
        self.java_runtime_jar = java_runtime_jar

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

            # 创建临时目录和文件（使用正确的类名）
            temp_dir = tempfile.mkdtemp()
            tmp_path = Path(temp_dir) / class_file_name

            # 创建临时文件应用变异
            with open(tmp_path, 'w', encoding='utf-8') as tmp_file:
                # 读取原始文件
                with open(original_file, 'r', encoding='utf-8') as f:
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
                        if not mutated_code.endswith('\n'):
                            mutated_code += '\n'
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

            # 尝试编译
            result = subprocess.run(
                ['javac', str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )

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
            logger.error(f"验证变异体失败: {e}")
            mutant.compile_error = str(e)
            mutant.status = "invalid"
            return False

    def filter_mutants(self, mutants: List[Mutant], original_file: str) -> List[Mutant]:
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

        valid_mutants = []
        invalid_count = 0
        for idx, mutant in enumerate(mutants):
            logger.debug(f"处理变异体 {idx+1}/{len(mutants)}")
            if self.validate_mutant(mutant, original_file):
                valid_mutants.append(mutant)
            else:
                invalid_count += 1
                logger.debug(f"过滤掉不合法的变异体: {mutant.id}")
                logger.debug(f"  语义意图: {mutant.semantic_intent}")
                if mutant.compile_error:
                    logger.debug(f"  编译错误: {mutant.compile_error[:100]}...")

        logger.info(f"过滤结果: {len(valid_mutants)}/{len(mutants)} 个合法变异体")
        if invalid_count > 0:
            logger.warning(f"过滤掉 {invalid_count} 个不合法的变异体")

        return valid_mutants
