# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for groups attribute extraction from test classes."""

import pytest

from nac_test.pyats_core.discovery.test_type_resolver import TestMetadataResolver

from .conftest import create_mock_path


class TestGroupsExtraction:
    """Test groups attribute extraction from test classes."""

    @pytest.mark.parametrize(
        ("content", "expected_type", "expected_groups"),
        [
            # Simple groups list (API)
            (
                "class Test(NACTestBase):\n    groups = ['health', 'bgp']",
                "api",
                ["health", "bgp"],
            ),
            # Groups from D2D test
            (
                "class Test(SSHTestBase):\n    groups = ['nrfu', 'ospf']",
                "d2d",
                ["nrfu", "ospf"],
            ),
            # Annotated groups (groups: list[str] = [...])
            (
                "class Test(NACTestBase):\n    groups: list[str] = ['health']",
                "api",
                ["health"],
            ),
        ],
    )
    def test_valid_groups_extraction(
        self, content: str, expected_type: str, expected_groups: list[str]
    ) -> None:
        """Test extraction of valid groups from various formats."""
        mock_path = create_mock_path("/tests/test_file.py", content)

        metadata = TestMetadataResolver.resolve(mock_path)

        assert metadata.test_type == expected_type
        assert metadata.groups == expected_groups

    def test_no_groups_returns_empty_list(self) -> None:
        """Test that tests without groups attribute return empty list."""
        mock_path = create_mock_path(
            "/tests/test_file.py", "class Test(NACTestBase):\n    pass"
        )

        metadata = TestMetadataResolver.resolve(mock_path)

        assert metadata.groups == []

    @pytest.mark.parametrize(
        ("content", "expected_groups"),
        [
            # Non-list value ignored
            ("class Test(NACTestBase):\n    groups = 'not_a_list'", []),
            # Non-string elements filtered out
            (
                "class Test(NACTestBase):\n    groups = ['valid', 123, 'another']",
                ["valid", "another"],
            ),
        ],
    )
    def test_invalid_groups_handling(
        self, content: str, expected_groups: list[str]
    ) -> None:
        """Test that invalid groups values are handled gracefully."""
        mock_path = create_mock_path("/tests/test_file.py", content)

        metadata = TestMetadataResolver.resolve(mock_path)

        assert metadata.groups == expected_groups

    def test_registered_base_class_exposes_groups_for_tag_filtering(self) -> None:
        """Groups must be readable for every base class in BASE_CLASS_MAPPING.

        Groups are deliberately dropped when the base class is unrecognized (see
        test_unrecognized_base_class_ignores_groups). The consequence is that an
        adapter base class missing from BASE_CLASS_MAPPING makes its tests
        invisible to --include/--exclude without any error: the tests still run
        unfiltered, so the omission only surfaces as a tag that silently matches
        nothing. This asserts the D2D device adapters are registered so their
        tags work.
        """
        for base in ("SSHTestBase", "NXOSTestBase", "FTDTestBase", "IOSXETestBase"):
            mock_path = create_mock_path(
                f"/tests/d2d/test_{base.lower()}.py",
                f"class Test({base}):\n    groups = ['minimal']",
            )

            metadata = TestMetadataResolver.resolve(mock_path)

            assert metadata.test_type == "d2d", f"{base} should resolve as d2d"
            assert metadata.groups == ["minimal"], (
                f"{base} is missing from BASE_CLASS_MAPPING, so its groups are "
                f"dropped and tag filtering silently excludes its tests"
            )

    def test_unrecognized_base_class_ignores_groups(self) -> None:
        """Test that groups are ignored when base class is unrecognized."""
        mock_path = create_mock_path(
            "/tests/random/test_file.py",
            "class Test(UnknownBase):\n    groups = ['custom', 'tags']",
        )

        metadata = TestMetadataResolver.resolve(mock_path)

        assert metadata.test_type == "api"  # Falls back to default
        assert metadata.groups == []  # Groups ignored for unrecognized base

    def test_resolve_returns_metadata_with_groups(self) -> None:
        """Test that resolve returns TestFileMetadata with groups attribute."""
        mock_path = create_mock_path(
            "/tests/test_file.py",
            "class Test(NACTestBase):\n    groups = ['health', 'bgp']",
        )

        result = TestMetadataResolver.resolve(mock_path)

        assert result.groups == ["health", "bgp"]
