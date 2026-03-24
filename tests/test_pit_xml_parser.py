import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from comet.executor.pit_xml_parser import PitXmlParseError, parse_pit_mutations_xml
from comet.utils.method_keys import build_method_key


class PitXmlParserTests(unittest.TestCase):
    def test_maps_mutated_method_descriptor_to_comet_method_signature(self) -> None:
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<mutations>
  <mutation detected="true" status="KILLED" numberOfTestsRun="1">
    <sourceFile>Calculator.java</sourceFile>
    <mutatedClass>com.example.Calculator</mutatedClass>
    <mutatedMethod>add</mutatedMethod>
    <methodDescription>(II)I</methodDescription>
    <lineNumber>42</lineNumber>
    <mutator>org.pitest.mutationtest.engine.gregor.mutators.MathMutator</mutator>
    <indexes><index>0</index></indexes>
    <blocks><block>0</block></blocks>
    <killingTest>com.example.CalculatorTest.testAdd(com.example.CalculatorTest)</killingTest>
    <description>Replaced integer addition with subtraction</description>
  </mutation>
  <mutation detected="false" status="SURVIVED" numberOfTestsRun="0">
    <sourceFile>Calculator.java</sourceFile>
    <mutatedClass>com.example.Calculator</mutatedClass>
    <mutatedMethod>add</mutatedMethod>
    <methodDescription>(DD)D</methodDescription>
    <lineNumber>57</lineNumber>
    <mutator>org.pitest.mutationtest.engine.gregor.mutators.MathMutator</mutator>
    <indexes><index>1</index></indexes>
    <blocks><block>1</block></blocks>
    <killingTest />
    <description>Replaced double addition with subtraction</description>
  </mutation>
</mutations>
"""

        with TemporaryDirectory() as tmp_dir:
            xml_path = Path(tmp_dir) / "mutations.xml"
            _ = xml_path.write_text(xml_content, encoding="utf-8")

            records = parse_pit_mutations_xml(xml_path)

        self.assertEqual(len(records), 2)

        int_record = records[0]
        self.assertEqual(int_record.class_name, "com.example.Calculator")
        self.assertEqual(int_record.method_name, "add")
        self.assertEqual(int_record.method_signature, "int add(int, int)")
        self.assertEqual(
            int_record.method_key,
            build_method_key("com.example.Calculator", "add", "int add(int, int)"),
        )
        self.assertEqual(int_record.method_description, "(II)I")
        self.assertEqual(int_record.line_number, 42)
        self.assertEqual(
            int_record.mutator,
            "org.pitest.mutationtest.engine.gregor.mutators.MathMutator",
        )
        self.assertEqual(int_record.status, "KILLED")
        self.assertEqual(
            int_record.killing_test,
            "com.example.CalculatorTest.testAdd(com.example.CalculatorTest)",
        )

        double_record = records[1]
        self.assertEqual(double_record.method_signature, "double add(double, double)")
        self.assertEqual(
            double_record.method_key,
            build_method_key("com.example.Calculator", "add", "double add(double, double)"),
        )
        self.assertEqual(double_record.status, "SURVIVED")
        self.assertIsNone(double_record.killing_test)
        self.assertNotEqual(int_record.method_key, double_record.method_key)

    def test_rejects_invalid_mutation_xml_record(self) -> None:
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<mutations>
  <mutation detected="true" status="RUN_ERROR" numberOfTestsRun="0">
    <mutatedClass>com.example.Calculator</mutatedClass>
    <mutatedMethod>add</mutatedMethod>
    <lineNumber>not-a-number</lineNumber>
    <mutator>org.pitest.mutationtest.engine.gregor.mutators.MathMutator</mutator>
    <killingTest />
  </mutation>
</mutations>
"""

        with TemporaryDirectory() as tmp_dir:
            xml_path = Path(tmp_dir) / "mutations.xml"
            _ = xml_path.write_text(xml_content, encoding="utf-8")

            with self.assertRaisesRegex(PitXmlParseError, "缺少关键字段 methodDescription"):
                _ = parse_pit_mutations_xml(xml_path)
