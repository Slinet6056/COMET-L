"""Bug 报告解析器模块 - 简化版

支持任意格式的文本文件，完全依赖语义搜索进行相关性匹配。
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class BugReport:
    """Bug 报告模型 - 简化版

    不再解析结构化字段，保留原始文本内容，让语义搜索处理相关性。
    """

    id: str
    title: str  # 从文件名或首行提取
    file_path: str
    content: str  # 原始文本内容
    file_type: str  # md, txt, diff, patch 等
    metadata: Dict[str, Any] = field(default_factory=dict)  # 可选的 front-matter

    def to_text(self) -> str:
        """
        将 Bug 报告转换为适合 embedding 的文本格式

        直接返回标题和原始内容，让语义搜索处理相关性。

        Returns:
            格式化的文本
        """
        parts = [f"# {self.title}"]
        if self.content:
            parts.append(self.content)
        return "\n\n".join(parts)


class BugReportParser:
    """Bug 报告解析器 - 支持任意格式的文本文件

    支持的文件类型：
    - .md: Markdown 文件（支持可选的 front-matter）
    - .txt: 纯文本文件
    - .diff / .patch: Diff/Patch 文件
    """

    SUPPORTED_EXTENSIONS = {".md", ".txt", ".diff", ".patch"}

    def __init__(self):
        """初始化解析器"""
        self._id_counter = 0

    def _generate_id(self, file_path: str) -> str:
        """生成 Bug 报告 ID"""
        self._id_counter += 1
        file_name = Path(file_path).stem
        return f"bug_{file_name}_{self._id_counter}"

    def _extract_title(
        self, content: str, file_path: str, metadata: Dict[str, Any]
    ) -> str:
        """
        提取标题

        优先级：front-matter title > 首行 # 标题 > 文件名

        Args:
            content: 文件内容
            file_path: 文件路径
            metadata: front-matter 元数据

        Returns:
            标题字符串
        """
        # 1. 优先使用 front-matter 中的 title
        if metadata.get("title"):
            return metadata["title"]

        # 2. 尝试从内容中提取 # 标题
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()

        # 3. 使用文件名作为标题
        return Path(file_path).stem.replace("-", " ").replace("_", " ").title()

    def _parse_frontmatter(self, lines: List[str]) -> Dict[str, Any]:
        """
        解析 YAML front-matter

        Args:
            lines: front-matter 行列表（不含 --- 分隔符）

        Returns:
            元数据字典
        """
        metadata = {}
        current_key = None
        current_list = None

        for line in lines:
            if not line.strip():
                continue

            # 检查是否是列表项
            if line.startswith("  - ") or line.startswith("- "):
                if current_key and current_list is not None:
                    item = line.lstrip("- ").strip()
                    current_list.append(item)
                continue

            # 解析键值对
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()

                if value:
                    # 移除引号
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    metadata[key] = value
                    current_key = None
                    current_list = None
                else:
                    # 可能是列表的开始
                    current_key = key
                    current_list = []
                    metadata[key] = current_list

        return metadata

    def parse_file(self, file_path: str) -> Optional[BugReport]:
        """
        解析单个文件

        支持 .md, .txt, .diff, .patch 格式

        Args:
            file_path: 文件路径

        Returns:
            BugReport 对象，解析失败返回 None
        """
        path = Path(file_path)

        # 检查文件是否存在
        if not path.exists():
            logger.warning(f"文件不存在: {file_path}")
            return None

        # 检查文件类型
        suffix = path.suffix.lower()
        if suffix not in self.SUPPORTED_EXTENSIONS:
            logger.debug(f"不支持的文件类型: {suffix}, 跳过 {file_path}")
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_content = f.read()

            # 解析 front-matter（仅 .md 文件）
            metadata = {}
            content = raw_content

            if suffix == ".md":
                lines = raw_content.strip().split("\n")
                if lines and lines[0].strip() == "---":
                    for i, line in enumerate(lines[1:], 1):
                        if line.strip() == "---":
                            metadata = self._parse_frontmatter(lines[1:i])
                            # 内容从 front-matter 之后开始
                            content = "\n".join(lines[i + 1 :])
                            break

            # 提取标题
            title = self._extract_title(content, str(path), metadata)

            return BugReport(
                id=self._generate_id(file_path),
                title=title,
                file_path=str(path),
                content=content.strip(),
                file_type=suffix.lstrip("."),
                metadata=metadata,
            )

        except Exception as e:
            logger.warning(f"解析 Bug 报告失败 {file_path}: {e}")
            return None

    def parse_directory(self, directory: str) -> List[BugReport]:
        """
        解析目录下的所有支持的文件

        Args:
            directory: 目录路径

        Returns:
            BugReport 列表
        """
        dir_path = Path(directory)
        if not dir_path.exists() or not dir_path.is_dir():
            logger.warning(f"Bug 报告目录不存在: {directory}")
            return []

        reports = []

        # 遍历所有支持的文件类型
        for ext in self.SUPPORTED_EXTENSIONS:
            pattern = f"**/*{ext}"
            for file_path in dir_path.glob(pattern):
                report = self.parse_file(str(file_path))
                if report:
                    reports.append(report)

        logger.info(f"从 {directory} 解析了 {len(reports)} 个 Bug 报告")
        return reports


def load_bug_reports(directory: Optional[str]) -> List[BugReport]:
    """
    加载 Bug 报告的便捷函数

    Args:
        directory: Bug 报告目录

    Returns:
        BugReport 列表
    """
    if not directory:
        return []

    parser = BugReportParser()
    return parser.parse_directory(directory)
