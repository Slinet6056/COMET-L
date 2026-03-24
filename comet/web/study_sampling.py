from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Protocol, TypedDict

from ..utils.method_keys import build_method_key
from ..utils.project_utils import find_java_file, get_all_java_classes
from .study_protocol import (
    DEFAULT_STUDY_SAMPLE_SIZE,
    DEFAULT_STUDY_SEED,
    STUDY_OUTPUT_FILENAMES,
    StudySampledMethodSchema,
    choose_study_sample_size,
)

logger = logging.getLogger(__name__)


_TYPE_DECLARATION_RE = re.compile(
    r"\b(?P<kind>class|interface)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)


class MethodRange(TypedDict):
    begin: int
    end: int


class MethodRecord(TypedDict, total=False):
    className: str
    name: str
    signature: str
    range: MethodRange


class ClassMappingRecord(TypedDict):
    simple_name: str
    file_path: str


class PublicMethodExecutor(Protocol):
    def get_public_methods(self, file_path: str) -> list[MethodRecord] | None: ...


class ClassMappingStore(Protocol):
    def get_all_class_mappings(self) -> list[ClassMappingRecord]: ...

    def get_class_file_path(self, class_name: str) -> str | None: ...


def _method_line_count(method_info: MethodRecord) -> int | None:
    method_range = method_info.get("range")
    if not isinstance(method_range, dict):
        return None

    begin_line = method_range.get("begin")
    end_line = method_range.get("end")
    return end_line - begin_line + 1


def _is_interface_type(file_path: Path, class_name: str, cache: dict[Path, set[str]]) -> bool:
    if file_path not in cache:
        interface_names: set[str] = set()
        try:
            source = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(f"读取 Java 文件失败，无法判断是否为接口: {file_path} ({exc})")
            cache[file_path] = interface_names
            return False

        for match in _TYPE_DECLARATION_RE.finditer(source):
            if match.group("kind") == "interface":
                interface_names.add(match.group("name"))

        cache[file_path] = interface_names

    return class_name in cache[file_path]


def discover_cold_start_methods(
    project_path: str,
    java_executor: PublicMethodExecutor,
    db: ClassMappingStore | None = None,
    min_method_lines: int = 1,
) -> list[StudySampledMethodSchema]:
    discovered_methods: list[StudySampledMethodSchema] = []
    skipped_for_size = 0
    interface_cache: dict[Path, set[str]] = {}
    all_classes = sorted(get_all_java_classes(project_path, db=db))

    for class_name in all_classes:
        file_path = find_java_file(project_path, class_name, db=db)
        if not file_path:
            logger.warning(f"未找到类文件: {class_name}")
            continue

        if _is_interface_type(Path(file_path), class_name, interface_cache):
            continue

        try:
            methods = java_executor.get_public_methods(str(file_path))
        except Exception as exc:
            logger.warning(f"获取类 {class_name} 的公共方法失败: {exc}")
            continue

        if not methods:
            continue

        for method in methods:
            if method.get("className") != class_name:
                continue

            method_name = method.get("name")
            if not method_name:
                continue

            method_lines = _method_line_count(method)
            if method_lines is not None and method_lines < min_method_lines:
                skipped_for_size += 1
                continue

            method_signature = method.get("signature") or f"public void {method_name}()"
            discovered_methods.append(
                StudySampledMethodSchema(
                    target_id=build_method_key(class_name, method_name, method_signature),
                    class_name=class_name,
                    method_name=method_name,
                    method_signature=method_signature,
                    order=0,
                )
            )

    if skipped_for_size > 0:
        logger.info(f"根据最小行数配置 ({min_method_lines} 行)，跳过了 {skipped_for_size} 个方法")

    return sorted(
        discovered_methods,
        key=lambda item: (
            item.class_name,
            item.method_name,
            item.method_signature,
            item.target_id,
        ),
    )


def sample_cold_start_methods(
    discovered_methods: list[StudySampledMethodSchema],
    sample_size: int = DEFAULT_STUDY_SAMPLE_SIZE,
    seed: int = DEFAULT_STUDY_SEED,
) -> list[StudySampledMethodSchema]:
    sample_count = choose_study_sample_size(len(discovered_methods), sample_size)
    ordered_methods = list(discovered_methods)

    if sample_count >= len(ordered_methods):
        selected_methods = ordered_methods
    else:
        rng = random.Random(seed)
        selected_indexes = sorted(rng.sample(range(len(ordered_methods)), sample_count))
        selected_methods = [ordered_methods[index] for index in selected_indexes]

    return [
        method.model_copy(update={"order": order}) for order, method in enumerate(selected_methods)
    ]


def freeze_sampled_methods(
    output_dir: str | Path,
    sampled_methods: list[StudySampledMethodSchema],
) -> Path:
    output_path = Path(output_dir) / STUDY_OUTPUT_FILENAMES["sampled_methods"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [method.model_dump(mode="json") for method in sampled_methods]
    _ = output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path
