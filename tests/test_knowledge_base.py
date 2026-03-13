from typing import Any, cast
from unittest import TestCase
from unittest.mock import Mock, patch

from comet.config.settings import EmbeddingConfig, KnowledgeConfig, RetrievalConfig
from comet.knowledge.knowledge_base import RAGKnowledgeBase
from comet.knowledge.vector_store import KnowledgeType


class StubRAGKnowledgeBase(RAGKnowledgeBase):
    def set_vector_store_for_test(self, vector_store: Any) -> None:
        self._initialized = True
        self._vector_store = vector_store

    @property
    def vector_store_mock(self) -> Mock:
        vector_store = self._vector_store
        assert vector_store is not None
        return cast(Mock, vector_store)


class RAGKnowledgeBaseIndexSourceAnalysisTests(TestCase):
    def _build_knowledge_base(self) -> StubRAGKnowledgeBase:
        knowledge_base = StubRAGKnowledgeBase(store=Mock(), config=None, llm_api_key=None)
        knowledge_base.set_vector_store_for_test(Mock())
        return knowledge_base

    def test_index_source_analysis_batches_documents_per_class(self) -> None:
        knowledge_base = self._build_knowledge_base()

        analysis_result = {
            "methods": [
                {
                    "name": "add",
                    "signature": "int add(int a, int b)",
                    "returnType": "int",
                    "isPublic": True,
                    "cyclomaticComplexity": 1,
                    "parameters": [
                        {"type": "int", "name": "a"},
                        {"type": "int", "name": "b"},
                    ],
                    "nullChecks": [{"line": 10, "variables": ["a"], "condition": "a == null"}],
                    "boundaryChecks": [],
                    "exceptionHandling": {},
                    "methodCalls": [{"name": "sum", "target": "helper.sum"}],
                },
                {
                    "name": "subtract",
                    "signature": "int subtract(int a, int b)",
                    "returnType": "int",
                    "isPublic": True,
                    "cyclomaticComplexity": 1,
                    "parameters": [
                        {"type": "int", "name": "a"},
                        {"type": "int", "name": "b"},
                    ],
                    "nullChecks": [],
                    "boundaryChecks": [{"line": 20, "condition": "b > a", "type": "comparison"}],
                    "exceptionHandling": {},
                    "methodCalls": [],
                },
            ]
        }

        knowledge_base.index_source_analysis("Calculator", analysis_result)

        knowledge_base.vector_store_mock.add.assert_called_once()
        knowledge_type, documents = knowledge_base.vector_store_mock.add.call_args.args
        self.assertEqual(knowledge_type, KnowledgeType.SOURCE_ANALYSIS)
        self.assertEqual(len(documents), 5)
        self.assertEqual(
            [document.id for document in documents],
            [
                "analysis_Calculator_add_0",
                "analysis_Calculator_add_1",
                "analysis_Calculator_add_2",
                "analysis_Calculator_subtract_0",
                "analysis_Calculator_subtract_1",
            ],
        )

    def test_index_source_analysis_skips_empty_document_batch(self) -> None:
        knowledge_base = self._build_knowledge_base()

        knowledge_base.index_source_analysis("Calculator", {"methods": []})

        knowledge_base.vector_store_mock.add.assert_not_called()


class RAGKnowledgeBaseInitializationTests(TestCase):
    def test_initialize_uses_run_scoped_vector_store_directory(self) -> None:
        store = Mock()
        config = KnowledgeConfig(
            enabled=True,
            embedding=EmbeddingConfig(api_key="embedding-key"),
            retrieval=RetrievalConfig(top_k=3, score_threshold=0.2),
        )
        knowledge_base = RAGKnowledgeBase(
            store=store,
            config=config,
            llm_api_key="llm-key",
            vector_store_directory="/tmp/state/runs/run-001/chromadb",
        )

        embedding_service = Mock()
        vector_store = Mock()
        retriever = Mock()

        with (
            patch(
                "comet.knowledge.embedding.EmbeddingService.from_config",
                return_value=embedding_service,
            ) as embedding_factory,
            patch(
                "comet.knowledge.vector_store.VectorStore", return_value=vector_store
            ) as vector_cls,
            patch(
                "comet.knowledge.retriever.KnowledgeRetriever.from_config",
                return_value=retriever,
            ) as retriever_factory,
        ):
            self.assertTrue(knowledge_base.initialize())

        embedding_factory.assert_called_once_with(
            config.embedding,
            llm_api_key="llm-key",
            cache_dir="/tmp/state/runs/run-001/chromadb/embedding_cache",
        )
        vector_cls.assert_called_once_with(
            embedding_service,
            persist_directory="/tmp/state/runs/run-001/chromadb",
        )
        retriever_factory.assert_called_once_with(config.retrieval, vector_store)
