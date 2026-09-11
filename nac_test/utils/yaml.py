# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""YAML utilities providing PyYAML-compatible interface using ruamel.yaml.

This module provides `safe_load()` and `dump()` wrapper functions that offer
a PyYAML-compatible API while using ruamel.yaml underneath. This allows
nac-test to avoid depending on PyYAML, which is only available as a transitive
dependency of PyATS (and thus not installed on Windows).

By using `YAML(typ="safe", pure=False)`, the parser and emitter leverage C
extensions (`ruamel.yaml.clib`) when available for accelerated performance,
while seamlessly falling back to pure Python if the C library is not installed.

TODO: This functionality should be moved to the nac-yaml package.
See: https://github.com/netascode/nac-yaml/issues/41
"""

from io import StringIO
from typing import IO, Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError as YAMLError  # noqa: PLC0414 - re-export


def _create_yaml_dumper() -> YAML:
    """Create a safe YAML dumper with nac-test defaults.

    Uses C-accelerated emitter via `pure=False` if `ruamel.yaml.clib` is installed,
    falling back to pure Python otherwise.
    """
    yaml_dumper = YAML(typ="safe", pure=False)
    yaml_dumper.default_flow_style = False
    return yaml_dumper


def safe_load(stream: str | IO[str]) -> Any:
    """Load YAML safely from a string or file-like object.

    This is equivalent to PyYAML's `yaml.safe_load()` function. Uses C-accelerated
    parser via `pure=False` if `ruamel.yaml.clib` is installed, falling back to
    pure Python otherwise.

    Args:
        stream: A YAML string or file-like object to parse.

    Returns:
        The parsed YAML data structure (dict, list, or scalar).
        Returns None for empty documents.

    Example:
        >>> safe_load("key: value")
        {'key': 'value'}
        >>> safe_load("- item1\\n- item2")
        ['item1', 'item2']
    """
    y = YAML(typ="safe", pure=False)
    if isinstance(stream, str):
        stream = StringIO(stream)
    return y.load(stream)


def dump(data: Any) -> str:
    """Dump data to YAML string.

    Args:
        data: The data structure to serialize (dict, list, or scalar).

    Returns:
        The YAML string.

    Example:
        >>> dump({"key": "value"})
        'key: value\\n'
    """
    y = _create_yaml_dumper()
    s = StringIO()
    y.dump(data, s)
    return s.getvalue()


# Separate function for stream output to avoid complex @overload type hints
# that would be needed if dump() accepted an optional stream parameter
def dump_to_stream(data: Any, stream: IO[str]) -> None:
    """Dump data to YAML stream.

    Args:
        data: The data structure to serialize (dict, list, or scalar).
        stream: File-like object to write to.

    Example:
        >>> with open("output.yaml", "w") as f:
        ...     dump_to_stream({"key": "value"}, f)
    """
    y = _create_yaml_dumper()
    y.dump(data, stream)
