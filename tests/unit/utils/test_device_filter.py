# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2026 Cisco Systems, Inc.

"""Unit tests for device filter parsing, matching, and validation."""

from collections import ChainMap
from typing import Any

import pytest

from nac_test.cli.validators.args import validate_device_filter
from nac_test.utils.device_filter import (
    DeviceFilter,
    _resolve_path,
    _stringify,
    apply_all,
    check_repeated_positive_filters,
    extract_available_keys,
    filters_from_json,
    filters_to_json,
    format_unknown_field_error,
    referenced_root_fields,
)


class TestDeviceFilterParse:
    """Tests for DeviceFilter.parse()."""

    @pytest.mark.parametrize(
        "spec,expected_field,expected_op,expected_value",
        [
            ("hostname=leaf1", "hostname", "=", "leaf1"),
            ("role=spine", "role", "=", "spine"),
            ("bgp.asn=65001", "bgp.asn", "=", "65001"),
            ("hostname=~leaf.*", "hostname", "=~", "leaf.*"),
            ("role=~^(spine|leaf)$", "role", "=~", "^(spine|leaf)$"),
            ("role!=access", "role", "!=", "access"),
            ("hostname!=~^core", "hostname", "!=~", "^core"),
            ("site.code=~(?i)sjc", "site.code", "=~", "(?i)sjc"),
            ("  hostname = leaf1  ", "hostname", "=", "leaf1"),
            ("tag=value=with=equals", "tag", "=", "value=with=equals"),
            ("custom.field_name=123", "custom.field_name", "=", "123"),
        ],
    )
    def test_parse_valid_filters(
        self, spec: str, expected_field: str, expected_op: str, expected_value: str
    ) -> None:
        """Test parsing valid filter specifications."""
        f = DeviceFilter.parse(spec)
        assert f.field == expected_field
        assert f.operator == expected_op
        assert f.value == expected_value
        if f.operator in ("=~", "!=~"):
            assert f.pattern is not None

    @pytest.mark.parametrize(
        "invalid_spec",
        [
            "",
            "   ",
            "hostname",
            "hostname>",
            "hostname<5",
            "=leaf1",
            "!=leaf1",
            "=~regex",
            "123invalid=foo",
            "invalid key=foo",
            "invalid-key=foo",
            "hostname=~[unclosed",
            "hostname!=~*invalid_regex",
        ],
    )
    def test_parse_invalid_filters_raises(self, invalid_spec: str) -> None:
        """Test that invalid filter specifications raise ValueError."""
        with pytest.raises(ValueError):
            DeviceFilter.parse(invalid_spec)

    def test_str_representation(self) -> None:
        """Test string representation of DeviceFilter."""
        assert str(DeviceFilter.parse("hostname=leaf1")) == "hostname=leaf1"
        assert str(DeviceFilter.parse("hostname=~leaf.*")) == "hostname=~leaf.*"
        assert str(DeviceFilter.parse("role!=spine")) == "role!=spine"
        assert str(DeviceFilter.parse("role!=~^core")) == "role!=~^core"


class TestDeviceFilterMatches:
    """Tests for DeviceFilter.matches()."""

    def test_exact_match_string(self) -> None:
        f = DeviceFilter.parse("hostname=leaf1")
        assert f.matches({"hostname": "leaf1"})
        assert not f.matches({"hostname": "leaf2"})

    def test_exact_match_int_coercion(self) -> None:
        f = DeviceFilter.parse("asn=65001")
        assert f.matches({"asn": 65001})
        assert f.matches({"asn": "65001"})
        assert not f.matches({"asn": 65002})

    def test_exact_match_bool(self) -> None:
        f = DeviceFilter.parse("managed=true")
        assert f.matches({"managed": True})
        assert f.matches({"managed": "true"})
        assert not f.matches({"managed": False})

    def test_regex_match(self) -> None:
        f = DeviceFilter.parse("hostname=~^leaf[1-3]$")
        assert f.matches({"hostname": "leaf1"})
        assert f.matches({"hostname": "leaf2"})
        assert f.matches({"hostname": "leaf3"})
        assert not f.matches({"hostname": "leaf4"})
        assert not f.matches({"hostname": "spine1"})

    def test_regex_match_case_insensitive(self) -> None:
        f = DeviceFilter.parse("site=~(?i)sjc")
        assert f.matches({"site": "SJC"})
        assert f.matches({"site": "sjc1"})
        assert not f.matches({"site": "FRA"})

    def test_not_equal_match(self) -> None:
        f = DeviceFilter.parse("role!=spine")
        assert f.matches({"role": "leaf"})
        assert not f.matches({"role": "spine"})
        # Missing key should match != filter
        assert f.matches({})

    def test_not_regex_match(self) -> None:
        f = DeviceFilter.parse("hostname!=~^core")
        assert f.matches({"hostname": "leaf1"})
        assert not f.matches({"hostname": "core1"})
        # Missing key should match !=~ filter
        assert f.matches({})

    def test_nested_path_resolution(self) -> None:
        f = DeviceFilter.parse("bgp.asn=65001")
        device = {"bgp": {"asn": 65001}}
        assert f.matches(device)
        assert not f.matches({"bgp": {"asn": 65002}})
        assert not f.matches({"bgp": "not-a-dict"})
        assert not f.matches({})

    def test_chainmap_resolution(self) -> None:
        f1 = DeviceFilter.parse("hostname=leaf1")
        f2 = DeviceFilter.parse("role=spine")
        cm: ChainMap[str, Any] = ChainMap(
            {"hostname": "leaf1"}, {"role": "spine", "raw": 123}
        )
        assert f1.matches(cm)
        assert f2.matches(cm)

    def test_missing_field_positive_filter(self) -> None:
        f = DeviceFilter.parse("custom_tag=prod")
        assert not f.matches({"hostname": "leaf1"})

    def test_none_value_handling(self) -> None:
        f = DeviceFilter.parse("description=None")
        assert not f.matches({"description": None})
        f2 = DeviceFilter.parse("description!=active")
        assert f2.matches({"description": None})


class TestHelpers:
    """Tests for helper functions in device_filter module."""

    def test_resolve_path(self) -> None:
        data = {"a": {"b": {"c": "val"}}}
        found, val = _resolve_path(data, "a.b.c")
        assert found is True
        assert val == ["val"]

        found, _ = _resolve_path(data, "a.b.d")
        assert found is False

        found, _ = _resolve_path(data, "a.x.c")
        assert found is False

    def test_stringify(self) -> None:
        assert _stringify(None) is None
        assert _stringify(True) == "true"
        assert _stringify(False) == "false"
        assert _stringify(123) == "123"
        assert _stringify("abc") == "abc"
        assert _stringify(["a", "b"]) == "['a', 'b']"

    def test_extract_available_keys(self) -> None:
        devices: list[dict[str, Any] | ChainMap[str, Any]] = [
            {"hostname": "leaf1", "bgp": {"asn": 65001}},
            {"hostname": "leaf2", "role": "spine"},
            ChainMap({"virtual_host": "1.1.1.1"}, {"raw_key": "val"}),
        ]
        keys = extract_available_keys(devices)
        assert "hostname" in keys
        assert "bgp.asn" in keys
        assert "role" in keys
        assert "virtual_host" in keys
        assert "raw_key" in keys

    def test_referenced_root_fields(self) -> None:
        filters = [
            DeviceFilter.parse("hostname=leaf1"),
            DeviceFilter.parse("bgp.asn=65001"),
            DeviceFilter.parse("role!=spine"),
        ]
        roots = referenced_root_fields(filters)
        assert roots == {"hostname", "bgp", "role"}


class TestApplyAll:
    """Tests for apply_all()."""

    def test_apply_all_basic(self) -> None:
        devices = [
            {"hostname": "leaf1", "role": "leaf", "site": "sjc"},
            {"hostname": "leaf2", "role": "leaf", "site": "fra"},
            {"hostname": "spine1", "role": "spine", "site": "sjc"},
        ]
        filters = [
            DeviceFilter.parse("role=leaf"),
            DeviceFilter.parse("site=sjc"),
        ]
        result = apply_all(devices, filters)
        assert len(result.matched) == 1
        assert result.matched[0]["hostname"] == "leaf1"
        assert result.count_before == 3
        assert result.count_after == 1
        assert result.unknown_fields == []

    def test_apply_all_unknown_fields(self) -> None:
        devices = [
            {"hostname": "leaf1", "role": "leaf"},
            {"hostname": "leaf2", "role": "leaf"},
        ]
        filters = [
            DeviceFilter.parse("role=leaf"),
            DeviceFilter.parse("nonexistent_field=foo"),
        ]
        result = apply_all(devices, filters)
        assert result.unknown_fields == ["nonexistent_field"]
        assert len(result.matched) == 0
        assert result.count_after == 0

    def test_apply_all_empty_devices(self) -> None:
        filters = [DeviceFilter.parse("role=leaf")]
        result = apply_all([], filters)
        assert result.matched == []
        assert result.count_before == 0
        assert result.count_after == 0
        assert result.unknown_fields == []


class TestCheckRepeatedPositiveFilters:
    """Tests for check_repeated_positive_filters()."""

    def test_detects_repeated_conflicting_positive_filters(self) -> None:
        filters = [
            DeviceFilter.parse("role=leaf"),
            DeviceFilter.parse("role=spine"),
        ]
        warnings = check_repeated_positive_filters(filters)
        assert len(warnings) == 1
        assert "role" in warnings[0]
        assert "leaf" in warnings[0] and "spine" in warnings[0]

    def test_no_warning_for_identical_positive_filters(self) -> None:
        filters = [
            DeviceFilter.parse("role=leaf"),
            DeviceFilter.parse("role=leaf"),
        ]
        warnings = check_repeated_positive_filters(filters)
        assert len(warnings) == 0

    def test_no_warning_for_repeated_negative_filters(self) -> None:
        filters = [
            DeviceFilter.parse("role!=leaf"),
            DeviceFilter.parse("role!=spine"),
        ]
        warnings = check_repeated_positive_filters(filters)
        assert len(warnings) == 0


class TestFormatUnknownFieldError:
    """Tests for format_unknown_field_error()."""

    def test_format_error(self) -> None:
        msg = format_unknown_field_error(
            ["unknown1", "unknown2"], ["hostname", "role", "ip"]
        )
        assert "unknown1" in msg
        assert "unknown2" in msg
        assert "hostname" in msg
        assert "252" in msg or "EXIT_DATA_ERROR" in msg or "unknown" in msg.lower()


class TestJsonSerialization:
    """Tests for filters_to_json and filters_from_json."""

    def test_round_trip(self) -> None:
        original = [
            DeviceFilter.parse("hostname=leaf1"),
            DeviceFilter.parse("role=~spine.*"),
            DeviceFilter.parse("bgp.asn!=65000"),
            DeviceFilter.parse("site!=~^backup"),
        ]
        json_str = filters_to_json(original)
        reconstructed = filters_from_json(json_str)
        assert len(reconstructed) == len(original)
        for orig, recon in zip(original, reconstructed, strict=False):
            assert orig.field == recon.field
            assert orig.operator == recon.operator
            assert orig.value == recon.value

    def test_from_empty_or_invalid_json(self) -> None:
        assert filters_from_json("") == []
        assert filters_from_json("[]") == []
        assert filters_from_json("invalid json") == []


class TestCliValidator:
    """Tests for validate_device_filter()."""

    def test_valid_list(self) -> None:
        res = validate_device_filter(["hostname=leaf1", "role=spine"])
        assert res == ["hostname=leaf1", "role=spine"]

    def test_invalid_item_raises(self) -> None:
        import typer

        with pytest.raises(typer.BadParameter):
            validate_device_filter(["hostname=leaf1", "invalid_filter"])

    def test_none_or_empty(self) -> None:
        assert validate_device_filter(None) is None
        assert validate_device_filter([]) == []
