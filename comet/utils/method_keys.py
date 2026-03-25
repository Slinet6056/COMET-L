from __future__ import annotations

import re

from ..utils.hash_utils import code_hash

_JAVA_MODIFIERS = {
    "public",
    "protected",
    "private",
    "static",
    "final",
    "abstract",
    "synchronized",
    "native",
    "strictfp",
    "default",
    "transient",
    "volatile",
}


def _strip_generic_arguments(type_text: str) -> str:
    depth = 0
    parts: list[str] = []
    for char in type_text:
        if char == "<":
            depth += 1
            continue
        if char == ">":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            parts.append(char)
    return "".join(parts)


def _split_top_level_parameters(parameter_text: str) -> list[str]:
    if not parameter_text:
        return []

    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in parameter_text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _simplify_java_type(type_text: str) -> str:
    normalized = " ".join(type_text.split())
    if not normalized:
        return normalized

    normalized = normalized.replace("...", "[]")
    normalized = _strip_generic_arguments(normalized)
    normalized = re.sub(r"@[^\s]+(?:\([^)]*\))?\s*", "", normalized)
    normalized = " ".join(normalized.split())
    normalized = normalized.replace("? extends ", "").replace("? super ", "").replace("?", "Object")

    array_suffix = ""
    while normalized.endswith("[]"):
        array_suffix += "[]"
        normalized = normalized[:-2].strip()

    simple_parts = [part.split(".")[-1] for part in normalized.split(".") if part]
    simplified = ".".join(simple_parts) if simple_parts else normalized
    if re.fullmatch(r"[A-Z]", simplified):
        simplified = "Object"
    return f"{simplified}{array_suffix}" if simplified else normalized


def _normalize_parameter_type(parameter_text: str) -> str:
    normalized = " ".join(parameter_text.split())
    if not normalized:
        return normalized

    normalized = normalized.replace("...", "[]")
    normalized = _strip_generic_arguments(normalized)
    normalized = re.sub(r"@[^\s]+(?:\([^)]*\))?\s*", "", normalized)

    tokens = [token for token in normalized.split() if token not in _JAVA_MODIFIERS]
    if not tokens:
        return ""

    candidate = " ".join(tokens)
    candidate = re.sub(r"\s+[A-Za-z_$][\w$]*$", "", candidate)
    return _simplify_java_type(candidate)


def canonicalize_coverage_method_signature(method_signature: str | None) -> str | None:
    normalized = normalize_method_signature(method_signature)
    if normalized is None:
        return None

    match = re.fullmatch(
        r"(?P<return>.+?)\s+(?P<name>[A-Za-z_$][\w$]*)\((?P<params>.*)\)", normalized
    )
    if match is None:
        return normalized

    return_section = match.group("return").strip()
    method_name = match.group("name")
    parameter_section = match.group("params").strip()

    return_tokens = [token for token in return_section.split() if token not in _JAVA_MODIFIERS]
    cleaned_return = " ".join(return_tokens)
    cleaned_return = re.sub(r"^<[^>]+>\s*", "", cleaned_return)
    return_type = _simplify_java_type(cleaned_return)

    parameter_types = [
        parameter_type
        for parameter_type in (
            _normalize_parameter_type(part)
            for part in _split_top_level_parameters(parameter_section)
        )
        if parameter_type
    ]
    return f"{return_type} {method_name}({', '.join(parameter_types)})"


def normalize_method_signature(method_signature: str | None) -> str | None:
    if method_signature is None:
        return None
    normalized = " ".join(method_signature.split())
    return normalized or None


def build_method_key(
    class_name: str,
    method_name: str | None,
    method_signature: str | None = None,
) -> str:
    if not method_name:
        return class_name

    normalized_signature = normalize_method_signature(method_signature)
    if normalized_signature is None:
        return f"{class_name}.{method_name}"

    signature_suffix = code_hash(normalized_signature)[:10]
    return f"{class_name}.{method_name}#{signature_suffix}"


def build_preprocess_task_id(
    class_name: str,
    method_name: str,
    method_signature: str | None = None,
) -> str:
    return f"Pre:{build_method_key(class_name, method_name, method_signature)}"


def build_test_class_name(
    class_name: str,
    method_name: str,
    method_signature: str | None = None,
) -> str:
    clean_class_name = class_name.replace("$", "_")
    normalized_signature = normalize_method_signature(method_signature)
    if normalized_signature is None:
        return f"{clean_class_name}_{method_name}Test"

    signature_suffix = code_hash(normalized_signature)[:8]
    return f"{clean_class_name}_{method_name}_{signature_suffix}Test"
