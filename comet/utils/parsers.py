"""LLM 输出解析器 - 解析代码生成的各种输出格式"""

import re
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def parse_mutation_response(response: str) -> List[Dict[str, Any]]:
    """
    解析变异体生成的响应（分隔符格式）

    格式示例：
    ===MUTANT===
    LINES: 18-20
    ORIGINAL:
        if (value == null) {
            throw new IllegalArgumentException();
        }
    MUTATED:
        if (value == null) {
            return null;
        }

    Args:
        response: LLM 返回的原始文本

    Returns:
        变异体数据列表，每个元素包含 line_start, line_end, original, mutated
    """
    mutants = []

    # 移除可能的 markdown 代码块标记
    response = response.strip()
    if response.startswith("```"):
        lines = response.split("\n")
        # 移除第一行和最后一行的代码块标记
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response = "\n".join(lines)

    # 按 ===MUTANT=== 分割
    parts = response.split("===MUTANT===")

    for part in parts:
        part = part.strip()
        if not part:
            continue

        try:
            # 提取 LINES 信息
            lines_match = re.search(r"LINES:\s*(\d+)-(\d+)", part)
            if not lines_match:
                logger.warning("无法找到 LINES 信息，跳过这个变异体")
                continue

            line_start = int(lines_match.group(1))
            line_end = int(lines_match.group(2))

            # 提取 ORIGINAL 和 MUTATED 代码
            # 查找 ORIGINAL: 和 MUTATED: 标记的位置
            original_match = re.search(
                r"ORIGINAL:\s*\n(.*?)\n(?:MUTATED:|$)", part, re.DOTALL
            )
            mutated_match = re.search(r"MUTATED:\s*\n(.*?)$", part, re.DOTALL)

            if not original_match or not mutated_match:
                logger.warning("无法找到 ORIGINAL 或 MUTATED 代码，跳过这个变异体")
                logger.debug(f"Part 内容: {part[:200]}...")
                continue

            original_code = original_match.group(1).rstrip()
            mutated_code = mutated_match.group(1).rstrip()

            # 验证代码不为空
            if not original_code.strip() or not mutated_code.strip():
                logger.warning("ORIGINAL 或 MUTATED 代码为空，跳过这个变异体")
                continue

            mutants.append(
                {
                    "line_start": line_start,
                    "line_end": line_end,
                    "original": original_code,
                    "mutated": mutated_code,
                }
            )

        except Exception as e:
            logger.warning(f"解析变异体失败: {e}")
            logger.debug(f"Part 内容: {part[:200]}...")
            continue

    logger.info(f"成功解析 {len(mutants)} 个变异体")
    return mutants


def parse_test_method_response(response: str) -> Optional[str]:
    """
    解析测试方法生成的响应（纯代码格式）

    Args:
        response: LLM 返回的原始文本（应该是一个完整的测试方法）

    Returns:
        测试方法代码，如果解析失败则返回 None
    """
    code = response.strip()

    # 移除可能的 markdown 代码块标记
    if code.startswith("```"):
        lines = code.split("\n")
        # 移除第一行（```java 或 ```）
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 移除最后一行（```）
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines).strip()

    # 验证代码包含 @Test 注解
    if "@Test" not in code:
        logger.warning("解析的代码不包含 @Test 注解")
        return None

    # 验证代码看起来像一个方法（有 void 或其他返回类型，有括号）
    if not re.search(r"(public|protected|private)?\s*(static\s+)?\w+\s+\w+\s*\(", code):
        logger.warning("解析的代码不像是一个完整的方法")
        return None

    logger.info("成功解析测试方法")
    return code


def parse_test_class_response(response: str) -> Optional[str]:
    """
    解析测试类修复的响应（完整类代码）

    Args:
        response: LLM 返回的原始文本（应该是一个完整的测试类）

    Returns:
        测试类代码，如果解析失败则返回 None
    """
    code = response.strip()

    # 移除可能的 markdown 代码块标记
    if code.startswith("```"):
        lines = code.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines).strip()

    # 验证代码包含 class 关键字
    if not re.search(r"(public|protected|private)?\s*class\s+\w+", code):
        logger.warning("解析的代码不包含 class 定义")
        return None

    logger.info("成功解析测试类")
    return code


def extract_test_method_name(test_code: str) -> Optional[str]:
    """
    从测试方法代码中提取方法名

    Args:
        test_code: 测试方法代码

    Returns:
        方法名，如果提取失败则返回 None
    """
    # 匹配方法定义：@Test 注解后的第一个方法
    # 支持多种格式：void methodName(), public void methodName(), static void methodName() 等
    match = re.search(
        r"@Test.*?\n\s*(public|protected|private)?\s*(static\s+)?\w+\s+(\w+)\s*\(",
        test_code,
        re.DOTALL,
    )

    if match:
        method_name = match.group(3)
        return method_name

    logger.warning("无法从测试代码中提取方法名")
    return None
