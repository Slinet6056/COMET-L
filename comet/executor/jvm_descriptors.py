from __future__ import annotations

_JVM_PRIMITIVE_TYPES = {
    "B": "byte",
    "C": "char",
    "D": "double",
    "F": "float",
    "I": "int",
    "J": "long",
    "S": "short",
    "Z": "boolean",
    "V": "void",
}


def build_method_signature(method_name: str, descriptor: str | None) -> str | None:
    if not descriptor:
        return None

    parameter_types, return_type = parse_method_descriptor(descriptor)
    if return_type is None:
        return None
    return f"{return_type} {method_name}({', '.join(parameter_types)})"


def parse_method_descriptor(descriptor: str) -> tuple[list[str], str | None]:
    if not descriptor.startswith("("):
        return [], None

    index = 1
    parameter_types: list[str] = []
    while index < len(descriptor) and descriptor[index] != ")":
        parameter_type, index = parse_descriptor_type(descriptor, index)
        if parameter_type is None:
            return [], None
        parameter_types.append(parameter_type)

    if index >= len(descriptor) or descriptor[index] != ")":
        return [], None

    return_type, index = parse_descriptor_type(descriptor, index + 1)
    if return_type is None or index != len(descriptor):
        return [], None

    return parameter_types, return_type


def parse_descriptor_type(descriptor: str, index: int) -> tuple[str | None, int]:
    if index >= len(descriptor):
        return None, index

    array_depth = 0
    while index < len(descriptor) and descriptor[index] == "[":
        array_depth += 1
        index += 1

    if index >= len(descriptor):
        return None, index

    descriptor_type = descriptor[index]
    if descriptor_type == "L":
        end_index = descriptor.find(";", index)
        if end_index == -1:
            return None, len(descriptor)
        base_type = descriptor[index + 1 : end_index].split("/")[-1].replace("$", ".")
        next_index = end_index + 1
    else:
        base_type = _JVM_PRIMITIVE_TYPES.get(descriptor_type)
        if base_type is None:
            return None, index + 1
        next_index = index + 1

    return f"{base_type}{'[]' * array_depth}", next_index
