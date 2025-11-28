"""项目扫描器 - 扫描 Java 项目并建立类到文件的映射"""

import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class ProjectScanner:
    """项目扫描器 - 扫描 Java 项目中的所有类"""

    def __init__(self, java_executor, db):
        """
        初始化项目扫描器

        Args:
            java_executor: Java 执行器，用于解析 Java 文件
            db: 数据库，用于存储类映射
        """
        self.java_executor = java_executor
        self.db = db

    def scan_project(self, project_path: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        扫描项目中的所有 Java 源文件，建立类到文件的映射

        Args:
            project_path: 项目路径
            use_cache: 是否使用数据库中已有的缓存

        Returns:
            扫描统计信息
        """
        from .project_utils import find_java_files

        project = Path(project_path)

        # 如果使用缓存，先检查是否已经扫描过
        if use_cache:
            existing_mappings = self.db.get_all_class_mappings()
            if existing_mappings:
                logger.info(f"使用已缓存的类映射 ({len(existing_mappings)} 个类)")
                return {
                    "total_files": 0,
                    "total_classes": len(existing_mappings),
                    "cached": True,
                }

        # 清空旧映射
        self.db.clear_class_mappings()

        # 查找所有 Java 源文件
        logger.info(f"扫描项目: {project_path}")
        java_files = find_java_files(str(project), pattern="**/*.java")

        # 过滤掉测试文件
        source_files = [
            f for f in java_files
            if "/test/" not in str(f) and "\\test\\" not in str(f)
        ]

        logger.info(f"找到 {len(source_files)} 个源文件")

        total_classes = 0
        scanned_files = 0
        failed_files = []

        for file_path in source_files:
            try:
                classes = self._scan_file(str(file_path))
                if classes:
                    total_classes += len(classes)
                    scanned_files += 1
                    logger.debug(f"扫描文件: {file_path} ({len(classes)} 个类)")
            except Exception as e:
                logger.warning(f"扫描文件失败 {file_path}: {e}")
                failed_files.append(str(file_path))

        logger.info(
            f"扫描完成: {scanned_files}/{len(source_files)} 个文件, "
            f"{total_classes} 个类"
        )

        if failed_files:
            logger.warning(f"失败的文件数: {len(failed_files)}")

        return {
            "total_files": scanned_files,
            "total_classes": total_classes,
            "failed_files": len(failed_files),
            "cached": False,
        }

    def _scan_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        扫描单个 Java 文件，提取所有类信息

        Args:
            file_path: Java 文件路径

        Returns:
            类信息列表
        """
        # 使用 JavaExecutor 解析文件
        analysis_result = self.java_executor.analyze_code(file_path)

        if not analysis_result:
            return []

        package_name = analysis_result.get("package", "")
        classes_data = analysis_result.get("classes", [])

        classes = []
        for class_data in classes_data:
            class_name = class_data.get("name", "")
            is_interface = class_data.get("isInterface", False)
            is_public = class_data.get("isPublic", False)

            # 构造完整类名
            if package_name:
                full_class_name = f"{package_name}.{class_name}"
            else:
                full_class_name = class_name

            # 保存到数据库
            self.db.save_class_mapping(
                class_name=full_class_name,
                simple_name=class_name,
                file_path=file_path,
                package_name=package_name,
                is_public=is_public,
                is_interface=is_interface,
            )

            classes.append({
                "class_name": full_class_name,
                "simple_name": class_name,
                "package": package_name,
                "is_public": is_public,
                "is_interface": is_interface,
            })

        return classes

    def get_file_for_class(self, class_name: str) -> Optional[str]:
        """
        根据类名获取源文件路径

        Args:
            class_name: 类名（可以是简单类名或完整类名）

        Returns:
            文件路径，如果找不到则返回 None
        """
        return self.db.get_class_file_path(class_name)

    def rescan_file(self, file_path: str) -> int:
        """
        重新扫描单个文件并更新映射

        Args:
            file_path: 文件路径

        Returns:
            扫描到的类数量
        """
        try:
            # 删除该文件的旧映射
            # 注意：这需要数据库支持按文件删除，我们暂时不实现这个

            # 重新扫描
            classes = self._scan_file(file_path)
            logger.info(f"重新扫描文件: {file_path} ({len(classes)} 个类)")
            return len(classes)
        except Exception as e:
            logger.error(f"重新扫描文件失败 {file_path}: {e}")
            return 0
