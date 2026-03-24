from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ..utils.hash_utils import generate_id
from ..utils.method_keys import build_method_key, normalize_method_signature
from ..web.study_protocol import StudyMutantStatus
from .jvm_descriptors import build_method_signature

logger = logging.getLogger(__name__)

_REQUIRED_MUTATION_CHILDREN = (
    "mutatedClass",
    "mutatedMethod",
    "methodDescription",
    "lineNumber",
    "mutator",
)


class PitXmlParseError(ValueError):
    pass


@dataclass(slots=True)
class PitMutantRecord:
    mutant_id: str
    class_name: str
    method_name: str
    method_signature: str
    method_key: str
    method_description: str
    line_number: int
    mutator: str
    status: str
    killing_test: str | None


class PitXmlParser:
    def parse_mutations_xml(self, xml_path: str | Path) -> list[PitMutantRecord]:
        path = Path(xml_path)
        if not path.exists():
            raise PitXmlParseError(f"PIT XML 文件不存在: {path}")

        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise PitXmlParseError(f"PIT XML 解析失败: {path}") from exc

        return self._parse_root(root, source=str(path))

    def parse_xml_content(self, xml_content: str) -> list[PitMutantRecord]:
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as exc:
            raise PitXmlParseError("PIT XML 内容解析失败") from exc

        return self._parse_root(root, source="<memory>")

    def _parse_root(self, root: ET.Element, source: str) -> list[PitMutantRecord]:
        mutations = root.findall("mutation")
        records: list[PitMutantRecord] = []

        for index, mutation_elem in enumerate(mutations, start=1):
            try:
                records.append(self._parse_mutation(mutation_elem))
            except PitXmlParseError as exc:
                raise PitXmlParseError(f"PIT mutation 记录无效 ({source}#{index}): {exc}") from exc

        return records

    def _parse_mutation(self, mutation_elem: ET.Element) -> PitMutantRecord:
        values = {
            field_name: self._require_child_text(mutation_elem, field_name)
            for field_name in _REQUIRED_MUTATION_CHILDREN
        }
        values["status"] = self._require_value(mutation_elem, "status")

        method_signature = build_method_signature(
            values["mutatedMethod"],
            values["methodDescription"],
        )
        normalized_signature = normalize_method_signature(method_signature)
        if normalized_signature is None:
            raise PitXmlParseError(
                f"无法将 methodDescription 转换为方法签名: {values['methodDescription']}"
            )

        try:
            line_number = int(values["lineNumber"])
        except ValueError as exc:
            raise PitXmlParseError(f"lineNumber 不是有效整数: {values['lineNumber']}") from exc

        try:
            status = StudyMutantStatus(values["status"]).value
        except ValueError as exc:
            raise PitXmlParseError(f"不支持的 PIT status: {values['status']}") from exc

        class_name = values["mutatedClass"]
        method_name = values["mutatedMethod"]
        method_key = build_method_key(class_name, method_name, normalized_signature)
        killing_test = self._optional_child_text(mutation_elem, "killingTest")

        mutant_id = generate_id(
            "pit_mutant",
            (
                f"{class_name}|{method_name}|{normalized_signature}|{line_number}|"
                f"{values['mutator']}|{values['methodDescription']}"
            ),
        )

        return PitMutantRecord(
            mutant_id=mutant_id,
            class_name=class_name,
            method_name=method_name,
            method_signature=normalized_signature,
            method_key=method_key,
            method_description=values["methodDescription"],
            line_number=line_number,
            mutator=values["mutator"],
            status=status,
            killing_test=killing_test,
        )

    def _require_child_text(self, parent: ET.Element, child_name: str) -> str:
        value = self._optional_child_text(parent, child_name)
        if value is None:
            raise PitXmlParseError(f"缺少关键字段 {child_name}")
        return value

    def _require_value(self, parent: ET.Element, field_name: str) -> str:
        value = parent.get(field_name)
        if value is None:
            value = self._optional_child_text(parent, field_name)
        if value is None:
            raise PitXmlParseError(f"缺少关键字段 {field_name}")
        return value

    def _optional_child_text(self, parent: ET.Element, child_name: str) -> str | None:
        child = parent.find(child_name)
        if child is None:
            return None

        text = child.text.strip() if child.text is not None else ""
        return text or None


def parse_pit_mutations_xml(xml_path: str | Path) -> list[PitMutantRecord]:
    return PitXmlParser().parse_mutations_xml(xml_path)
