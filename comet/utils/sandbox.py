"""沙箱管理器"""

import os
import shutil
import logging
import threading
from pathlib import Path
from typing import Optional, Dict
import uuid
import time

logger = logging.getLogger(__name__)


class SandboxManager:
    """沙箱管理器 - 为测试和变异创建隔离的执行环境

    支持：
    - 全局工作空间沙箱（用于整个运行期间）
    - 目标级独立沙箱（每个目标方法/类有自己的沙箱）
    - 线程安全的沙箱创建和清理
    """

    def __init__(self, sandbox_root: str = "./sandbox"):
        """
        初始化沙箱管理器

        Args:
            sandbox_root: 沙箱根目录
        """
        self.sandbox_root = Path(sandbox_root)
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # 线程安全锁
        self._sandboxes: Dict[str, str] = {}  # sandbox_id -> sandbox_path
        self._project_path: Optional[str] = None  # 源项目路径（用于创建新沙箱）

    def create_sandbox(self, project_path: str, sandbox_id: Optional[str] = None) -> str:
        """
        创建新的沙箱环境（线程安全）

        Args:
            project_path: 源项目路径
            sandbox_id: 沙箱 ID（如果为 None 则自动生成）

        Returns:
            沙箱路径
        """
        with self._lock:
            if sandbox_id is None:
                sandbox_id = str(uuid.uuid4())[:8]

            sandbox_path = self.sandbox_root / sandbox_id

            if sandbox_path.exists():
                logger.warning(f"沙箱已存在，将被清空: {sandbox_path}")
                shutil.rmtree(sandbox_path)

            # 复制项目到沙箱
            shutil.copytree(
                project_path,
                sandbox_path,
                ignore=shutil.ignore_patterns(
                    'target', '*.class', '.git', '.idea', '__pycache__',
                    '*.pyc', 'node_modules'
                )
            )

            # 记录沙箱
            self._sandboxes[sandbox_id] = str(sandbox_path)

            logger.info(f"创建沙箱: {sandbox_path} (ID: {sandbox_id})")
            return str(sandbox_path)

    def get_sandbox_path(self, sandbox_id: str) -> str:
        """获取沙箱路径"""
        return str(self.sandbox_root / sandbox_id)

    def create_target_sandbox(self, project_path: str, class_name: str,
                              method_name: Optional[str] = None) -> str:
        """
        为特定目标创建独立沙箱（线程安全）

        Args:
            project_path: 源项目路径
            class_name: 类名
            method_name: 方法名（可选）

        Returns:
            沙箱路径
        """
        # 构造沙箱 ID
        timestamp = int(time.time() * 1000)  # 毫秒级时间戳
        thread_id = threading.get_ident()

        if method_name:
            sandbox_id = f"{class_name}_{method_name}_{timestamp}_{thread_id}"
        else:
            sandbox_id = f"{class_name}_{timestamp}_{thread_id}"

        # 清理类名中的路径分隔符
        sandbox_id = sandbox_id.replace('.', '_').replace('/', '_').replace('\\', '_')

        return self.create_sandbox(project_path, sandbox_id)

    def cleanup_sandbox(self, sandbox_id: str) -> None:
        """清理沙箱（线程安全）"""
        with self._lock:
            sandbox_path = self.sandbox_root / sandbox_id
            if sandbox_path.exists():
                try:
                    shutil.rmtree(sandbox_path)
                    logger.info(f"清理沙箱: {sandbox_path}")
                except Exception as e:
                    logger.error(f"清理沙箱失败 {sandbox_path}: {e}")
                finally:
                    # 从记录中移除
                    self._sandboxes.pop(sandbox_id, None)

    def cleanup_all(self) -> None:
        """清理所有沙箱"""
        if self.sandbox_root.exists():
            shutil.rmtree(self.sandbox_root)
            self.sandbox_root.mkdir(parents=True, exist_ok=True)
            logger.info("清理所有沙箱")

    def copy_file_to_sandbox(self, sandbox_id: str, source_file: str, target_rel_path: str) -> str:
        """
        复制文件到沙箱

        Args:
            sandbox_id: 沙箱 ID
            source_file: 源文件路径
            target_rel_path: 目标相对路径

        Returns:
            目标文件完整路径
        """
        sandbox_path = self.sandbox_root / sandbox_id
        target_path = sandbox_path / target_rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(source_file, target_path)
        return str(target_path)

    def get_file_from_sandbox(self, sandbox_id: str, rel_path: str) -> Optional[str]:
        """
        从沙箱获取文件内容

        Args:
            sandbox_id: 沙箱 ID
            rel_path: 相对路径

        Returns:
            文件内容，如果文件不存在则返回 None
        """
        sandbox_path = self.sandbox_root / sandbox_id
        file_path = sandbox_path / rel_path

        if not file_path.exists():
            return None

        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    def list_sandboxes(self) -> list[str]:
        """列出所有沙箱"""
        if not self.sandbox_root.exists():
            return []
        return [d.name for d in self.sandbox_root.iterdir() if d.is_dir()]

    def export_test_files(self, sandbox_id: str, original_project_path: str) -> None:
        """
        将沙箱中的测试文件导出到原项目

        Args:
            sandbox_id: 沙箱 ID
            original_project_path: 原项目路径
        """
        sandbox_path = self.sandbox_root / sandbox_id
        if not sandbox_path.exists():
            logger.error(f"沙箱不存在: {sandbox_id}")
            return

        # 获取沙箱中的测试目录
        sandbox_test_root = sandbox_path / "src" / "test" / "java"
        if not sandbox_test_root.exists():
            logger.warning(f"沙箱中没有测试目录: {sandbox_test_root}")
            return

        # 获取原项目的测试目录
        original_test_root = Path(original_project_path) / "src" / "test" / "java"
        original_test_root.mkdir(parents=True, exist_ok=True)

        # 复制所有测试文件
        copied_count = 0
        for test_file in sandbox_test_root.rglob("*Test.java"):
            # 计算相对路径
            rel_path = test_file.relative_to(sandbox_test_root)
            target_file = original_test_root / rel_path

            # 创建目标目录
            target_file.parent.mkdir(parents=True, exist_ok=True)

            # 复制文件
            shutil.copy2(test_file, target_file)
            copied_count += 1
            logger.info(f"导出测试文件: {rel_path}")

        logger.info(f"成功导出 {copied_count} 个测试文件到原项目")

    def create_workspace_sandbox(self, project_path: str) -> str:
        """
        创建工作空间沙箱（用于整个运行期间）

        Args:
            project_path: 源项目路径

        Returns:
            沙箱路径
        """
        sandbox_id = "workspace"
        self._project_path = project_path  # 保存项目路径，用于后续创建独立沙箱
        return self.create_sandbox(project_path, sandbox_id)

    def get_context(self, class_name: str, method_name: Optional[str] = None):
        """
        获取沙箱上下文管理器

        Args:
            class_name: 类名
            method_name: 方法名（可选）

        Returns:
            SandboxContext 对象
        """
        return SandboxContext(self, class_name, method_name)

    def set_project_path(self, project_path: str) -> None:
        """
        设置源项目路径

        Args:
            project_path: 源项目路径
        """
        self._project_path = project_path

    def get_active_sandboxes(self) -> Dict[str, str]:
        """
        获取所有活跃的沙箱

        Returns:
            sandbox_id -> sandbox_path 的字典
        """
        with self._lock:
            return self._sandboxes.copy()

    def cleanup_target_sandboxes(self) -> None:
        """清理所有目标沙箱（保留 workspace 沙箱）"""
        with self._lock:
            target_sandboxes = [
                sandbox_id for sandbox_id in self._sandboxes.keys()
                if sandbox_id != "workspace"
            ]

            for sandbox_id in target_sandboxes:
                self.cleanup_sandbox(sandbox_id)

            logger.info(f"清理了 {len(target_sandboxes)} 个目标沙箱")


class SandboxContext:
    """沙箱上下文管理器 - 自动管理沙箱生命周期

    使用方式：
        with sandbox_manager.get_context("MyClass", "myMethod") as sandbox_path:
            # 在这个沙箱中执行操作
            ...
        # 退出时自动清理沙箱
    """

    def __init__(self, manager: SandboxManager, class_name: str,
                 method_name: Optional[str] = None):
        """
        初始化沙箱上下文

        Args:
            manager: 沙箱管理器
            class_name: 类名
            method_name: 方法名（可选）
        """
        self.manager = manager
        self.class_name = class_name
        self.method_name = method_name
        self.sandbox_path: Optional[str] = None
        self.sandbox_id: Optional[str] = None

    def __enter__(self) -> str:
        """进入上下文，创建沙箱"""
        if not self.manager._project_path:
            raise ValueError("必须先设置项目路径（调用 set_project_path 或 create_workspace_sandbox）")

        self.sandbox_path = self.manager.create_target_sandbox(
            self.manager._project_path,
            self.class_name,
            self.method_name
        )

        # 从路径中提取 sandbox_id
        self.sandbox_id = Path(self.sandbox_path).name

        logger.debug(f"进入沙箱上下文: {self.sandbox_id}")
        return self.sandbox_path

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文，清理沙箱"""
        if self.sandbox_id:
            logger.debug(f"退出沙箱上下文: {self.sandbox_id}")
            self.manager.cleanup_sandbox(self.sandbox_id)

        return False  # 不抑制异常
