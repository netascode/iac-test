# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for TestMetadataResolver.

This module tests the TestMetadataResolver class which extracts metadata from
PyATS test files: test type (API vs D2D) and groups (for tag-based filtering).

Test Structure:
    - TestStaticAnalysisDetection: Tests AST-based base class detection using
      real fixture files from tests/fixtures/
    - TestDirectoryFallback: Tests directory path-based fallback detection
    - TestDefaultBehavior: Tests default classification with warnings
    - TestErrorHandling: Tests various error conditions
    - TestIntegration: Integration tests with real test scenarios

Groups extraction tests are in test_groups_extraction.py.
"""

import logging
from unittest.mock import patch

import pytest

from nac_test.pyats_core.common.types import DEFAULT_TEST_TYPE
from nac_test.pyats_core.discovery.test_type_resolver import (
    BASE_CLASS_MAPPING,
    NoRecognizedBaseError,
    TestMetadataResolver,
)

from .conftest import FIXTURES_DIR, create_mock_path


class TestStaticAnalysisDetection:
    """Test AST-based detection of test types from base class inheritance.

    Uses real fixture files from tests/fixtures/ to validate the full
    file-reading and AST-parsing pipeline.
    """

    @pytest.mark.parametrize(
        ("fixture_path", "expected_type"),
        [
            ("api/test_api_simple.py", "api"),
            ("api/test_api_multiple_inheritance.py", "api"),
            ("api/test_api_attribute.py", "api"),
            ("api/test_api_multiline.py", "api"),
            ("d2d/test_d2d_simple.py", "d2d"),
            ("d2d/test_d2d_multiple_inheritance.py", "d2d"),
            ("d2d/test_d2d_multiline.py", "d2d"),
            ("edge_cases/test_nested_classes.py", "api"),
            ("edge_cases/test_comments_and_strings.py", "d2d"),
        ],
    )
    def test_base_class_detection(self, fixture_path: str, expected_type: str) -> None:
        """Test detection of test type from base class inheritance."""
        test_file = FIXTURES_DIR / fixture_path

        result = TestMetadataResolver.resolve(test_file)

        assert result.test_type == expected_type

    def test_import_alias_not_detected(self) -> None:
        """Test that import aliases fall back to default with warning."""
        test_file = FIXTURES_DIR / "edge_cases" / "test_import_alias.py"

        with patch("logging.Logger.warning") as mock_warning:
            result = TestMetadataResolver.resolve(test_file)

        assert result.test_type == "api"  # Default
        mock_warning.assert_called_once()
        assert "Could not detect test type" in str(mock_warning.call_args)

    def test_all_known_api_bases_in_mapping(self) -> None:
        """Verify BASE_CLASS_MAPPING includes expected API base classes."""
        api_bases = {
            base for base, test_type in BASE_CLASS_MAPPING.items() if test_type == "api"
        }
        assert {"NACTestBase", "APICTestBase", "CatalystCenterTestBase"} <= api_bases

    def test_all_known_d2d_bases_in_mapping(self) -> None:
        """Verify BASE_CLASS_MAPPING includes expected D2D base classes."""
        d2d_bases = {
            base for base, test_type in BASE_CLASS_MAPPING.items() if test_type == "d2d"
        }
        assert {"SSHTestBase", "IOSXETestBase"} <= d2d_bases


class TestDirectoryFallback:
    """Test directory-based fallback detection."""

    @pytest.mark.parametrize(
        ("fixture_path", "expected_type"),
        [
            ("directory_fallback/d2d/test_custom_base.py", "d2d"),
            ("directory_fallback/api/test_unknown_base.py", "api"),
        ],
    )
    def test_directory_fallback_with_fixtures(
        self, fixture_path: str, expected_type: str
    ) -> None:
        """Test fallback to directory-based detection using fixture files."""
        test_file = FIXTURES_DIR / fixture_path

        result = TestMetadataResolver.resolve(test_file)

        assert result.test_type == expected_type

    @pytest.mark.parametrize(
        ("mock_path_str", "content", "expected_type"),
        [
            ("/tests/D2D/test_file.py", "class Test: pass", "api"),
            ("/project/tests/feature/d2d/verify.py", "class Test: pass", "d2d"),
            ("/tests/api/test.py", "class Test(SSHTestBase): pass", "d2d"),
        ],
    )
    def test_directory_detection_with_mocks(
        self, mock_path_str: str, content: str, expected_type: str
    ) -> None:
        """Test directory detection behavior with mock paths."""
        mock_path = create_mock_path(mock_path_str, content)

        result = TestMetadataResolver.resolve(mock_path)

        assert result.test_type == expected_type


class TestDefaultBehavior:
    """Test default behavior when detection fails."""

    def test_defaults_to_api_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that unknown tests default to API with warning."""
        mock_path = create_mock_path(
            "/tests/random/test_file.py", "class Test(UnknownBase): pass"
        )

        with caplog.at_level(logging.WARNING):
            result = TestMetadataResolver.resolve(mock_path)

        assert result.test_type == "api"
        assert "Could not detect test type" in caplog.text
        assert "Assuming 'api'" in caplog.text

    @pytest.mark.parametrize(
        ("fixture_path",),
        [
            ("edge_cases/test_empty_file.py",),
            ("edge_cases/test_no_base_class.py",),
        ],
    )
    def test_fixture_defaults_to_api(
        self, fixture_path: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that fixture files without recognized bases default to API."""
        test_file = FIXTURES_DIR / fixture_path

        with caplog.at_level(logging.WARNING):
            result = TestMetadataResolver.resolve(test_file)

        assert result.test_type == "api"
        assert "Could not detect test type" in caplog.text

    def test_default_type_constant(self) -> None:
        assert DEFAULT_TEST_TYPE == "api"


class TestErrorHandling:
    """Test error cases and exception handling."""

    def test_syntax_error_falls_back(self, caplog: pytest.LogCaptureFixture) -> None:
        test_file = FIXTURES_DIR / "edge_cases" / "test_syntax_error.py"

        with caplog.at_level(logging.WARNING):
            result = TestMetadataResolver.resolve(test_file)

        assert result.test_type == "api"
        assert "Failed to parse" in caplog.text

    def test_file_not_found_error(self) -> None:
        test_file = FIXTURES_DIR / "nonexistent" / "test_missing.py"

        with pytest.raises(OSError):
            TestMetadataResolver._extract_metadata(test_file)

    def test_mixed_api_and_d2d_returns_first(self) -> None:
        test_file = FIXTURES_DIR / "edge_cases" / "test_mixed_invalid.py"

        result = TestMetadataResolver.resolve(test_file)

        assert result.test_type == "api"

    def test_no_recognized_base_exception(self) -> None:
        exc = NoRecognizedBaseError("test.py", ["CustomBase", "AnotherBase"])
        assert "test.py" in str(exc)
        assert "CustomBase" in str(exc)
        assert exc.filename == "test.py"
        assert exc.found_bases == ["CustomBase", "AnotherBase"]

        exc2 = NoRecognizedBaseError("empty.py")
        assert "No base classes found" in str(exc2)
        assert exc2.found_bases == []

    def test_unicode_in_file_path(self) -> None:
        mock_path = create_mock_path(
            "/tests/тесты/test_файл.py", "class TestCase(NACTestBase): pass"
        )

        result = TestMetadataResolver.resolve(mock_path)

        assert result.test_type == "api"

    def test_permission_denied_error(self, caplog: pytest.LogCaptureFixture) -> None:
        mock_path = create_mock_path("/tests/test.py", "")
        mock_path.read_text.side_effect = PermissionError("Permission denied")

        with caplog.at_level(logging.WARNING):
            result = TestMetadataResolver.resolve(mock_path)

        assert result.test_type == "api"
        assert "Failed to parse" in caplog.text or "Could not detect" in caplog.text


class TestIntegration:
    """Integration tests with real fixture files."""

    def test_all_fixture_files_resolve(self) -> None:
        errors = []

        for py_file in FIXTURES_DIR.rglob("*.py"):
            try:
                metadata = TestMetadataResolver.resolve(py_file)
                assert metadata.test_type in {"api", "d2d"}
            except Exception as e:
                errors.append(f"{py_file}: {e}")

        assert len(errors) == 0, f"Errors resolving fixtures: {errors}"

    def test_logging_output(self, caplog: pytest.LogCaptureFixture) -> None:
        test_file = FIXTURES_DIR / "api" / "test_api_simple.py"

        with caplog.at_level(logging.DEBUG):
            TestMetadataResolver.resolve(test_file)
        assert "Analyzing AST" in caplog.text

    def test_base_class_mapping_completeness(self) -> None:
        expected_api = {
            "NACTestBase",
            "APICTestBase",
            "SDWANManagerTestBase",
            "CatalystCenterTestBase",
            "MerakiTestBase",
            "FMCTestBase",
            "ISETestBase",
        }
        expected_d2d = {
            "SSHTestBase",
            "SDWANTestBase",
            "IOSXETestBase",
            "NXOSTestBase",
            "FTDTestBase",
            "IOSTestBase",
        }

        for base in expected_api:
            assert BASE_CLASS_MAPPING.get(base) == "api", f"{base} should be 'api'"
        for base in expected_d2d:
            assert BASE_CLASS_MAPPING.get(base) == "d2d", f"{base} should be 'd2d'"

        for test_type in BASE_CLASS_MAPPING.values():
            assert test_type in {"api", "d2d"}
