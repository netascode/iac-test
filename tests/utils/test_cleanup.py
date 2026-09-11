# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for cleanup utilities."""

import logging
from pathlib import Path

import pytest
from _pytest.logging import LogCaptureFixture
from pytest_mock import MockerFixture

from nac_test.core.constants import PYATS_RESULTS_DIRNAME
from nac_test.pyats_core.constants import VALID_TEST_TYPES
from nac_test.utils.cleanup import cleanup_stale_test_artifacts


class TestCleanupStaleTestArtifacts:
    """Tests for cleanup_stale_test_artifacts function."""

    def test_removes_test_type_directories(self, tmp_path: Path) -> None:
        """Test that api/, d2d/, default/ directories are removed."""
        test_type_dirs = (*VALID_TEST_TYPES, "default")
        for test_type in test_type_dirs:
            test_type_dir = tmp_path / test_type
            test_type_dir.mkdir()
            (test_type_dir / "html_report_data_temp").mkdir()
            (test_type_dir / "html_report_data_temp" / "stale.jsonl").touch()

        cleanup_stale_test_artifacts(tmp_path)

        for test_type in test_type_dirs:
            assert not (tmp_path / test_type).exists()

    @pytest.mark.parametrize(
        "preserved_path,is_file",
        [
            ("nac_test_job_api_20250224.zip", True),
            ("nac_test_job_d2d_20250224.zip", True),
            (PYATS_RESULTS_DIRNAME, False),
            ("robot_results", False),
            ("merged_data_model.json", True),
        ],
    )
    def test_preserves_expected_paths(
        self, tmp_path: Path, preserved_path: str, is_file: bool
    ) -> None:
        """Test that archives, pyats_results, robot_results, and other files are preserved."""
        path = tmp_path / preserved_path
        if is_file:
            path.touch()
        else:
            path.mkdir()
            (path / "content.txt").touch()

        (tmp_path / "api").mkdir()

        cleanup_stale_test_artifacts(tmp_path)

        assert path.exists()
        assert not (tmp_path / "api").exists()

    def test_handles_nonexistent_directory(self) -> None:
        """Test that nonexistent directory is handled gracefully."""
        cleanup_stale_test_artifacts(Path("/nonexistent/path"))

    def test_handles_empty_directory(self, tmp_path: Path) -> None:
        """Test that empty directory is handled gracefully."""
        cleanup_stale_test_artifacts(tmp_path)
        assert tmp_path.exists()

    def test_logs_warning_when_directory_persists(
        self, tmp_path: Path, mocker: MockerFixture, caplog: LogCaptureFixture
    ) -> None:
        """Test that warning is logged when rmtree fails to remove directory."""
        # Create a directory to clean
        api_dir = tmp_path / "api"
        api_dir.mkdir()
        (api_dir / "test_file.txt").touch()

        # Mock rmtree to succeed but mock exists check to show directory persisted
        mocker.patch("nac_test.utils.cleanup.shutil.rmtree")
        mocker.patch("nac_test.utils.cleanup.Path.exists", return_value=True)

        cleanup_stale_test_artifacts(tmp_path)

        # Verify warning was logged about failed removal
        assert any(
            "Failed to remove stale directory" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )
        # Verify count was NOT incremented (no INFO log about cleanup)
        assert not any(
            "Cleaned up" in record.message
            for record in caplog.records
            if record.levelname == "INFO"
        )

    def test_count_only_incremented_on_successful_removal(
        self, tmp_path: Path, mocker: MockerFixture, caplog: LogCaptureFixture
    ) -> None:
        """Test that removal count is only incremented when directory actually removed."""
        caplog.set_level(logging.INFO)
        api_dir = tmp_path / "api"
        d2d_dir = tmp_path / "d2d"
        default_dir = tmp_path / "default"
        api_dir.mkdir()
        d2d_dir.mkdir()
        default_dir.mkdir()

        real_exists = Path.exists

        def exists_side_effect(self: Path) -> bool:
            # Simulate d2d persisting after rmtree (silent failure)
            if self == d2d_dir:
                return True
            return real_exists(self)

        mocker.patch("nac_test.utils.cleanup.Path.exists", exists_side_effect)

        cleanup_stale_test_artifacts(tmp_path)

        info_logs = [
            record.message for record in caplog.records if record.levelname == "INFO"
        ]
        assert any(
            "Cleaned up 2 stale test artifact director" in msg for msg in info_logs
        )
        warning_logs = [
            record.message for record in caplog.records if record.levelname == "WARNING"
        ]
        assert any("Failed to remove stale directory" in msg for msg in warning_logs)
