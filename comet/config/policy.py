"""Web 入口的服务端配置策略。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, get_origin

from pydantic import BaseModel, ValidationError

from comet.config.settings import Settings

PathTuple = tuple[str, ...]

USER_CONFIG_FIELDS: set[PathTuple] = {
    ("agent", "parallel", "enabled"),
    ("execution", "selected_java_version"),
}
CLAMPED_CONFIG_FIELDS: set[PathTuple] = {
    ("evolution", "max_iterations"),
    ("evolution", "budget_llm_calls"),
    ("execution", "timeout"),
}
FIXED_CONFIG_FIELDS: set[PathTuple] = {
    ("preprocessing", "max_workers"),
    ("agent", "parallel", "max_parallel_targets"),
    ("agent", "parallel", "max_eval_workers"),
    ("execution", "runtime_java_home"),
    ("execution", "maven_home"),
    ("logging", "file"),
    ("llm", "api_key"),
    ("knowledge", "embedding", "api_key"),
    ("github", "oauth_client_secret"),
    ("github", "encrypted_token_store_path"),
    ("github", "encrypted_key_store_path"),
    ("github", "managed_clone_root"),
}
SECRET_KEY_PARTS = ("api_key", "secret", "token", "password", "session")


@dataclass(slots=True)
class ConfigPolicyAnnotations:
    overridden_fields: list[str] = field(default_factory=list)
    clamped_fields: list[str] = field(default_factory=list)
    redacted_fields: list[str] = field(default_factory=list)

    def extend(self, other: "ConfigPolicyAnnotations") -> None:
        self.overridden_fields = _merge_unique(self.overridden_fields, other.overridden_fields)
        self.clamped_fields = _merge_unique(self.clamped_fields, other.clamped_fields)
        self.redacted_fields = _merge_unique(self.redacted_fields, other.redacted_fields)

    def to_api_dict(self) -> dict[str, list[str]]:
        return {
            "overriddenFields": self.overridden_fields,
            "clampedFields": self.clamped_fields,
            "redactedFields": self.redacted_fields,
        }


@dataclass(slots=True)
class ConfigPolicyResult:
    settings: Settings
    annotations: ConfigPolicyAnnotations


@dataclass(slots=True)
class ConfigPolicyFieldError:
    path: list[str | int]
    code: str
    message: str


class UnknownConfigFieldError(ValueError):
    def __init__(self, fields: Iterable[PathTuple]) -> None:
        self.fields = list(fields)
        super().__init__("Unknown submitted config field.")

    def to_field_errors(self) -> list[ConfigPolicyFieldError]:
        return [
            ConfigPolicyFieldError(
                path=list(path),
                code="unknown_config_field",
                message="Unknown submitted config field.",
            )
            for path in self.fields
        ]


class ConfigPolicyValueError(ValueError):
    def __init__(self, path: PathTuple, code: str, message: str) -> None:
        self.path = path
        self.code = code
        self.message = message
        super().__init__(message)

    def to_field_errors(self) -> list[ConfigPolicyFieldError]:
        return [ConfigPolicyFieldError(path=list(self.path), code=self.code, message=self.message)]


def apply_uploaded_config_policy(
    base_settings: Settings, submitted: dict[str, Any]
) -> ConfigPolicyResult:
    normalized = _normalize_submitted_config(submitted)
    unknown_fields = _find_unknown_fields(Settings, normalized)
    if unknown_fields:
        raise UnknownConfigFieldError(unknown_fields)

    effective = base_settings.model_dump()
    annotations = ConfigPolicyAnnotations()
    policy = base_settings.deployment

    for path, value in _flatten_mapping(normalized):
        if path in USER_CONFIG_FIELDS:
            if path == ("execution", "selected_java_version"):
                value = _normalize_allowed_java_version(value, policy.allowed_java_versions)
            _set_path(effective, path, value)
            continue

        if path in CLAMPED_CONFIG_FIELDS:
            clamped = _clamp_config_value(path, value, base_settings)
            if clamped != value:
                _append_unique(annotations.clamped_fields, _format_path(path))
            _set_path(effective, path, clamped)
            continue

        _append_unique(annotations.overridden_fields, _format_path(path))

    settings = Settings.model_validate(effective)
    clamp_annotations = enforce_deployment_policy(settings)
    annotations.extend(clamp_annotations)
    return ConfigPolicyResult(settings=settings, annotations=annotations)


def apply_run_form_policy(
    settings: Settings,
    *,
    max_iterations: int | None,
    budget: int | None,
    mutation_enabled: bool | None,
    parallel: bool,
    parallel_targets: int | None,
    selected_java_version: str | None,
) -> ConfigPolicyResult:
    effective = settings.model_copy(deep=True)
    annotations = ConfigPolicyAnnotations()

    if max_iterations is not None:
        clamped = _clamp_int(max_iterations, 1, effective.deployment.max_iterations)
        if clamped != max_iterations:
            _append_unique(annotations.clamped_fields, "evolution.max_iterations")
        effective.evolution.max_iterations = clamped

    if budget is not None:
        clamped = _clamp_int(budget, 1, effective.deployment.max_budget)
        if clamped != budget:
            _append_unique(annotations.clamped_fields, "evolution.budget")
        effective.evolution.budget_llm_calls = clamped

    if mutation_enabled is not None:
        _append_unique(annotations.overridden_fields, "evolution.mutation_enabled")

    if parallel:
        effective.agent.parallel.enabled = True

    if parallel_targets is not None:
        _append_unique(annotations.overridden_fields, "agent.parallel.max_parallel_targets")

    if selected_java_version is not None:
        effective.execution.selected_java_version = _normalize_allowed_java_version(
            selected_java_version,
            effective.deployment.allowed_java_versions,
        )

    clamp_annotations = enforce_deployment_policy(effective)
    annotations.extend(clamp_annotations)
    return ConfigPolicyResult(settings=effective, annotations=annotations)


def enforce_deployment_policy(settings: Settings) -> ConfigPolicyAnnotations:
    annotations = ConfigPolicyAnnotations()
    policy = settings.deployment

    max_iterations = _clamp_int(settings.evolution.max_iterations, 1, policy.max_iterations)
    if max_iterations != settings.evolution.max_iterations:
        _append_unique(annotations.clamped_fields, "evolution.max_iterations")
        settings.evolution.max_iterations = max_iterations

    budget = _clamp_int(settings.evolution.budget_llm_calls, 1, policy.max_budget)
    if budget != settings.evolution.budget_llm_calls:
        _append_unique(annotations.clamped_fields, "evolution.budget")
        settings.evolution.budget_llm_calls = budget

    timeout = _clamp_int(settings.execution.timeout, 1, policy.max_run_timeout_seconds)
    if timeout != settings.execution.timeout:
        _append_unique(annotations.clamped_fields, "execution.timeout")
        settings.execution.timeout = timeout

    if settings.execution.selected_java_version is not None:
        settings.execution.selected_java_version = _normalize_allowed_java_version(
            settings.execution.selected_java_version,
            policy.allowed_java_versions,
        )

    return annotations


def redact_config_snapshot(
    config: dict[str, Any],
) -> tuple[dict[str, Any], ConfigPolicyAnnotations]:
    redacted = _redact_value(config, ())
    assert isinstance(redacted, dict)
    annotations = ConfigPolicyAnnotations(redacted_fields=_collect_redacted_fields(config))
    return redacted, annotations


def redacted_settings_dict(settings: Settings) -> tuple[dict[str, Any], ConfigPolicyAnnotations]:
    return redact_config_snapshot(settings.to_dict())


def _normalize_submitted_config(submitted: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(submitted or {})
    evolution = normalized.get("evolution")
    if isinstance(evolution, dict) and "budget" in evolution:
        normalized_evolution = dict(evolution)
        if "budget_llm_calls" not in normalized_evolution:
            normalized_evolution["budget_llm_calls"] = normalized_evolution["budget"]
        normalized_evolution.pop("budget", None)
        normalized["evolution"] = normalized_evolution
    return normalized


def _find_unknown_fields(
    model: type[BaseModel], data: Any, prefix: PathTuple = ()
) -> list[PathTuple]:
    if not isinstance(data, dict):
        return []

    unknown: list[PathTuple] = []
    for key, value in data.items():
        if key not in model.model_fields:
            unknown.append((*prefix, str(key)))
            continue

        nested_model = _field_model(model.model_fields[key].annotation)
        if nested_model is not None and isinstance(value, dict):
            unknown.extend(_find_unknown_fields(nested_model, value, (*prefix, str(key))))
    return unknown


def _field_model(annotation: Any) -> type[BaseModel] | None:
    origin = get_origin(annotation)
    candidate = origin or annotation
    if isinstance(candidate, type) and issubclass(candidate, BaseModel):
        return candidate
    return None


def _flatten_mapping(
    data: dict[str, Any], prefix: PathTuple = ()
) -> Iterable[tuple[PathTuple, Any]]:
    for key, value in data.items():
        path = (*prefix, str(key))
        if isinstance(value, dict):
            yield from _flatten_mapping(value, path)
        else:
            yield path, value


def _set_path(data: dict[str, Any], path: PathTuple, value: Any) -> None:
    current = data
    for segment in path[:-1]:
        current = current.setdefault(segment, {})
    current[path[-1]] = value


def _clamp_config_value(path: PathTuple, value: Any, settings: Settings) -> int:
    parsed = _parse_int(path, value)
    if path == ("evolution", "max_iterations"):
        return _clamp_int(parsed, 1, settings.deployment.max_iterations)
    if path == ("evolution", "budget_llm_calls"):
        return _clamp_int(parsed, 1, settings.deployment.max_budget)
    if path == ("execution", "timeout"):
        return _clamp_int(parsed, 1, settings.deployment.max_run_timeout_seconds)
    return parsed


def _parse_int(path: PathTuple, value: Any) -> int:
    if isinstance(value, bool):
        raise ConfigPolicyValueError(path, "int_type", "Value must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigPolicyValueError(path, "int_parsing", "Value must be an integer.") from exc


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return min(max(value, minimum), maximum)


def _normalize_allowed_java_version(value: Any, allowed_versions: list[str]) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if normalized not in allowed_versions:
        allowed = ", ".join(allowed_versions)
        raise ConfigPolicyValueError(
            ("execution", "selected_java_version"),
            "invalid_java_version",
            f"不支持的 Java 版本: {normalized}。可选值: {allowed}",
        )
    return normalized


def _redact_value(value: Any, path: PathTuple) -> Any:
    if isinstance(value, dict):
        return {key: _redact_value(child, (*path, str(key))) for key, child in value.items()}
    if _is_secret_path(path) and value is not None:
        return "[REDACTED]"
    if isinstance(value, list):
        return [_redact_value(item, path) for item in value]
    return value


def _collect_redacted_fields(config: dict[str, Any]) -> list[str]:
    redacted: list[str] = []
    for path, value in _flatten_mapping(config):
        if _is_secret_path(path) and value is not None:
            redacted.append(_format_path(path))
    return redacted


def _is_secret_path(path: PathTuple) -> bool:
    if not path:
        return False
    key = path[-1].lower()
    return any(part in key for part in SECRET_KEY_PARTS)


def _format_path(path: PathTuple) -> str:
    if path == ("evolution", "budget_llm_calls"):
        return "evolution.budget"
    return ".".join(path)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _merge_unique(first: list[str], second: list[str]) -> list[str]:
    merged = list(first)
    for value in second:
        _append_unique(merged, value)
    return merged


def validation_errors_from_policy_error(exc: ValidationError) -> list[ConfigPolicyFieldError]:
    return [
        ConfigPolicyFieldError(
            path=list(error.get("loc", ())),
            code=str(error.get("type", "validation_error")),
            message=str(error.get("msg", "Invalid value")),
        )
        for error in exc.errors()
    ]
