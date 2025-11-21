"""代码处理工具函数 - 轻量级文本处理

复杂的 Java 代码解析（提取方法、签名等）使用 JavaExecutor
"""

import re
from typing import List, Dict, Optional


def extract_imports(java_code: str) -> List[str]:
    """
    从 Java 代码中提取 import 语句

    Args:
        java_code: Java 源代码

    Returns:
        import 语句列表
    """
    import_pattern = r'^\s*import\s+[^;]+;'
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
    package_match = re.search(r'^\s*package\s+([^;]+);', java_code, re.MULTILINE)
    if package_match:
        result["package"] = package_match.group(1).strip()

    # 提取类名
    class_match = re.search(
        r'^\s*(public\s+)?(class|interface|enum)\s+(\w+)',
        java_code,
        re.MULTILINE
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
    lines = code.split('\n')
    numbered_lines = [f"{i + start:4d} | {line}" for i, line in enumerate(lines)]
    return '\n'.join(numbered_lines)


def extract_class_from_file(file_path: str) -> str:
    """
    从文件中提取 Java 类代码

    Args:
        file_path: 文件路径

    Returns:
        类代码
    """
    with open(file_path, 'r', encoding='utf-8') as f:
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
        "import org.junit.jupiter.api.Test;",
        "import org.junit.jupiter.api.BeforeEach;",
        "import org.junit.jupiter.api.AfterEach;",
        "import static org.junit.jupiter.api.Assertions.*;",
    ]

    all_imports = default_imports + [imp for imp in imports if imp not in default_imports]
    lines.extend(all_imports)
    lines.append("")

    # 类声明
    lines.append(f"public class {test_class_name} {{")
    lines.append("")

    # 被测对象
    lines.append(f"    private {target_class} target;")
    lines.append("")

    # setUp 方法
    lines.append("    @BeforeEach")
    lines.append("    public void setUp() {")
    lines.append(f"        target = new {target_class}();")
    lines.append("    }")
    lines.append("")

    # 测试方法
    for method_code in test_methods:
        # 缩进
        indented = '\n'.join('    ' + line for line in method_code.split('\n'))
        lines.append(indented)
        lines.append("")

    # 类结束
    lines.append("}")

    return '\n'.join(lines)
