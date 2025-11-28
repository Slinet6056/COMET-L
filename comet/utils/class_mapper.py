"""类到文件映射管理器"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ClassInfo:
    """类信息"""
    class_name: str  # 完整类名（包含包名）
    simple_name: str  # 简单类名（不含包名）
    file_path: str  # 源文件路径
    package_name: Optional[str] = None
    is_public: bool = False
    is_interface: bool = False


class ClassMapper:
    """类到文件映射管理器

    维护项目中所有类到源文件的映射关系，支持：
    - 按类名查找源文件
    - 按文件查找包含的类
    - 处理内部类、接口等复杂情况
    """

    def __init__(self):
        """初始化映射器"""
        self._class_to_file: Dict[str, ClassInfo] = {}  # 完整类名 -> ClassInfo
        self._simple_name_to_classes: Dict[str, List[ClassInfo]] = {}  # 简单类名 -> ClassInfo 列表
        self._file_to_classes: Dict[str, List[ClassInfo]] = {}  # 文件路径 -> ClassInfo 列表

    def add_class(
        self,
        class_name: str,
        file_path: str,
        package_name: Optional[str] = None,
        is_public: bool = False,
        is_interface: bool = False,
    ) -> None:
        """
        添加类到映射

        Args:
            class_name: 类名（可以是简单类名或完整类名）
            file_path: 源文件路径
            package_name: 包名
            is_public: 是否为 public 类
            is_interface: 是否为接口
        """
        # 构造完整类名
        if package_name and '.' not in class_name:
            full_class_name = f"{package_name}.{class_name}"
        else:
            full_class_name = class_name

        # 提取简单类名
        simple_name = class_name.split('.')[-1]

        # 规范化文件路径
        normalized_path = str(Path(file_path).resolve())

        class_info = ClassInfo(
            class_name=full_class_name,
            simple_name=simple_name,
            file_path=normalized_path,
            package_name=package_name,
            is_public=is_public,
            is_interface=is_interface,
        )

        # 添加到映射
        self._class_to_file[full_class_name] = class_info

        if simple_name not in self._simple_name_to_classes:
            self._simple_name_to_classes[simple_name] = []
        self._simple_name_to_classes[simple_name].append(class_info)

        if normalized_path not in self._file_to_classes:
            self._file_to_classes[normalized_path] = []
        self._file_to_classes[normalized_path].append(class_info)

        logger.debug(f"添加类映射: {full_class_name} -> {file_path}")

    def get_file_path(self, class_name: str) -> Optional[str]:
        """
        根据类名获取源文件路径

        Args:
            class_name: 类名（可以是简单类名或完整类名），可能包含内部类标记 ($)

        Returns:
            文件路径，如果找不到则返回 None
        """
        # 处理内部类：如果类名包含 $，则提取外部类名
        # 例如：ShippingService$ShippingInfo -> ShippingService
        # 例如：com.example.ShippingService$ShippingInfo -> com.example.ShippingService
        original_class_name = class_name
        if '$' in class_name:
            # 提取外部类名（保留包名部分）
            class_name = class_name.split('$')[0]
            logger.debug(f"检测到内部类 {original_class_name}，使用外部类 {class_name} 查找文件")

        # 先尝试完整类名
        if class_name in self._class_to_file:
            return self._class_to_file[class_name].file_path

        # 再尝试简单类名
        simple_name = class_name.split('.')[-1]
        if simple_name in self._simple_name_to_classes:
            classes = self._simple_name_to_classes[simple_name]
            if len(classes) == 1:
                return classes[0].file_path
            elif len(classes) > 1:
                logger.warning(
                    f"类名 {simple_name} 有多个匹配: "
                    f"{', '.join(c.class_name for c in classes)}"
                )
                # 优先返回 public 类
                public_classes = [c for c in classes if c.is_public]
                if public_classes:
                    return public_classes[0].file_path
                return classes[0].file_path

        return None

    def get_class_info(self, class_name: str) -> Optional[ClassInfo]:
        """
        根据类名获取类信息

        Args:
            class_name: 类名，可能包含内部类标记 ($)

        Returns:
            ClassInfo 对象，如果找不到则返回 None
        """
        # 处理内部类：如果类名包含 $，则提取外部类名
        original_class_name = class_name
        if '$' in class_name:
            class_name = class_name.split('$')[0]
            logger.debug(f"检测到内部类 {original_class_name}，使用外部类 {class_name} 查找信息")

        if class_name in self._class_to_file:
            return self._class_to_file[class_name]

        # 尝试简单类名
        simple_name = class_name.split('.')[-1]
        if simple_name in self._simple_name_to_classes:
            classes = self._simple_name_to_classes[simple_name]
            if len(classes) == 1:
                return classes[0]
            # 多个匹配，优先返回 public 类
            public_classes = [c for c in classes if c.is_public]
            if public_classes:
                return public_classes[0]
            return classes[0]

        return None

    def get_classes_in_file(self, file_path: str) -> List[ClassInfo]:
        """
        获取指定文件中包含的所有类

        Args:
            file_path: 文件路径

        Returns:
            ClassInfo 列表
        """
        normalized_path = str(Path(file_path).resolve())
        return self._file_to_classes.get(normalized_path, [])

    def get_all_classes(self) -> List[ClassInfo]:
        """获取所有类信息"""
        return list(self._class_to_file.values())

    def get_all_files(self) -> Set[str]:
        """获取所有源文件路径"""
        return set(self._file_to_classes.keys())

    def clear(self) -> None:
        """清空所有映射"""
        self._class_to_file.clear()
        self._simple_name_to_classes.clear()
        self._file_to_classes.clear()
        logger.info("已清空类映射")

    def get_statistics(self) -> Dict[str, int]:
        """获取统计信息"""
        return {
            "total_classes": len(self._class_to_file),
            "total_files": len(self._file_to_classes),
            "public_classes": sum(1 for c in self._class_to_file.values() if c.is_public),
            "interfaces": sum(1 for c in self._class_to_file.values() if c.is_interface),
        }

    def __len__(self) -> int:
        """返回类的总数"""
        return len(self._class_to_file)

    def __contains__(self, class_name: str) -> bool:
        """检查类名是否存在（支持内部类）"""
        # 处理内部类
        if '$' in class_name:
            class_name = class_name.split('$')[0]

        if class_name in self._class_to_file:
            return True
        simple_name = class_name.split('.')[-1]
        return simple_name in self._simple_name_to_classes

    def __repr__(self) -> str:
        stats = self.get_statistics()
        return (
            f"ClassMapper(classes={stats['total_classes']}, "
            f"files={stats['total_files']}, "
            f"public={stats['public_classes']}, "
            f"interfaces={stats['interfaces']})"
        )
