# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2026 Daniel Schmidt

"""Device filtering engine for D2D and API test execution.

Provides expression parsing, path traversal, stringification, pure-Mapping
predicate matching, and batch filtering with diagnostics for NAC test runs.
"""

import dataclasses
import json
import logging
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Supported operators ordered longest-first for deterministic prefix parsing
VALID_OPERATORS: tuple[str, ...] = ("!=~", "=~", "!=", "=")
POSITIVE_OPERATORS: frozenset[str] = frozenset({"=", "=~"})
NEGATIVE_OPERATORS: frozenset[str] = frozenset({"!=", "!=~"})
_IDENTIFIER_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _stringify(val: Any) -> str | None:
    """Stringify a data-model value for filter comparison.

    - Bools -> "true" / "false"
    - None -> None (treated as missing)
    - Others -> str(val)
    """
    if val is None:
        return None
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def _resolve_path(mapping: Mapping[str, Any], path: str) -> tuple[bool, list[Any]]:
    """Traverse dotted paths across nested mappings and lists-of-mappings.

    Returns:
        tuple[bool, list[Any]]:
            - exists: True if at least one non-None value was reachable along the path
            - values: list of resolved non-None leaf values
    """
    parts = path.split(".")
    current: list[Any] = [mapping]

    for part in parts:
        next_items: list[Any] = []
        for item in current:
            if isinstance(item, Mapping) and part in item:
                val = item[part]
                if isinstance(val, list):
                    next_items.extend(val)
                elif val is not None:
                    next_items.append(val)
            elif isinstance(item, list):
                for elem in item:
                    if isinstance(elem, Mapping) and part in elem:
                        val = elem[part]
                        if isinstance(val, list):
                            next_items.extend(val)
                        elif val is not None:
                            next_items.append(val)
        current = next_items
        if not current:
            return False, []

    non_none_values = [v for v in current if v is not None]
    if not non_none_values:
        return False, []
    return True, non_none_values


def extract_available_keys(
    devices: Sequence[Mapping[str, Any]], max_depth: int = 2
) -> set[str]:
    """Discover available field keys across a device population (up to 1 dotted level)."""
    keys: set[str] = set()
    for d in devices:
        for k, v in d.items():
            keys.add(k)
            if max_depth > 1:
                if isinstance(v, Mapping):
                    for sub_k in v:
                        keys.add(f"{k}.{sub_k}")
                elif isinstance(v, list):
                    for elem in v:
                        if isinstance(elem, Mapping):
                            for sub_k in elem:
                                keys.add(f"{k}.{sub_k}")
    return keys


@dataclass(frozen=True)
class DeviceFilter:
    """Parsed single device filter expression."""

    field: str
    operator: str
    value: str
    pattern: re.Pattern[str] | None = dataclasses.field(
        default=None, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        if self.operator not in VALID_OPERATORS:
            raise ValueError(
                f"Unsupported operator '{self.operator}'. Must be one of: {', '.join(VALID_OPERATORS)}"
            )
        if not self.field:
            raise ValueError("Filter field cannot be empty")
        if self.operator in ("=~", "!=~") and self.pattern is None:
            try:
                compiled = re.compile(self.value)
            except re.error as e:
                raise ValueError(
                    f"Invalid regular expression in filter '{self.field}{self.operator}{self.value}': {e}"
                ) from e
            object.__setattr__(self, "pattern", compiled)

    @classmethod
    def parse(cls, filter_str: str) -> "DeviceFilter":
        """Parse a filter string (e.g. 'role=spine', 'tags=~prod.*')."""
        trimmed = filter_str.strip()
        if not trimmed:
            raise ValueError("Filter expression cannot be empty")

        matched_op: str | None = None
        for op in VALID_OPERATORS:
            if op in trimmed:
                matched_op = op
                break

        if matched_op is None:
            raise ValueError(
                f"Invalid filter expression '{filter_str}'. Missing operator ({', '.join(VALID_OPERATORS)})"
            )

        parts = trimmed.split(matched_op, 1)
        field_name = parts[0].strip()
        val = parts[1].strip()

        if not field_name:
            raise ValueError(
                f"Invalid filter '{filter_str}': field name cannot be empty"
            )
        for seg in field_name.split("."):
            if not _IDENTIFIER_SEGMENT_RE.match(seg):
                raise ValueError(
                    f"Invalid filter '{filter_str}': field segment '{seg}' is not a valid identifier"
                )
        if not val:
            raise ValueError(f"Invalid filter '{filter_str}': value cannot be empty")

        return cls(field=field_name, operator=matched_op, value=val)

    def matches(self, mapping: Mapping[str, Any]) -> bool:
        """Predicate checking if mapping matches this filter."""
        exists, values = _resolve_path(mapping, self.field)
        if not exists:
            # Missing fields: positive operators never match, negative operators always match
            return self.operator in NEGATIVE_OPERATORS

        stringified = [_stringify(v) for v in values]
        stringified_non_none = [s for s in stringified if s is not None]

        if not stringified_non_none:
            return self.operator in NEGATIVE_OPERATORS

        if self.operator == "=":
            return any(s == self.value for s in stringified_non_none)
        if self.operator == "!=":
            return not any(s == self.value for s in stringified_non_none)
        if self.operator == "=~":
            assert self.pattern is not None
            return any(bool(self.pattern.search(s)) for s in stringified_non_none)
        if self.operator == "!=~":
            assert self.pattern is not None
            return not any(bool(self.pattern.search(s)) for s in stringified_non_none)

        return False

    def to_dict(self) -> dict[str, str]:
        """Convert filter to JSON-serializable dictionary."""
        return {"field": self.field, "operator": self.operator, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "DeviceFilter":
        """Instantiate filter from dictionary."""
        return cls(
            field=data["field"],
            operator=data["operator"],
            value=data["value"],
        )

    def __str__(self) -> str:
        return f"{self.field}{self.operator}{self.value}"


@dataclass
class FilterResult:
    """Result of applying multiple device filters to a device population."""

    matched: list[Any]
    count_before: int
    count_after: int
    unknown_fields: list[str]
    keys_seen: set[str]


def referenced_root_fields(filters: Sequence[DeviceFilter]) -> set[str]:
    """Return root field names referenced by filters (for lazy virtual field building)."""
    return {f.field.split(".")[0] for f in filters}


def check_repeated_positive_filters(filters: Sequence[DeviceFilter]) -> list[str]:
    """Check for repeated positive filters on the same field and return warning messages."""
    seen_positive: dict[str, list[DeviceFilter]] = {}
    for f in filters:
        if f.operator in POSITIVE_OPERATORS:
            seen_positive.setdefault(f.field, []).append(f)

    warnings: list[str] = []
    for field_name, flist in seen_positive.items():
        if len({str(f) for f in flist}) > 1:
            exprs = ", ".join(str(f) for f in flist)
            warnings.append(
                f"Multiple positive filters for field '{field_name}' detected ({exprs}). "
                f"Filters combine with AND logic, which may match no devices. "
                f"Use regex alternation (e.g. {field_name}=~'val1|val2') to match either value."
            )
    return warnings


def format_unknown_field_error(
    unknown_fields: Collection[str],
    keys_seen: Collection[str],
    max_display_keys: int = 40,
) -> str:
    """Format a self-documenting error message for unknown filter fields."""
    unknown_list = sorted(unknown_fields)
    fields_str = ", ".join(f"'{f}'" for f in unknown_list)
    sorted_keys = sorted(keys_seen)
    if len(sorted_keys) > max_display_keys:
        displayed_keys = sorted_keys[:max_display_keys]
        keys_str = (
            ", ".join(f"'{k}'" for k in displayed_keys)
            + f", ... ({len(sorted_keys) - max_display_keys} more)"
        )
    else:
        keys_str = ", ".join(f"'{k}'" for k in sorted_keys) if sorted_keys else "<none>"

    return (
        f"Device filter field(s) not found in data model: {fields_str}. "
        f"Available fields: {keys_str}"
    )


def apply_all(
    devices: Sequence[Mapping[str, Any]], filters: Sequence[DeviceFilter]
) -> FilterResult:
    """Apply all filters to device population using AND logic."""
    count_before = len(devices)
    keys_seen = extract_available_keys(devices)

    if not devices:
        return FilterResult(
            matched=[],
            count_before=0,
            count_after=0,
            unknown_fields=[],
            keys_seen=set(),
        )

    if not filters:
        return FilterResult(
            matched=list(devices),
            count_before=count_before,
            count_after=count_before,
            unknown_fields=[],
            keys_seen=keys_seen,
        )

    # Check unknown fields: a field is unknown if it never resolves on ANY device in the population
    unknown_fields: list[str] = []
    for f in filters:
        found = False
        for d in devices:
            exists, _ = _resolve_path(d, f.field)
            if exists:
                found = True
                break
        if not found:
            unknown_fields.append(f.field)

    matched = [d for d in devices if all(f.matches(d) for f in filters)]

    return FilterResult(
        matched=matched,
        count_before=count_before,
        count_after=len(matched),
        unknown_fields=unknown_fields,
        keys_seen=keys_seen,
    )


def filters_to_json(filters: Sequence[DeviceFilter]) -> str:
    """Serialize a list of DeviceFilter objects to JSON string."""
    return json.dumps([f.to_dict() for f in filters])


def filters_from_json(json_str: str) -> list[DeviceFilter]:
    """Deserialize a list of DeviceFilter objects from JSON string."""
    if not json_str or not json_str.strip():
        return []
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse device filter JSON: {e}")
        return []
    if not isinstance(data, list):
        return []
    result: list[DeviceFilter] = []
    for item in data:
        if isinstance(item, dict):
            try:
                result.append(DeviceFilter.from_dict(item))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"Skipping invalid device filter dict {item}: {e}")
        elif isinstance(item, str):
            try:
                result.append(DeviceFilter.parse(item))
            except ValueError as e:
                logger.warning(f"Skipping invalid device filter string '{item}': {e}")
    return result
