from __future__ import annotations

from ..utils.hash_utils import code_hash


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
