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
        logger.warning(f"项目路径不存在: {project_path}")
        return []

    # 排除测试文件
    files = []
    for file_path in project.glob(pattern):
        # 跳过测试目录
        if "/test/" in str(file_path) or "\\test\\" in str(file_path):
            continue
        files.append(file_path)

    return files


def find_java_file(project_path: str, class_name: str, db=None) -> Optional[Path]:
    """
    根据类名查找 Java 文件

    Args:
        project_path: 项目路径
        class_name: 类名（不含包名），可能包含内部类标记 ($)
        db: 数据库对象（可选），如果提供则优先从数据库查找

    Returns:
        文件路径，如果找不到则返回 None
    """
    # 优先从数据库查找类文件映射（支持同一文件中的多个类）
    if db is not None:
        try:
            file_path_str = db.get_class_file_path(class_name)
            if file_path_str:
                file_path = Path(file_path_str)
                if file_path.exists():
                    logger.debug(f"从数据库找到类文件: {class_name} -> {file_path}")
                    return file_path
        except Exception as e:
            logger.debug(f"从数据库查找类文件失败: {e}")

    # 处理内部类：如果类名包含 $，则提取外部类名
    # 例如：ShippingService$ShippingInfo -> ShippingService
    if "$" in class_name:
        outer_class = class_name.split("$")[0]
        logger.debug(f"检测到内部类 {class_name}，使用外部类 {outer_class} 查找文件")
        class_name = outer_class

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


def clear_test_directory(project_path: str) -> bool:
    """
    清空测试目录中的所有测试文件

    Args:
        project_path: 项目路径

    Returns:
        是否成功清空
    """
    import shutil

    test_root = Path(project_path) / "src" / "test" / "java"

    if not test_root.exists():
        logger.info("测试目录不存在，无需清空")
        return True

    try:
        # 删除测试目录下的所有内容
        for item in test_root.iterdir():
            if item.is_file():
                item.unlink()
                logger.debug(f"删除测试文件: {item}")
            elif item.is_dir():
                shutil.rmtree(item)
                logger.debug(f"删除测试目录: {item}")

        logger.info(f"已清空测试目录: {test_root}")
        return True
    except Exception as e:
        logger.warning(f"清空测试目录失败: {e}")
        return False


def get_all_java_classes(project_path: str, db=None) -> List[str]:
    """
    获取项目中所有 Java 类的类名

    Args:
        project_path: 项目路径
        db: 数据库对象（可选），如果提供则从数据库中获取所有类名

    Returns:
        类名列表（简单类名，不含包名）
    """
    # 如果提供了数据库，优先从数据库获取所有类名（包括同一文件中的多个类）
    if db is not None:
        try:
            mappings = db.get_all_class_mappings()
            if mappings:
                # 返回简单类名列表（去重）
                class_names = list(set(m["simple_name"] for m in mappings))
                logger.debug(f"从数据库获取到 {len(class_names)} 个类名")
                return class_names
        except Exception as e:
            logger.warning(f"从数据库获取类名失败: {e}，回退到文件扫描")

    # 回退到基于文件名的方式（兼容旧逻辑）
    java_files = find_java_files(project_path)
    class_names = []

    for file_path in java_files:
        # 提取类名（文件名去掉 .java 后缀）
        class_name = file_path.stem
        class_names.append(class_name)

    return class_names


def write_test_file(
    project_path: str,
    package_name: str,
    test_code: str,
    test_class_name: str,
    format_code: bool = True,
    formatting_enabled: Optional[bool] = None,
    formatting_style: Optional[str] = None,
) -> Optional[Path]:
    """
    将测试代码写入文件（直接覆盖）

    Args:
        project_path: 项目路径
        package_name: 包名
        test_code: 测试代码
        test_class_name: 测试类名
        format_code: 是否格式化代码（默认 True，已废弃，请使用 formatting_enabled）
        formatting_enabled: 是否启用格式化（来自配置，优先于 format_code）
        formatting_style: 格式化风格 (GOOGLE 或 AOSP)

    Returns:
        写入的文件路径，如果失败则返回 None
    """
    test_root = get_test_root(project_path)
    if not test_root:
        logger.warning("无法创建测试目录")
        return None

    if package_name:
        package_dir = test_root / package_name.replace(".", "/")
        package_dir.mkdir(parents=True, exist_ok=True)
    else:
        package_dir = test_root

    test_file = package_dir / f"{test_class_name}.java"
    try:
        test_file.write_text(test_code, encoding="utf-8")
        logger.info(f"测试文件已写入: {test_file}")

        # 确定是否格式化：优先使用 formatting_enabled 配置，否则使用 format_code 参数
        should_format = formatting_enabled if formatting_enabled is not None else format_code

        if should_format:
            from .java_formatter import format_java_file

            style = formatting_style or "GOOGLE"
            if format_java_file(str(test_file), style=style):
                logger.debug(f"测试文件已格式化 (style={style}): {test_file}")
            else:
                logger.debug(f"格式化跳过或失败: {test_file}")

        return test_file
    except Exception as e:
        logger.warning(f"写入测试文件失败: {e}")
        return None


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
