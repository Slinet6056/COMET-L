"""向量存储模块 - ChromaDB 封装"""

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

import chromadb
from chromadb.config import Settings as ChromaSettings

from .embedding import EmbeddingService

logger = logging.getLogger(__name__)


# 知识类型常量
class KnowledgeType:
    """知识类型"""

    SOURCE_ANALYSIS = "source_analysis"  # 源代码分析结果
    BUG_REPORTS = "bug_reports"  # Bug 报告
    CONTRACTS = "contracts"  # 方法契约
    PATTERNS = "patterns"  # 缺陷模式


@dataclass
class Document:
    """文档模型"""

    id: str
    content: str
    metadata: Dict[str, Any]
    embedding: Optional[List[float]] = None


@dataclass
class SearchResult:
    """检索结果"""

    document: Document
    score: float  # 相似度分数 (0-1，越高越相似)


class VectorStore:
    """向量存储 - ChromaDB 封装"""

    def __init__(
        self,
        embedding_service: EmbeddingService,
        persist_directory: str = "./cache/chromadb",
    ):
        """
        初始化向量存储

        Args:
            embedding_service: Embedding 服务
            persist_directory: 持久化目录
        """
        self.embedding_service = embedding_service
        self.persist_directory = Path(persist_directory)

        # 确保目录存在
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # 初始化 ChromaDB 客户端
        self.client = chromadb.PersistentClient(
            path=str(self.persist_directory),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        # 集合缓存
        self._collections: Dict[str, chromadb.Collection] = {}

        logger.info(f"向量存储初始化完成，持久化目录: {self.persist_directory}")

    def _get_collection(self, knowledge_type: str) -> chromadb.Collection:
        """
        获取或创建集合

        Args:
            knowledge_type: 知识类型

        Returns:
            ChromaDB Collection
        """
        if knowledge_type not in self._collections:
            self._collections[knowledge_type] = self.client.get_or_create_collection(
                name=knowledge_type,
                metadata={"hnsw:space": "cosine"},  # 使用余弦相似度
            )
        return self._collections[knowledge_type]

    def add(
        self,
        knowledge_type: str,
        documents: List[Document],
    ) -> None:
        """
        添加文档到向量存储

        Args:
            knowledge_type: 知识类型
            documents: 文档列表
        """
        if not documents:
            return

        collection = self._get_collection(knowledge_type)

        # 获取 embeddings
        texts = [doc.content for doc in documents]
        embeddings = self.embedding_service.embed_batch(texts)

        # 准备数据
        ids = [doc.id for doc in documents]
        metadatas = [doc.metadata for doc in documents]

        # 添加到集合
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )

        logger.info(f"添加了 {len(documents)} 个文档到 {knowledge_type}")

    def add_single(
        self,
        knowledge_type: str,
        document: Document,
    ) -> None:
        """
        添加单个文档

        Args:
            knowledge_type: 知识类型
            document: 文档
        """
        self.add(knowledge_type, [document])

    def update(
        self,
        knowledge_type: str,
        document: Document,
    ) -> None:
        """
        更新文档

        Args:
            knowledge_type: 知识类型
            document: 文档
        """
        collection = self._get_collection(knowledge_type)

        # 获取 embedding
        embedding = self.embedding_service.embed(document.content)

        # 更新
        collection.update(
            ids=[document.id],
            embeddings=[embedding],
            documents=[document.content],
            metadatas=[document.metadata],
        )

        logger.debug(f"更新文档: {document.id}")

    def delete(
        self,
        knowledge_type: str,
        document_ids: List[str],
    ) -> None:
        """
        删除文档

        Args:
            knowledge_type: 知识类型
            document_ids: 文档 ID 列表
        """
        if not document_ids:
            return

        collection = self._get_collection(knowledge_type)
        collection.delete(ids=document_ids)

        logger.debug(f"删除了 {len(document_ids)} 个文档")

    def search(
        self,
        knowledge_type: str,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.0,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """
        相似度搜索

        Args:
            knowledge_type: 知识类型
            query: 查询文本
            top_k: 返回数量
            score_threshold: 相似度阈值 (0-1)
            filter_metadata: 元数据过滤条件

        Returns:
            搜索结果列表
        """
        collection = self._get_collection(knowledge_type)

        # 检查集合是否为空
        if collection.count() == 0:
            return []

        # 获取查询 embedding
        query_embedding = self.embedding_service.embed(query)

        # 执行搜索
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, collection.count()),
            where=filter_metadata,
            include=["documents", "metadatas", "distances"],
        )

        # 转换结果
        search_results = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                # ChromaDB 返回的是距离，需要转换为相似度
                # 对于余弦距离: similarity = 1 - distance
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1 - distance

                # 过滤低于阈值的结果
                if score < score_threshold:
                    continue

                document = Document(
                    id=doc_id,
                    content=results["documents"][0][i] if results["documents"] else "",
                    metadata=results["metadatas"][0][i] if results["metadatas"] else {},
                )

                search_results.append(SearchResult(document=document, score=score))

        return search_results

    def search_multi(
        self,
        knowledge_types: List[str],
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.0,
    ) -> Dict[str, List[SearchResult]]:
        """
        在多个知识类型中搜索

        Args:
            knowledge_types: 知识类型列表
            query: 查询文本
            top_k: 每个类型返回的数量
            score_threshold: 相似度阈值

        Returns:
            按知识类型分组的搜索结果
        """
        results = {}
        for kt in knowledge_types:
            results[kt] = self.search(kt, query, top_k, score_threshold)
        return results

    def get_by_id(
        self,
        knowledge_type: str,
        document_id: str,
    ) -> Optional[Document]:
        """
        根据 ID 获取文档

        Args:
            knowledge_type: 知识类型
            document_id: 文档 ID

        Returns:
            文档或 None
        """
        collection = self._get_collection(knowledge_type)

        results = collection.get(
            ids=[document_id],
            include=["documents", "metadatas"],
        )

        if results["ids"]:
            return Document(
                id=results["ids"][0],
                content=results["documents"][0] if results["documents"] else "",
                metadata=results["metadatas"][0] if results["metadatas"] else {},
            )
        return None

    def get_all(
        self,
        knowledge_type: str,
        limit: Optional[int] = None,
    ) -> List[Document]:
        """
        获取所有文档

        Args:
            knowledge_type: 知识类型
            limit: 返回数量限制

        Returns:
            文档列表
        """
        collection = self._get_collection(knowledge_type)

        count = collection.count()
        if count == 0:
            return []

        n = limit if limit and limit < count else count

        results = collection.get(
            limit=n,
            include=["documents", "metadatas"],
        )

        documents = []
        for i, doc_id in enumerate(results["ids"]):
            documents.append(
                Document(
                    id=doc_id,
                    content=results["documents"][i] if results["documents"] else "",
                    metadata=results["metadatas"][i] if results["metadatas"] else {},
                )
            )

        return documents

    def count(self, knowledge_type: str) -> int:
        """
        获取文档数量

        Args:
            knowledge_type: 知识类型

        Returns:
            文档数量
        """
        collection = self._get_collection(knowledge_type)
        return collection.count()

    def clear(self, knowledge_type: str) -> None:
        """
        清空集合

        Args:
            knowledge_type: 知识类型
        """
        # 删除并重建集合
        self.client.delete_collection(knowledge_type)
        if knowledge_type in self._collections:
            del self._collections[knowledge_type]

        logger.info(f"清空集合: {knowledge_type}")

    def get_stats(self) -> Dict[str, int]:
        """
        获取统计信息

        Returns:
            各知识类型的文档数量
        """
        stats = {}
        for kt in [
            KnowledgeType.SOURCE_ANALYSIS,
            KnowledgeType.BUG_REPORTS,
            KnowledgeType.CONTRACTS,
            KnowledgeType.PATTERNS,
        ]:
            try:
                stats[kt] = self.count(kt)
            except Exception:
                stats[kt] = 0
        return stats

    @classmethod
    def from_config(
        cls,
        vector_db_config: Any,
        embedding_service: EmbeddingService,
    ) -> "VectorStore":
        """
        从配置创建 VectorStore

        Args:
            vector_db_config: VectorDBConfig 对象
            embedding_service: Embedding 服务

        Returns:
            VectorStore 实例
        """
        return cls(
            embedding_service=embedding_service,
            persist_directory=vector_db_config.persist_directory,
        )
