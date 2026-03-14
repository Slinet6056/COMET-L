from typing import Any, cast
from unittest import TestCase
from unittest.mock import Mock, patch

from comet.config.settings import EmbeddingConfig, KnowledgeConfig, RetrievalConfig
from comet.knowledge.knowledge_base import RAGKnowledgeBase
from comet.knowledge.retriever import KnowledgeRetriever
from comet.knowledge.vector_store import KnowledgeType, VectorStore
from comet.models import Contract
from comet.store.knowledge_store import KnowledgeStore


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
        self.assertTrue(
            documents[0].id.startswith("analysis_Calculator_add_int add(int a, int b)_")
        )
        self.assertTrue(
            documents[3].id.startswith("analysis_Calculator_subtract_int subtract(int a, int b)_")
        )
        self.assertEqual(documents[0].metadata["method_signature"], "int add(int a, int b)")

    def test_method_contract_lookup_uses_method_signature_when_provided(self) -> None:
        store = KnowledgeStore(":memory:")
        knowledge_base = RAGKnowledgeBase(store=store, config=None, llm_api_key=None)

        first_contract = Contract(
            id="contract-int-add",
            class_name="Calculator",
            method_name="add",
            method_signature="int add(int a, int b)",
            preconditions=[],
            postconditions=["returns integer sum"],
            exceptions=[],
            description="int overload",
            source="test",
            confidence=1.0,
        )
        second_contract = Contract(
            id="contract-double-add",
            class_name="Calculator",
            method_name="add",
            method_signature="double add(double a, double b)",
            preconditions=[],
            postconditions=["returns double sum"],
            exceptions=[],
            description="double overload",
            source="test",
            confidence=1.0,
        )

        knowledge_base.add_contract(first_contract)
        knowledge_base.add_contract(second_contract)

        contracts = knowledge_base.get_contracts_for_method(
            "Calculator",
            "add",
            "double add(double a, double b)",
        )

        self.assertEqual([contract.id for contract in contracts], ["contract-double-add"])

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


class VectorStoreFilterNormalizationTests(TestCase):
    def test_normalize_filter_metadata_keeps_single_field_filter(self) -> None:
        self.assertEqual(
            VectorStore._normalize_filter_metadata({"class_name": "ProductService"}),
            {"class_name": "ProductService"},
        )

    def test_normalize_filter_metadata_wraps_multi_field_filter_in_and(self) -> None:
        self.assertEqual(
            VectorStore._normalize_filter_metadata(
                {
                    "class_name": "ProductService",
                    "method_signature": "void addProduct(String, double)",
                }
            ),
            {
                "$and": [
                    {"class_name": "ProductService"},
                    {"method_signature": "void addProduct(String, double)"},
                ]
            },
        )

    def test_normalize_filter_metadata_preserves_operator_filter(self) -> None:
        operator_filter = {
            "$and": [
                {"class_name": "ProductService"},
                {"method_signature": "void addProduct(String, double)"},
            ]
        }
        self.assertIs(VectorStore._normalize_filter_metadata(operator_filter), operator_filter)


class KnowledgeRetrieverFilterTests(TestCase):
    def test_retrieve_for_mutation_generation_uses_multi_field_filter(self) -> None:
        vector_store = Mock()
        vector_store.search.return_value = []
        retriever = KnowledgeRetriever(vector_store=vector_store, top_k=3, score_threshold=0.2)

        retriever.retrieve_for_mutation_generation(
            class_name="ProductService",
            method_name="addProduct",
            method_signature="void addProduct(String, double)",
            source_code="public void addProduct(String name, double price) {}",
        )

        search_calls = vector_store.search.call_args_list
        self.assertGreaterEqual(len(search_calls), 1)
        source_analysis_call = search_calls[0]
        self.assertEqual(source_analysis_call.args[0], KnowledgeType.SOURCE_ANALYSIS)
        self.assertEqual(
            source_analysis_call.kwargs["filter_metadata"],
            {
                "class_name": "ProductService",
                "method_signature": "void addProduct(String, double)",
            },
        )
