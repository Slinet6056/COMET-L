"""沙箱管理器"""

import os
import shutil
import logging
from pathlib import Path
from typing import Optional
import uuid

logger = logging.getLogger(__name__)


class SandboxManager:
    """沙箱管理器 - 为测试和变异创建隔离的执行环境"""

    def __init__(self, sandbox_root: str = "./sandbox"):
        """
        初始化沙箱管理器

        Args:
            sandbox_root: 沙箱根目录
        """
        self.sandbox_root = Path(sandbox_root)
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

    def create_sandbox(self, project_path: str, sandbox_id: Optional[str] = None) -> str:
        """
        创建新的沙箱环境

        Args:
            project_path: 源项目路径
            sandbox_id: 沙箱 ID（如果为 None 则自动生成）

        Returns:
            沙箱路径
        """
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

        logger.info(f"创建沙箱: {sandbox_path}")
        return str(sandbox_path)

    def get_sandbox_path(self, sandbox_id: str) -> str:
        """获取沙箱路径"""
        return str(self.sandbox_root / sandbox_id)

    def cleanup_sandbox(self, sandbox_id: str) -> None:
        """清理沙箱"""
        sandbox_path = self.sandbox_root / sandbox_id
        if sandbox_path.exists():
            shutil.rmtree(sandbox_path)
            logger.info(f"清理沙箱: {sandbox_path}")

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
        return self.create_sandbox(project_path, sandbox_id)
