"""知识库管理 - 整合 RAG 功能"""

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from ..models import Contract, Pattern
from ..store.knowledge_store import KnowledgeStore
from ..config.settings import KnowledgeConfig

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """知识库管理类 - 管理 Patterns 和 Contracts（传统模式）"""

    def __init__(self, store: KnowledgeStore):
        """
        初始化知识库

        Args:
            store: 知识库存储实例
        """
        self.store = store
        self._contracts_cache: Dict[str, List[Contract]] = {}
        self._patterns_cache: Optional[List[Pattern]] = None

    def add_contract(self, contract: Contract) -> None:
        """
        添加契约到知识库

        Args:
            contract: 契约对象
        """
        self.store.save_contract(contract)
        # 清除缓存
        if contract.class_name in self._contracts_cache:
            del self._contracts_cache[contract.class_name]
        logger.info(f"添加契约: {contract.class_name}.{contract.method_name}")

    def get_contracts_for_class(self, class_name: str) -> List[Contract]:
        """
        获取类的所有契约

        Args:
            class_name: 类名

        Returns:
            契约列表
        """
        if class_name not in self._contracts_cache:
            self._contracts_cache[class_name] = self.store.get_contracts_by_class(
                class_name
            )
        return self._contracts_cache[class_name]

    def get_contracts_for_method(
        self, class_name: str, method_name: str
    ) -> List[Contract]:
        """
        获取方法的契约

        Args:
            class_name: 类名
            method_name: 方法名

        Returns:
            契约列表
        """
        return self.store.get_contracts_by_method(class_name, method_name)

    def add_pattern(self, pattern: Pattern) -> None:
        """
        添加缺陷模式到知识库

        Args:
            pattern: 模式对象
        """
        self.store.save_pattern(pattern)
        # 清除缓存
        self._patterns_cache = None
        logger.info(f"添加模式: {pattern.name} ({pattern.category})")

    def get_all_patterns(self) -> List[Pattern]:
        """
        获取所有模式

        Returns:
            模式列表（按成功率和使用次数排序）
        """
        if self._patterns_cache is None:
            self._patterns_cache = self.store.get_all_patterns()
        return self._patterns_cache

    def get_patterns_by_category(self, category: str) -> List[Pattern]:
        """
        获取特定类别的模式

        Args:
            category: 模式类别

        Returns:
            模式列表
        """
        return self.store.get_patterns_by_category(category)

    def get_top_patterns(
        self, n: int = 5, min_confidence: float = 0.5
    ) -> List[Pattern]:
        """
        获取最佳模式（根据成功率和置信度）

        Args:
            n: 返回数量
            min_confidence: 最小置信度

        Returns:
            模式列表
        """
        all_patterns = self.get_all_patterns()
        filtered = [p for p in all_patterns if p.confidence >= min_confidence]
        return filtered[:n]

    def update_pattern_usage(self, pattern_id: str, success: bool) -> None:
        """
        更新模式使用统计

        Args:
            pattern_id: 模式 ID
            success: 是否成功（发现了缺陷）
        """
        self.store.update_pattern_stats(pattern_id, success)
        # 清除缓存
        self._patterns_cache = None
        logger.debug(f"更新模式统计: {pattern_id}, 成功={success}")

    def get_relevant_patterns(
        self, class_code: str, max_patterns: int = 10
    ) -> List[Pattern]:
        """
        获取与代码相关的模式（简化版：返回高成功率的模式）

        Args:
            class_code: 类代码
            max_patterns: 最大返回数量

        Returns:
            相关模式列表
        """
        # 简化版实现：返回成功率最高的模式
        return self.get_top_patterns(n=max_patterns)

    def get_stats(self) -> Dict[str, Any]:
        """
        获取知识库统计信息

        Returns:
            统计信息字典
        """
        all_contracts = self.store.get_all_contracts()
        all_patterns = self.get_all_patterns()

        return {
            "total_contracts": len(all_contracts),
            "total_patterns": len(all_patterns),
            "pattern_categories": len(set(p.category for p in all_patterns)),
            "avg_pattern_success_rate": (
                sum(p.success_rate for p in all_patterns) / len(all_patterns)
                if all_patterns
                else 0.0
            ),
        }

    def clear_cache(self) -> None:
        """清除缓存"""
        self._contracts_cache.clear()
        self._patterns_cache = None


class RAGKnowledgeBase(KnowledgeBase):
    """RAG 增强的知识库 - 支持向量检索"""

    def __init__(
        self,
        store: KnowledgeStore,
        config: Optional[KnowledgeConfig] = None,
        llm_api_key: Optional[str] = None,
    ):
        """
        初始化 RAG 知识库

        Args:
            store: 知识库存储实例
            config: 知识库配置
            llm_api_key: LLM API 密钥（用于 Embedding）
        """
        super().__init__(store)
        self.config = config
        self.llm_api_key = llm_api_key

        # RAG 组件（延迟初始化）
        self._embedding_service = None
        self._vector_store = None
        self._retriever = None
        self._initialized = False

    def _ensure_initialized(self) -> bool:
        """确保 RAG 组件已初始化"""
        if self._initialized:
            return True

        if not self.config or not self.config.enabled:
            logger.info("RAG 知识库未启用")
            return False

        try:
            from .embedding import EmbeddingService
            from .vector_store import VectorStore
            from .retriever import KnowledgeRetriever

            # 初始化 Embedding 服务
            cache_dir = str(
                Path(self.config.vector_db.persist_directory) / "embedding_cache"
            )
            self._embedding_service = EmbeddingService.from_config(
                self.config.embedding,
                llm_api_key=self.llm_api_key,
                cache_dir=cache_dir,
            )

            # 初始化向量存储
            self._vector_store = VectorStore.from_config(
                self.config.vector_db,
                self._embedding_service,
            )

            # 初始化检索器
            self._retriever = KnowledgeRetriever.from_config(
                self.config.retrieval,
                self._vector_store,
            )

            self._initialized = True
            logger.info("RAG 知识库初始化完成")
            return True

        except Exception as e:
            logger.warning(f"RAG 知识库初始化失败: {e}")
            return False

    @property
    def is_rag_enabled(self) -> bool:
        """检查 RAG 是否启用"""
        return self._initialized or (self.config and self.config.enabled)

    @property
    def vector_store(self):
        """获取向量存储"""
        self._ensure_initialized()
        return self._vector_store

    @property
    def retriever(self):
        """获取检索器"""
        self._ensure_initialized()
        return self._retriever

    def add_contract(self, contract: Contract) -> None:
        """添加契约到知识库（同时更新向量存储）"""
        super().add_contract(contract)

        # 同步到向量存储
        if self._ensure_initialized():
            self._index_contract(contract)

    def add_pattern(self, pattern: Pattern) -> None:
        """添加缺陷模式到知识库（同时更新向量存储）"""
        super().add_pattern(pattern)

        # 同步到向量存储
        if self._ensure_initialized():
            self._index_pattern(pattern)

    def _index_contract(self, contract: Contract) -> None:
        """将契约索引到向量存储"""
        from .vector_store import Document, KnowledgeType

        # 构建文档内容
        content = self._format_contract_for_indexing(contract)

        doc = Document(
            id=contract.id,
            content=content,
            metadata={
                "class_name": contract.class_name,
                "method_name": contract.method_name,
                "method_signature": contract.method_signature,
                "source": contract.source,
                "confidence": contract.confidence,
            },
        )

        self._vector_store.add_single(KnowledgeType.CONTRACTS, doc)

    def _index_pattern(self, pattern: Pattern) -> None:
        """将模式索引到向量存储"""
        from .vector_store import Document, KnowledgeType

        # 构建文档内容
        content = self._format_pattern_for_indexing(pattern)

        doc = Document(
            id=pattern.id,
            content=content,
            metadata={
                "name": pattern.name,
                "category": pattern.category,
                "confidence": pattern.confidence,
                "success_rate": pattern.success_rate,
            },
        )

        self._vector_store.add_single(KnowledgeType.PATTERNS, doc)

    def _format_contract_for_indexing(self, contract: Contract) -> str:
        """格式化契约用于索引"""
        parts = [
            f"Contract for {contract.class_name}.{contract.method_name}",
            f"Signature: {contract.method_signature}",
        ]

        if contract.preconditions:
            parts.append(f"Preconditions: {', '.join(contract.preconditions)}")

        if contract.postconditions:
            parts.append(f"Postconditions: {', '.join(contract.postconditions)}")

        if contract.exceptions:
            parts.append(f"Exceptions: {', '.join(contract.exceptions)}")

        if contract.description:
            parts.append(f"Description: {contract.description}")

        return "\n".join(parts)

    def _format_pattern_for_indexing(self, pattern: Pattern) -> str:
        """格式化模式用于索引"""
        parts = [
            f"Defect Pattern: {pattern.name}",
            f"Category: {pattern.category}",
            f"Description: {pattern.description}",
            f"Template: {pattern.template}",
        ]

        if pattern.examples:
            parts.append(f"Examples: {', '.join(pattern.examples[:3])}")

        if pattern.mutation_strategy:
            parts.append(f"Mutation Strategy: {pattern.mutation_strategy}")

        return "\n".join(parts)

    def get_relevant_patterns(
        self, class_code: str, max_patterns: int = 10
    ) -> List[Pattern]:
        """
        获取与代码相关的模式（使用 RAG 检索）

        Args:
            class_code: 类代码
            max_patterns: 最大返回数量

        Returns:
            相关模式列表
        """
        if not self._ensure_initialized():
            # 回退到传统方式
            return super().get_relevant_patterns(class_code, max_patterns)

        from .vector_store import KnowledgeType

        # 使用向量检索
        results = self._vector_store.search(
            KnowledgeType.PATTERNS,
            f"defect patterns for code: {class_code[:500]}",
            top_k=max_patterns,
            score_threshold=self.config.retrieval.score_threshold,
        )

        # 获取完整的 Pattern 对象
        patterns = []
        for r in results:
            pattern = self.store.get_pattern(r.document.id)
            if pattern:
                patterns.append(pattern)

        # 如果检索结果不足，用传统方式补充
        if len(patterns) < max_patterns:
            top_patterns = super().get_top_patterns(n=max_patterns - len(patterns))
            existing_ids = {p.id for p in patterns}
            for p in top_patterns:
                if p.id not in existing_ids:
                    patterns.append(p)

        return patterns[:max_patterns]

    def retrieve_for_test_generation(
        self,
        class_name: str,
        method_name: str,
        method_signature: Optional[str] = None,
        source_code: Optional[str] = None,
    ) -> str:
        """
        获取测试生成相关的知识（RAG 检索）

        Args:
            class_name: 类名
            method_name: 方法名
            method_signature: 方法签名
            source_code: 源代码

        Returns:
            格式化的知识文本
        """
        if not self._ensure_initialized():
            return ""

        return self._retriever.retrieve_for_test_generation(
            class_name, method_name, method_signature, source_code
        )

    def retrieve_for_mutation_generation(
        self,
        class_name: str,
        method_name: str,
        source_code: Optional[str] = None,
    ) -> str:
        """
        获取变异生成相关的知识（RAG 检索）

        Args:
            class_name: 类名
            method_name: 方法名
            source_code: 源代码

        Returns:
            格式化的知识文本
        """
        if not self._ensure_initialized():
            return ""

        return self._retriever.retrieve_for_mutation_generation(
            class_name, method_name, source_code
        )

    def index_source_analysis(
        self,
        class_name: str,
        analysis_result: Dict[str, Any],
    ) -> None:
        """
        索引源代码深度分析结果

        Args:
            class_name: 类名
            analysis_result: DeepAnalyzer 的分析结果
        """
        if not self._ensure_initialized():
            return

        from .vector_store import Document, KnowledgeType
        from .chunker import MethodAnalysisChunker

        chunker = MethodAnalysisChunker()

        # 分析结果中的每个方法
        methods = analysis_result.get("methods", [])
        for method in methods:
            chunks = chunker.chunk_method_analysis(method, class_name)

            for chunk in chunks:
                doc = Document(
                    id=f"analysis_{class_name}_{method.get('name', 'unknown')}_{chunk.chunk_index}",
                    content=chunk.content,
                    metadata=chunk.metadata,
                )
                self._vector_store.add_single(KnowledgeType.SOURCE_ANALYSIS, doc)

        logger.info(f"索引了 {class_name} 的 {len(methods)} 个方法分析结果")

    def index_bug_reports(self, bug_reports_dir: str) -> int:
        """
        索引 Bug 报告目录

        Args:
            bug_reports_dir: Bug 报告目录

        Returns:
            索引的 Bug 报告数量
        """
        if not self._ensure_initialized():
            return 0

        from .bug_parser import load_bug_reports
        from .vector_store import Document, KnowledgeType

        reports = load_bug_reports(bug_reports_dir)

        for report in reports:
            doc = Document(
                id=report.id,
                content=report.to_text(),
                metadata={
                    "title": report.title,
                    "file_path": report.file_path,
                    "file_type": report.file_type,
                },
            )
            self._vector_store.add_single(KnowledgeType.BUG_REPORTS, doc)

        logger.info(f"索引了 {len(reports)} 个 Bug 报告")
        return len(reports)

    def sync_to_vector_store(self) -> None:
        """
        同步所有现有知识到向量存储
        """
        if not self._ensure_initialized():
            return

        # 同步所有契约
        all_contracts = self.store.get_all_contracts()
        for contract in all_contracts:
            self._index_contract(contract)

        # 同步所有模式
        all_patterns = self.get_all_patterns()
        for pattern in all_patterns:
            self._index_pattern(pattern)

        logger.info(
            f"同步完成: {len(all_contracts)} 个契约, {len(all_patterns)} 个模式"
        )

    def get_stats(self) -> Dict[str, Any]:
        """获取知识库统计信息（包括 RAG 统计）"""
        stats = super().get_stats()

        if self._initialized and self._vector_store:
            vector_stats = self._vector_store.get_stats()
            stats["vector_store"] = vector_stats
            stats["rag_enabled"] = True
        else:
            stats["rag_enabled"] = False

        return stats


def create_knowledge_base(
    store: KnowledgeStore,
    config: Optional[KnowledgeConfig] = None,
    llm_api_key: Optional[str] = None,
) -> KnowledgeBase:
    """
    创建知识库实例

    Args:
        store: 知识库存储
        config: 知识库配置
        llm_api_key: LLM API 密钥

    Returns:
        KnowledgeBase 实例（RAG 或传统模式）
    """
    if config and config.enabled:
        return RAGKnowledgeBase(store, config, llm_api_key)
    return KnowledgeBase(store)
