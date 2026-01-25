"""知识检索器模块"""

import logging
from typing import List, Optional, Dict, Any

from .vector_store import VectorStore, KnowledgeType, SearchResult

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """知识检索器 - 统一的知识检索接口"""

    def __init__(
        self,
        vector_store: VectorStore,
        top_k: int = 5,
        score_threshold: float = 0.5,
    ):
        """
        初始化知识检索器

        Args:
            vector_store: 向量存储
            top_k: 每次检索返回的文档数
            score_threshold: 相似度阈值
        """
        self.vector_store = vector_store
        self.top_k = top_k
        self.score_threshold = score_threshold

    def retrieve_for_test_generation(
        self,
        class_name: str,
        method_name: str,
        method_signature: Optional[str] = None,
        source_code: Optional[str] = None,
    ) -> str:
        """
        获取测试生成相关的知识

        检索内容包括：
        - 方法的契约信息
        - 相关的 Bug 报告
        - 类似方法的缺陷模式

        Args:
            class_name: 类名
            method_name: 方法名
            method_signature: 方法签名（可选）
            source_code: 源代码（可选，用于更精确的检索）

        Returns:
            格式化的知识文本，可直接注入到 prompt
        """
        query = self._build_test_gen_query(
            class_name, method_name, method_signature, source_code
        )

        results = {}

        # 检索契约
        contracts = self.vector_store.search(
            KnowledgeType.CONTRACTS,
            query,
            top_k=self.top_k,
            score_threshold=self.score_threshold,
            filter_metadata={"class_name": class_name} if class_name else None,
        )
        results["contracts"] = contracts

        # 检索相关 Bug 报告
        bug_reports = self.vector_store.search(
            KnowledgeType.BUG_REPORTS,
            query,
            top_k=self.top_k,
            score_threshold=self.score_threshold,
        )
        results["bug_reports"] = bug_reports

        # 检索缺陷模式
        patterns = self.vector_store.search(
            KnowledgeType.PATTERNS,
            query,
            top_k=self.top_k,
            score_threshold=self.score_threshold,
        )
        results["patterns"] = patterns

        return self._format_test_gen_context(results, class_name, method_name)

    def retrieve_for_mutation_generation(
        self,
        class_name: str,
        method_name: str,
        source_code: Optional[str] = None,
    ) -> str:
        """
        获取变异生成相关的知识

        检索内容包括：
        - 方法的深度分析结果
        - 相关的缺陷模式
        - 类似代码的 Bug 报告

        Args:
            class_name: 类名
            method_name: 方法名
            source_code: 源代码（可选）

        Returns:
            格式化的知识文本
        """
        query = f"mutation patterns for {class_name}.{method_name}"
        if source_code:
            # 添加源代码的特征
            query += f" with code: {source_code[:500]}"

        results = {}

        # 检索源代码分析
        source_analysis = self.vector_store.search(
            KnowledgeType.SOURCE_ANALYSIS,
            query,
            top_k=self.top_k,
            score_threshold=self.score_threshold,
            filter_metadata={"class_name": class_name} if class_name else None,
        )
        results["source_analysis"] = source_analysis

        # 检索缺陷模式（变异生成优先）
        patterns = self.vector_store.search(
            KnowledgeType.PATTERNS,
            query,
            top_k=self.top_k * 2,  # 多获取一些模式
            score_threshold=self.score_threshold,
        )
        results["patterns"] = patterns

        # 检索相关 Bug
        bugs = self.vector_store.search(
            KnowledgeType.BUG_REPORTS,
            query,
            top_k=self.top_k,
            score_threshold=self.score_threshold,
        )
        results["bug_reports"] = bugs

        return self._format_mutation_gen_context(results, class_name, method_name)

    def retrieve_similar_bugs(
        self,
        code_snippet: str,
        top_k: Optional[int] = None,
    ) -> List[SearchResult]:
        """
        检索相似的 Bug 案例

        Args:
            code_snippet: 代码片段
            top_k: 返回数量

        Returns:
            SearchResult 列表
        """
        return self.vector_store.search(
            KnowledgeType.BUG_REPORTS,
            f"bug in code: {code_snippet}",
            top_k=top_k or self.top_k,
            score_threshold=self.score_threshold,
        )

    def retrieve_patterns_for_category(
        self,
        category: str,
        top_k: Optional[int] = None,
    ) -> List[SearchResult]:
        """
        检索特定类别的缺陷模式

        Args:
            category: 模式类别（如 null_pointer, boundary 等）
            top_k: 返回数量

        Returns:
            SearchResult 列表
        """
        return self.vector_store.search(
            KnowledgeType.PATTERNS,
            f"{category} defect pattern",
            top_k=top_k or self.top_k,
            score_threshold=self.score_threshold,
            filter_metadata={"category": category},
        )

    def retrieve_method_analysis(
        self,
        class_name: str,
        method_name: str,
    ) -> List[SearchResult]:
        """
        检索方法的深度分析结果

        Args:
            class_name: 类名
            method_name: 方法名

        Returns:
            SearchResult 列表
        """
        return self.vector_store.search(
            KnowledgeType.SOURCE_ANALYSIS,
            f"analysis of {class_name}.{method_name}",
            top_k=self.top_k,
            score_threshold=self.score_threshold,
            filter_metadata={"class_name": class_name, "method_name": method_name},
        )

    def retrieve_contracts(
        self,
        class_name: str,
        method_name: Optional[str] = None,
    ) -> List[SearchResult]:
        """
        检索契约信息

        Args:
            class_name: 类名
            method_name: 方法名（可选）

        Returns:
            SearchResult 列表
        """
        query = f"contract for {class_name}"
        if method_name:
            query += f".{method_name}"

        filter_meta = {"class_name": class_name}
        if method_name:
            filter_meta["method_name"] = method_name

        return self.vector_store.search(
            KnowledgeType.CONTRACTS,
            query,
            top_k=self.top_k,
            score_threshold=self.score_threshold,
            filter_metadata=filter_meta,
        )

    def _build_test_gen_query(
        self,
        class_name: str,
        method_name: str,
        method_signature: Optional[str] = None,
        source_code: Optional[str] = None,
    ) -> str:
        """构建测试生成查询"""
        parts = [f"test generation for {class_name}.{method_name}"]

        if method_signature:
            parts.append(f"signature: {method_signature}")

        if source_code:
            # 取源代码的前 300 个字符作为上下文
            parts.append(f"code: {source_code[:300]}")

        return " ".join(parts)

    def _format_test_gen_context(
        self,
        results: Dict[str, List[SearchResult]],
        class_name: str,
        method_name: str,
    ) -> str:
        """格式化测试生成上下文"""
        sections = []

        # 契约信息
        contracts = results.get("contracts", [])
        if contracts:
            sections.append("## 方法契约信息")
            for r in contracts[:3]:  # 最多 3 个
                sections.append(f"\n{r.document.content}")

        # 相关 Bug 模式
        bugs = results.get("bug_reports", [])
        if bugs:
            sections.append("\n## 相关 Bug 案例（参考）")
            for r in bugs[:2]:  # 最多 2 个
                sections.append(f"\n{r.document.content[:500]}...")

        # 缺陷模式
        patterns = results.get("patterns", [])
        if patterns:
            sections.append("\n## 相关缺陷模式（测试应覆盖）")
            for r in patterns[:3]:
                sections.append(f"\n- {r.document.content[:200]}")

        if not sections:
            return ""

        header = f"# {class_name}.{method_name} 的相关知识\n"
        return header + "\n".join(sections)

    def _format_mutation_gen_context(
        self,
        results: Dict[str, List[SearchResult]],
        class_name: str,
        method_name: str,
    ) -> str:
        """格式化变异生成上下文"""
        sections = []

        # 源代码分析
        analysis = results.get("source_analysis", [])
        if analysis:
            sections.append("## 代码分析结果")
            for r in analysis[:2]:
                sections.append(f"\n{r.document.content}")

        # 缺陷模式（重点）
        patterns = results.get("patterns", [])
        if patterns:
            sections.append("\n## 可用的缺陷模式（用于变异）")
            for r in patterns[:5]:  # 变异生成需要更多模式
                sections.append(f"\n### {r.document.metadata.get('name', 'Pattern')}")
                sections.append(r.document.content[:300])

        # Bug 案例
        bugs = results.get("bug_reports", [])
        if bugs:
            sections.append("\n## 相关 Bug 案例（变异参考）")
            for r in bugs[:2]:
                sections.append(f"\n{r.document.content[:400]}...")

        if not sections:
            return ""

        header = f"# {class_name}.{method_name} 的变异知识\n"
        return header + "\n".join(sections)

    @classmethod
    def from_config(
        cls,
        retrieval_config: Any,
        vector_store: VectorStore,
    ) -> "KnowledgeRetriever":
        """
        从配置创建 KnowledgeRetriever

        Args:
            retrieval_config: RetrievalConfig 对象
            vector_store: 向量存储

        Returns:
            KnowledgeRetriever 实例
        """
        return cls(
            vector_store=vector_store,
            top_k=retrieval_config.top_k,
            score_threshold=retrieval_config.score_threshold,
        )
