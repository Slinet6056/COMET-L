"""文本分块策略模块"""

import logging
import re
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# 尝试导入 tiktoken，如果失败则使用简单的字符计数
try:
    import tiktoken

    _tokenizer = tiktoken.get_encoding("cl100k_base")
    HAS_TIKTOKEN = True
except ImportError:
    _tokenizer = None
    HAS_TIKTOKEN = False
    logger.warning("tiktoken 未安装，将使用字符计数代替 token 计数")


def count_tokens(text: str) -> int:
    """
    计算文本的 token 数量

    Args:
        text: 输入文本

    Returns:
        token 数量
    """
    if HAS_TIKTOKEN and _tokenizer:
        return len(_tokenizer.encode(text))
    # 简单估算：英文约 4 字符/token，中文约 2 字符/token
    return len(text) // 3


@dataclass
class TextChunk:
    """文本块"""

    content: str
    metadata: Dict[str, Any]
    chunk_index: int = 0
    total_chunks: int = 1

    @property
    def token_count(self) -> int:
        """获取 token 数量"""
        return count_tokens(self.content)


class ChunkingStrategy:
    """分块策略基类"""

    def __init__(
        self,
        max_tokens: int = 500,
        overlap_tokens: int = 50,
    ):
        """
        初始化分块策略

        Args:
            max_tokens: 每个块的最大 token 数
            overlap_tokens: 块之间的重叠 token 数
        """
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, text: str, metadata: Dict[str, Any] = None) -> List[TextChunk]:
        """
        将文本分块

        Args:
            text: 输入文本
            metadata: 元数据

        Returns:
            TextChunk 列表
        """
        raise NotImplementedError


class SimpleChunker(ChunkingStrategy):
    """简单分块器 - 按固定大小分块"""

    def chunk(self, text: str, metadata: Dict[str, Any] = None) -> List[TextChunk]:
        """按固定 token 数量分块"""
        metadata = metadata or {}
        chunks = []

        if count_tokens(text) <= self.max_tokens:
            return [
                TextChunk(
                    content=text,
                    metadata=metadata.copy(),
                    chunk_index=0,
                    total_chunks=1,
                )
            ]

        # 按句子分割
        sentences = self._split_sentences(text)
        current_chunk = []
        current_tokens = 0

        for sentence in sentences:
            sentence_tokens = count_tokens(sentence)

            if current_tokens + sentence_tokens > self.max_tokens and current_chunk:
                # 保存当前块
                chunk_text = " ".join(current_chunk)
                chunks.append(
                    TextChunk(
                        content=chunk_text,
                        metadata=metadata.copy(),
                    )
                )

                # 计算重叠
                overlap_text = []
                overlap_tokens = 0
                for s in reversed(current_chunk):
                    s_tokens = count_tokens(s)
                    if overlap_tokens + s_tokens > self.overlap_tokens:
                        break
                    overlap_text.insert(0, s)
                    overlap_tokens += s_tokens

                current_chunk = overlap_text
                current_tokens = overlap_tokens

            current_chunk.append(sentence)
            current_tokens += sentence_tokens

        # 添加最后一块
        if current_chunk:
            chunk_text = " ".join(current_chunk)
            chunks.append(
                TextChunk(
                    content=chunk_text,
                    metadata=metadata.copy(),
                )
            )

        # 更新 chunk 索引
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
            chunk.total_chunks = total

        return chunks

    def _split_sentences(self, text: str) -> List[str]:
        """按句子分割文本"""
        # 简单的句子分割
        sentences = re.split(r"(?<=[.!?。！？])\s+", text)
        return [s.strip() for s in sentences if s.strip()]


class CodeChunker(ChunkingStrategy):
    """代码分块器 - 按代码结构分块"""

    def chunk(self, text: str, metadata: Dict[str, Any] = None) -> List[TextChunk]:
        """按代码结构分块"""
        metadata = metadata or {}
        chunks = []

        # 检测是否是 Java 代码
        if self._is_java_code(text):
            chunks = self._chunk_java(text, metadata)
        else:
            # 按段落分块
            chunks = self._chunk_by_paragraphs(text, metadata)

        return chunks

    def _is_java_code(self, text: str) -> bool:
        """检测是否是 Java 代码"""
        java_patterns = [
            r"\bclass\s+\w+",
            r"\bpublic\s+",
            r"\bprivate\s+",
            r"\bimport\s+",
            r"\bpackage\s+",
        ]
        return any(re.search(p, text) for p in java_patterns)

    def _chunk_java(self, text: str, metadata: Dict[str, Any]) -> List[TextChunk]:
        """按 Java 代码结构分块"""
        chunks = []

        # 尝试按方法分块
        method_pattern = r"((?:public|private|protected|static|\s)*\s*\w+\s+\w+\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{)"

        methods = []
        current_pos = 0

        for match in re.finditer(method_pattern, text):
            method_start = match.start()

            # 找到方法结束位置（匹配大括号）
            brace_count = 1
            pos = match.end()
            while pos < len(text) and brace_count > 0:
                if text[pos] == "{":
                    brace_count += 1
                elif text[pos] == "}":
                    brace_count -= 1
                pos += 1

            if brace_count == 0:
                method_text = text[method_start:pos]
                methods.append((method_start, pos, method_text))

        if methods:
            # 按方法分块
            for i, (start, end, method_text) in enumerate(methods):
                chunk_metadata = metadata.copy()
                # 提取方法名
                method_match = re.search(r"(\w+)\s*\(", method_text)
                if method_match:
                    chunk_metadata["method_name"] = method_match.group(1)

                chunks.append(
                    TextChunk(
                        content=method_text,
                        metadata=chunk_metadata,
                        chunk_index=i,
                        total_chunks=len(methods),
                    )
                )
        else:
            # 如果没找到方法，使用简单分块
            simple = SimpleChunker(self.max_tokens, self.overlap_tokens)
            chunks = simple.chunk(text, metadata)

        return chunks

    def _chunk_by_paragraphs(
        self, text: str, metadata: Dict[str, Any]
    ) -> List[TextChunk]:
        """按段落分块"""
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = []
        current_tokens = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_tokens = count_tokens(para)

            if current_tokens + para_tokens > self.max_tokens and current_chunk:
                chunk_text = "\n\n".join(current_chunk)
                chunks.append(
                    TextChunk(
                        content=chunk_text,
                        metadata=metadata.copy(),
                    )
                )
                current_chunk = []
                current_tokens = 0

            current_chunk.append(para)
            current_tokens += para_tokens

        if current_chunk:
            chunk_text = "\n\n".join(current_chunk)
            chunks.append(
                TextChunk(
                    content=chunk_text,
                    metadata=metadata.copy(),
                )
            )

        # 更新索引
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
            chunk.total_chunks = total

        return chunks


class MethodAnalysisChunker(ChunkingStrategy):
    """方法分析结果分块器 - 专门处理 Java 方法的深度分析结果"""

    def chunk_method_analysis(
        self,
        analysis: Dict[str, Any],
        class_name: str,
        source_code: Optional[str] = None,
    ) -> List[TextChunk]:
        """
        将方法分析结果转换为可检索的文本块

        Args:
            analysis: 深度分析结果（来自 DeepAnalyzer）
            class_name: 类名
            source_code: 可选的源代码

        Returns:
            TextChunk 列表
        """
        chunks = []
        method_name = analysis.get("name", "unknown")

        # 基本信息块
        basic_info = self._format_basic_info(analysis, class_name)
        chunks.append(
            TextChunk(
                content=basic_info,
                metadata={
                    "type": "method_basic_info",
                    "class_name": class_name,
                    "method_name": method_name,
                },
            )
        )

        # Null 检查模式
        null_checks = analysis.get("nullChecks", [])
        if null_checks:
            null_text = self._format_null_checks(null_checks, class_name, method_name)
            chunks.append(
                TextChunk(
                    content=null_text,
                    metadata={
                        "type": "null_check_patterns",
                        "class_name": class_name,
                        "method_name": method_name,
                    },
                )
            )

        # 边界检查模式
        boundary_checks = analysis.get("boundaryChecks", [])
        if boundary_checks:
            boundary_text = self._format_boundary_checks(
                boundary_checks, class_name, method_name
            )
            chunks.append(
                TextChunk(
                    content=boundary_text,
                    metadata={
                        "type": "boundary_check_patterns",
                        "class_name": class_name,
                        "method_name": method_name,
                    },
                )
            )

        # 异常处理
        exception_handling = analysis.get("exceptionHandling", {})
        if exception_handling.get("tryCatchBlocks") or exception_handling.get(
            "thrownExceptions"
        ):
            exception_text = self._format_exception_handling(
                exception_handling, class_name, method_name
            )
            chunks.append(
                TextChunk(
                    content=exception_text,
                    metadata={
                        "type": "exception_handling",
                        "class_name": class_name,
                        "method_name": method_name,
                    },
                )
            )

        # 方法调用
        method_calls = analysis.get("methodCalls", [])
        if method_calls:
            calls_text = self._format_method_calls(
                method_calls, class_name, method_name
            )
            chunks.append(
                TextChunk(
                    content=calls_text,
                    metadata={
                        "type": "method_dependencies",
                        "class_name": class_name,
                        "method_name": method_name,
                    },
                )
            )

        # 更新索引
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
            chunk.total_chunks = total

        return chunks

    def _format_basic_info(self, analysis: Dict[str, Any], class_name: str) -> str:
        """格式化基本信息"""
        parts = [
            f"Class: {class_name}",
            f"Method: {analysis.get('name', 'unknown')}",
            f"Signature: {analysis.get('signature', '')}",
            f"Return Type: {analysis.get('returnType', 'void')}",
            f"Visibility: {'public' if analysis.get('isPublic') else 'private/protected'}",
            f"Cyclomatic Complexity: {analysis.get('cyclomaticComplexity', 1)}",
        ]

        params = analysis.get("parameters", [])
        if params:
            param_str = ", ".join(
                f"{p.get('type', '')} {p.get('name', '')}" for p in params
            )
            parts.append(f"Parameters: {param_str}")

        if analysis.get("javadoc"):
            parts.append(f"Documentation: {analysis.get('javadoc')}")

        return "\n".join(parts)

    def _format_null_checks(
        self, null_checks: List[Dict], class_name: str, method_name: str
    ) -> str:
        """格式化 Null 检查信息"""
        parts = [
            f"Null Check Patterns in {class_name}.{method_name}:",
            "",
        ]

        for check in null_checks:
            vars_str = ", ".join(check.get("variables", []))
            parts.append(f"- Line {check.get('line', '?')}: Checking {vars_str}")
            parts.append(f"  Condition: {check.get('condition', '')}")

        return "\n".join(parts)

    def _format_boundary_checks(
        self, boundary_checks: List[Dict], class_name: str, method_name: str
    ) -> str:
        """格式化边界检查信息"""
        parts = [
            f"Boundary Check Patterns in {class_name}.{method_name}:",
            "",
        ]

        for check in boundary_checks:
            pattern = check.get("pattern", "general")
            parts.append(
                f"- Line {check.get('line', '?')}: {check.get('left', '')} {check.get('operator', '')} {check.get('right', '')}"
            )
            if pattern != "general":
                parts.append(f"  Pattern: {pattern}")

        return "\n".join(parts)

    def _format_exception_handling(
        self, exception_info: Dict, class_name: str, method_name: str
    ) -> str:
        """格式化异常处理信息"""
        parts = [
            f"Exception Handling in {class_name}.{method_name}:",
            "",
        ]

        # Declared thrown exceptions
        thrown = exception_info.get("thrownExceptions", [])
        if thrown:
            parts.append(f"Declared Exceptions: {', '.join(thrown)}")
            parts.append("")

        # Try-catch blocks
        try_catches = exception_info.get("tryCatchBlocks", [])
        for i, tc in enumerate(try_catches, 1):
            parts.append(f"Try-Catch Block {i} (line {tc.get('line', '?')}):")
            if tc.get("hasResources"):
                parts.append(f"  Resources: {', '.join(tc.get('resources', []))}")
            for catch in tc.get("catches", []):
                status = "SWALLOWED" if catch.get("isSwallowed") else "handled"
                parts.append(
                    f"  Catches {catch.get('exceptionType', 'Exception')}: {status}"
                )
            if tc.get("hasFinally"):
                parts.append("  Has finally block")
            parts.append("")

        return "\n".join(parts)

    def _format_method_calls(
        self, method_calls: List[Dict], class_name: str, method_name: str
    ) -> str:
        """格式化方法调用信息"""
        parts = [
            f"Method Dependencies in {class_name}.{method_name}:",
            "",
        ]

        # 按 scope 分组
        by_scope: Dict[str, List[Dict]] = {}
        for call in method_calls:
            scope = call.get("scope", "this")
            if scope not in by_scope:
                by_scope[scope] = []
            by_scope[scope].append(call)

        for scope, calls in by_scope.items():
            parts.append(f"Calls on {scope}:")
            for call in calls:
                args = ", ".join(call.get("arguments", []))
                parts.append(f"  - {call.get('methodName', '')}({args})")
            parts.append("")

        return "\n".join(parts)


def create_chunker(
    chunker_type: str = "simple",
    max_tokens: int = 500,
    overlap_tokens: int = 50,
) -> ChunkingStrategy:
    """
    创建分块器

    Args:
        chunker_type: 分块器类型 (simple, code, method_analysis)
        max_tokens: 最大 token 数
        overlap_tokens: 重叠 token 数

    Returns:
        ChunkingStrategy 实例
    """
    if chunker_type == "simple":
        return SimpleChunker(max_tokens, overlap_tokens)
    elif chunker_type == "code":
        return CodeChunker(max_tokens, overlap_tokens)
    elif chunker_type == "method_analysis":
        return MethodAnalysisChunker(max_tokens, overlap_tokens)
    else:
        raise ValueError(f"未知的分块器类型: {chunker_type}")
