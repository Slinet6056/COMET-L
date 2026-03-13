from typing import Any, cast
from unittest import TestCase
from unittest.mock import Mock

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
