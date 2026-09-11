# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Test base_test.py result collector initialization."""

from pathlib import Path
from unittest.mock import patch

import pytest
from pyats import aetest

from nac_test.pyats_core.common.base_test import NACTestBase


class TestResultCollectorInitialization:
    """Test _initialize_result_collector behavior in NACTestBase."""

    def test_falls_back_to_cwd_when_data_file_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Test that result collector uses cwd when data file path is invalid."""
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "password")

        # Point to non-existent file to trigger fallback
        monkeypatch.setenv(
            "MERGED_DATA_MODEL_TEST_VARIABLES_FILEPATH", "/nonexistent/path.json"
        )

        class TestClass(NACTestBase):
            @aetest.test  # type: ignore[misc]
            def test_method(self) -> None:
                pass

        test_instance = TestClass()

        # Change cwd to tmp_path so artifacts go there instead of repo root
        monkeypatch.chdir(tmp_path)

        with patch.object(
            test_instance, "load_data_model", return_value={"test": "data"}
        ):
            test_instance.setup()

        assert test_instance.output_dir == Path(".")
        expected_dir = tmp_path / "default" / "html_report_data_temp"
        assert expected_dir.exists()
