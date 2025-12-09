"""代码处理工具函数 - 轻量级文本处理

复杂的 Java 代码解析（提取方法、签名等）使用 JavaExecutor
"""

import re
import logging
from typing import List, Dict, Optional, Set

logger = logging.getLogger(__name__)


def extract_imports(java_code: str) -> List[str]:
    """
    从 Java 代码中提取 import 语句

    Args:
        java_code: Java 源代码

    Returns:
        import 语句列表
    """
    import_pattern = r"^\s*import\s+[^;]+;"
    imports = re.findall(import_pattern, java_code, re.MULTILINE)
    return [imp.strip() for imp in imports]


def parse_java_class(java_code: str) -> Dict[str, Optional[str]]:
    """
    解析 Java 类的基本信息

    Args:
        java_code: Java 源代码

    Returns:
        包含类名、包名等信息的字典
    """
    result = {
        "package": None,
        "class_name": None,
        "is_public": False,
    }

    # 提取包名
    package_match = re.search(r"^\s*package\s+([^;]+);", java_code, re.MULTILINE)
    if package_match:
        result["package"] = package_match.group(1).strip()

    # 提取类名
    class_match = re.search(
        r"^\s*(public\s+)?(class|interface|enum)\s+(\w+)", java_code, re.MULTILINE
    )
    if class_match:
        result["is_public"] = class_match.group(1) is not None
        result["class_name"] = class_match.group(3)

    return result


def add_line_numbers(code: str, start: int = 1) -> str:
    """
    为代码添加行号

    Args:
        code: 源代码
        start: 起始行号

    Returns:
        带行号的代码
    """
    lines = code.split("\n")
    numbered_lines = [f"{i + start:4d} | {line}" for i, line in enumerate(lines)]
    return "\n".join(numbered_lines)


def extract_class_from_file(file_path: str) -> str:
    """
    从文件中提取 Java 类代码

    Args:
        file_path: 文件路径

    Returns:
        类代码
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def build_test_class(
    test_class_name: str,
    target_class: str,
    package_name: Optional[str],
    imports: List[str],
    test_methods: List[str],
) -> str:
    """
    构建完整的测试类代码

    Args:
        test_class_name: 测试类名
        target_class: 被测类名
        package_name: 包名
        imports: 导入语句列表
        test_methods: 测试方法代码列表

    Returns:
        完整的测试类代码
    """
    lines = []

    # 包声明
    if package_name:
        lines.append(f"package {package_name};")
        lines.append("")

    # 导入语句
    default_imports = [
        # JUnit 5 核心注解和扩展
        "import org.junit.jupiter.api.*;",
        "import org.junit.jupiter.api.extension.*;",
        # JUnit 5 参数化测试
        "import org.junit.jupiter.params.*;",
        "import org.junit.jupiter.params.provider.*;",
        # JUnit 5 断言（静态导入）
        "import static org.junit.jupiter.api.Assertions.*;",
        "import static org.junit.jupiter.api.Assumptions.*;",
        # Mockito 核心（静态导入）
        "import static org.mockito.Mockito.*;",
        "import static org.mockito.ArgumentMatchers.*;",
        # Mockito 类和注解
        "import org.mockito.*;",
        "import org.mockito.stubbing.*;",
        "import org.mockito.junit.jupiter.*;",
        # Java 反射 API（用于测试私有字段和方法）
        "import java.lang.reflect.*;",
    ]

    all_imports = default_imports + [
        imp for imp in imports if imp not in default_imports
    ]
    lines.extend(all_imports)
    lines.append("")

    # 类声明
    lines.append(f"public class {test_class_name} {{")
    lines.append("")

    # 测试方法
    for method_code in test_methods:
        # 缩进
        indented = "\n".join("    " + line for line in method_code.split("\n"))
        lines.append(indented)
        lines.append("")

    # 类结束
    lines.append("}")

    return "\n".join(lines)


def validate_test_methods(methods: list, class_code: str) -> Set[str]:
    """
    验证测试方法代码，检查是否包含明显的错误模式
    使用 javalang 静态分析源代码中的 public 方法，然后检查测试代码是否调用了不存在的方法

    Args:
        methods: 测试方法列表（每个元素需有 .method_name 和 .code 属性）
        class_code: 被测类的代码

    Returns:
        包含错误的方法名集合
    """
    invalid_methods: Set[str] = set()

    # 使用javalang提取被测类的所有public方法
    public_methods: Set[str] = set()
    try:
        import javalang

        tree = javalang.parse.parse(class_code)

        for path, node in tree.filter(javalang.tree.MethodDeclaration):  # type: ignore
            if "public" in node.modifiers:  # type: ignore
                public_methods.add(node.name)  # type: ignore

        logger.debug(f"被测类的public方法（使用javalang）: {public_methods}")
    except Exception as e:
        # 如果javalang解析失败，降级到正则表达式
        logger.debug(f"javalang解析失败，降级到正则表达式: {e}")
        public_method_pattern = r"public\s+\w+(?:<[^>]+>)?\s+(\w+)\s*\("
        public_methods = set(re.findall(public_method_pattern, class_code))
        logger.debug(f"被测类的public方法（使用正则）: {public_methods}")

    # 检查每个测试方法
    for method in methods:
        code = method.code
        method_name = method.method_name

        # 错误模式1: 调用未定义的辅助方法
        # 查找所有方法调用（不包括标准库和测试框架）
        method_calls = re.findall(r"(\w+)\s*\(", code)

        suspicious_calls = []
        for call in method_calls:
            # 跳过标准方法和测试框架方法
            if call in [
                "assertEquals",
                "assertTrue",
                "assertFalse",
                "assertThrows",
                "assertNotNull",
                "assertNull",
                "verify",
                "when",
                "mock",
                "any",
                "anyString",
                "anyInt",
                "anyDouble",
                "eq",
                "println",
                "print",
                "format",
                "valueOf",
                "toString",
                "add",
                "remove",
                "get",
                "set",
                "put",
                "contains",
            ]:
                continue

            # 跳过被测类的public方法
            if call in public_methods:
                continue

            # 跳过构造函数调用（首字母大写）
            if call[0].isupper():
                continue

            # 检查是否是未定义的辅助方法
            # 如果方法名包含特定模式，可能是错误的辅助方法
            if "ByReflection" in call or "Helper" in call:
                suspicious_calls.append(call)
                logger.warning(f"方法 {method_name} 调用了可疑的辅助方法: {call}")

        if suspicious_calls:
            invalid_methods.add(method_name)
            continue

        # 错误模式2: 访问private内部类
        if re.search(r"\b\w+\.Payment\b", code) and "private" in class_code:
            # 检查是否试图访问private内部类
            if "PaymentService.Payment" in code:
                logger.warning(f"方法 {method_name} 试图访问private内部类")
                invalid_methods.add(method_name)
                continue

        # 错误模式3: 调用不存在的getter/setter
        # 提取所有 service.getXxx() 或 service.setXxx() 调用
        getter_setter_calls = re.findall(r"service\.(get|set)(\w+)\s*\(", code)
        for prefix, suffix in getter_setter_calls:
            method_call = f"{prefix}{suffix}"
            if method_call not in public_methods:
                logger.warning(f"方法 {method_name} 调用了不存在的方法: {method_call}")
                invalid_methods.add(method_name)
                break

    return invalid_methods
