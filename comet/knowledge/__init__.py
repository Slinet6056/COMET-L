"""知识库模块 - RAG 增强

注意：RAG 相关组件（VectorStore, EmbeddingService 等）使用延迟导入，
以避免在不使用 RAG 功能时强制要求 chromadb 依赖。
"""

# 基础组件（无 chromadb 依赖）
from .knowledge_base import KnowledgeBase, RAGKnowledgeBase, create_knowledge_base
from .bug_parser import BugReport, BugReportParser, load_bug_reports
from .chunker import (
    TextChunk,
    ChunkingStrategy,
    SimpleChunker,
    CodeChunker,
    MethodAnalysisChunker,
    create_chunker,
)

__all__ = [
    # 知识库
    "KnowledgeBase",
    "RAGKnowledgeBase",
    "create_knowledge_base",
    # Embedding（延迟导入）
    "EmbeddingService",
    # 向量存储（延迟导入）
    "VectorStore",
    "Document",
    "SearchResult",
    "KnowledgeType",
    # 检索器（延迟导入）
    "KnowledgeRetriever",
    # Bug 解析
    "BugReport",
    "BugReportParser",
    "load_bug_reports",
    # 分块
    "TextChunk",
    "ChunkingStrategy",
    "SimpleChunker",
    "CodeChunker",
    "MethodAnalysisChunker",
    "create_chunker",
]

# 延迟导入 RAG 相关组件（依赖 chromadb）
_rag_components = {
    "EmbeddingService": "embedding",
    "VectorStore": "vector_store",
    "Document": "vector_store",
    "SearchResult": "vector_store",
    "KnowledgeType": "vector_store",
    "KnowledgeRetriever": "retriever",
}


def __getattr__(name: str):
    """延迟导入 RAG 组件"""
    if name in _rag_components:
        module_name = _rag_components[name]
        try:
            import importlib

            module = importlib.import_module(f".{module_name}", __name__)
            return getattr(module, name)
        except ImportError as e:
            raise ImportError(
                f"无法导入 {name}：RAG 功能需要安装 chromadb。"
                f"请运行 'pip install chromadb' 或在配置中禁用 RAG（knowledge.enabled: false）。"
                f"\n原始错误: {e}"
            ) from e
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
