import unittest
from unittest.mock import Mock

from comet.generators.mutant_generator import MutantGenerator


class MutantGeneratorPatchValidationTests(unittest.TestCase):
    def _make_generator(self, response: str) -> MutantGenerator:
        llm = Mock()
        llm.chat_with_system.return_value = response

        knowledge_base = Mock()
        knowledge_base.get_contracts_for_class.return_value = []
        knowledge_base.get_relevant_patterns.return_value = []

        return MutantGenerator(llm, knowledge_base)

    def test_generate_mutants_rejects_duplicate_override_from_uncovered_annotation_line(
        self,
    ) -> None:
        generator = self._make_generator(
            """===MUTANT===
LINES: 3-4
ORIGINAL:
    public String toString() {
        return \"old\";
MUTATED:
    @Override
    public String toString() {
        return \"new\";
"""
        )
        class_code = (
            "public class Fraction {\n"
            "    @Override\n"
            "    public String toString() {\n"
            '        return "old";\n'
            "    }\n"
            "}\n"
        )

        mutants = generator.generate_mutants("Fraction", class_code, max_retries=1)

        self.assertEqual(mutants, [])

    def test_generate_mutants_accepts_annotation_when_replacement_range_covers_it(self) -> None:
        generator = self._make_generator(
            """===MUTANT===
LINES: 2-4
ORIGINAL:
    @Override
    public String toString() {
        return \"old\";
MUTATED:
    @Override
    public String toString() {
        return \"new\";
"""
        )
        class_code = (
            "public class Fraction {\n"
            "    @Override\n"
            "    public String toString() {\n"
            '        return "old";\n'
            "    }\n"
            "}\n"
        )

        mutants = generator.generate_mutants("Fraction", class_code, max_retries=1)

        self.assertEqual(len(mutants), 1)
        self.assertEqual(mutants[0].patch.line_start, 2)
        self.assertIn("@Override", mutants[0].patch.mutated_code)

    def test_generate_mutants_preserves_mutator_metadata(self) -> None:
        generator = self._make_generator(
            """===MUTANT===
MUTATOR: semantic-null-guard-removed
OPERATOR: semantic-null-guard-removed
LINES: 2-4
ORIGINAL:
    public String normalize(String value) {
        if (value == null) {
            return "";
MUTATED:
    public String normalize(String value) {
        if (value == null) {
            return null;
"""
        )
        class_code = (
            "public class Fraction {\n"
            "    public String normalize(String value) {\n"
            "        if (value == null) {\n"
            '            return "";\n'
            "        }\n"
            "        return value.trim();\n"
            "    }\n"
            "}\n"
        )

        mutants = generator.generate_mutants("Fraction", class_code, max_retries=1)

        self.assertEqual(len(mutants), 1)
        self.assertEqual(mutants[0].patch.mutator, "semantic-null-guard-removed")
        self.assertEqual(mutants[0].patch.operator, "semantic-null-guard-removed")
