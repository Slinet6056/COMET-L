"""项目工具函数 - 扫描和查找 Java 文件"""

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def find_java_files(project_path: str, pattern: str = "**/*.java") -> List[Path]:
    """
    查找 Java 文件

    Args:
        project_path: 项目路径
        pattern: glob 模式

    Returns:
        Java 文件路径列表
    """
    project = Path(project_path)
    if not project.exists():
        logger.error(f"项目路径不存在: {project_path}")
        return []

    # 排除测试文件
    files = []
    for file_path in project.glob(pattern):
        # 跳过测试目录
        if "/test/" in str(file_path) or "\\test\\" in str(file_path):
            continue
        files.append(file_path)

    return files


def find_java_file(project_path: str, class_name: str) -> Optional[Path]:
    """
    根据类名查找 Java 文件

    Args:
        project_path: 项目路径
        class_name: 类名（不含包名）

    Returns:
        文件路径，如果找不到则返回 None
    """
    # 预期文件名
    expected_filename = f"{class_name}.java"

    # 先尝试在 src/main/java 中查找
    source_root = get_source_root(project_path)
    if source_root:
        for file_path in source_root.rglob(expected_filename):
            logger.info(f"找到文件: {file_path}")
            return file_path

    # 如果没找到，尝试在整个项目中查找
    project = Path(project_path)
    for file_path in project.rglob(expected_filename):
        # 跳过测试文件
        if "/test/" in str(file_path) or "\\test\\" in str(file_path):
            continue
        logger.info(f"找到文件: {file_path}")
        return file_path

    logger.warning(f"未找到类文件: {class_name}")
    return None


def get_source_root(project_path: str) -> Optional[Path]:
    """
    获取源代码根目录 (src/main/java)

    Args:
        project_path: 项目路径

    Returns:
        源代码根路径，如果不存在则返回 None
    """
    project = Path(project_path)
    source_root = project / "src" / "main" / "java"

    if source_root.exists():
        return source_root

    # 尝试其他常见结构
    alt_root = project / "src"
    if alt_root.exists():
        return alt_root

    return None


def get_test_root(project_path: str) -> Optional[Path]:
    """
    获取测试代码根目录 (src/test/java)

    Args:
        project_path: 项目路径

    Returns:
        测试代码根路径，如果不存在则返回 None
    """
    project = Path(project_path)
    test_root = project / "src" / "test" / "java"

    if test_root.exists():
        return test_root

    # 如果不存在，创建它
    test_root.mkdir(parents=True, exist_ok=True)
    return test_root


def get_all_java_classes(project_path: str) -> List[str]:
    """
    获取项目中所有 Java 类的类名

    Args:
        project_path: 项目路径

    Returns:
        类名列表
    """
    java_files = find_java_files(project_path)
    class_names = []

    for file_path in java_files:
        # 提取类名（文件名去掉 .java 后缀）
        class_name = file_path.stem
        class_names.append(class_name)

    return class_names


def write_test_file(project_path: str, package_name: str, test_code: str, test_class_name: str, merge: bool = True) -> Optional[Path]:
    """
    将测试代码写入文件（支持追加模式）

    Args:
        project_path: 项目路径
        package_name: 包名
        test_code: 测试代码
        test_class_name: 测试类名
        merge: 是否合并已有测试（默认True）

    Returns:
        写入的文件路径，如果失败则返回 None
    """
    test_root = get_test_root(project_path)
    if not test_root:
        logger.error("无法创建测试目录")
        return None

    # 根据包名创建目录结构
    if package_name:
        package_dir = test_root / package_name.replace(".", "/")
        package_dir.mkdir(parents=True, exist_ok=True)
    else:
        package_dir = test_root

    # 写入文件
    test_file = package_dir / f"{test_class_name}.java"
    try:
        # 如果文件存在且需要合并，则提取现有测试方法
        if merge and test_file.exists():
            existing_code = test_file.read_text(encoding='utf-8')
            merged_code = _merge_test_methods(existing_code, test_code)
            test_file.write_text(merged_code, encoding='utf-8')
            logger.info(f"测试文件已更新（合并模式）: {test_file}")
        else:
            test_file.write_text(test_code, encoding='utf-8')
            logger.info(f"测试文件已写入: {test_file}")
        return test_file
    except Exception as e:
        logger.error(f"写入测试文件失败: {e}")
        return None


def _merge_test_methods(existing_code: str, new_code: str) -> str:
    """
    合并测试方法（避免覆盖已有测试）

    Args:
        existing_code: 现有测试代码
        new_code: 新生成的测试代码

    Returns:
        合并后的测试代码
    """
    import re

    # 提取现有测试方法名
    existing_methods = set(re.findall(r'@Test\s+(?:public\s+)?void\s+(\w+)\s*\(', existing_code))
    logger.debug(f"现有测试方法: {existing_methods}")

    # 从新代码中提取测试方法（包括完整的方法体）
    # 使用简单的方法：按行解析，找到@Test注解开始到对应的闭合大括号
    new_test_methods = _extract_test_methods(new_code)

    # 只保留不重复的测试方法
    methods_to_add = []
    for method_name, method_code in new_test_methods.items():
        if method_name not in existing_methods:
            methods_to_add.append(method_code)
            logger.debug(f"添加新测试方法: {method_name}")
        else:
            logger.debug(f"跳过重复测试方法: {method_name}")

    if not methods_to_add:
        logger.info("没有新测试方法需要添加")
        return existing_code

    # 在类结束前插入新方法
    last_brace_pos = existing_code.rfind('}')
    if last_brace_pos == -1:
        logger.error("无法找到类结束标记")
        return existing_code

    # 插入新方法
    before_brace = existing_code[:last_brace_pos].rstrip()
    new_methods_str = '\n\n    '.join(methods_to_add)
    merged = f"{before_brace}\n\n    {new_methods_str}\n}}"

    logger.info(f"成功合并 {len(methods_to_add)} 个新测试方法")
    return merged


def _extract_test_methods(code: str) -> dict:
    """
    从代码中提取所有@Test测试方法

    Args:
        code: Java测试代码

    Returns:
        {method_name: method_code} 字典
    """
    import re
    methods = {}
    lines = code.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # 找到@Test注解
        if line.startswith('@Test'):
            # 下一行应该是方法签名
            method_start = i
            i += 1
            if i >= len(lines):
                break

            # 提取方法名
            match = re.search(r'void\s+(\w+)\s*\(', lines[i])
            if not match:
                continue
            method_name = match.group(1)

            # 找到方法体的开始
            brace_count = 0
            method_lines = [lines[method_start], lines[i]]
            i += 1

            # 找到匹配的闭合大括号
            while i < len(lines):
                line = lines[i]
                method_lines.append(line)

                # 计算大括号
                brace_count += line.count('{') - line.count('}')

                if brace_count == 0 and '}' in line:
                    # 方法结束
                    methods[method_name] = '\n'.join(method_lines)
                    break

                i += 1

        i += 1

    return methods


def is_maven_project(project_path: str) -> bool:
    """
    检查是否为 Maven 项目

    Args:
        project_path: 项目路径

    Returns:
        是否为 Maven 项目
    """
    pom_file = Path(project_path) / "pom.xml"
    return pom_file.exists()
