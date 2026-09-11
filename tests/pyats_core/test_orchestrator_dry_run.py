# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for PyATSOrchestrator dry-run functionality."""

from pathlib import Path
from unittest.mock import patch

import pytest

from nac_test.core.constants import DRY_RUN_REASON
from nac_test.pyats_core.common.types import (
    PyatsDiscoveryResult,
    TestFileMetadata,
)
from nac_test.pyats_core.orchestrator import PyATSOrchestrator
from tests.conftest import PyATSTestDirs


def _make_execution_plan(
    api_paths: list[Path], d2d_paths: list[Path]
) -> PyatsDiscoveryResult:
    """Create a PyatsDiscoveryResult from path lists for test mocking."""
    api_tests = [TestFileMetadata(path=p, test_type="api") for p in api_paths]
    d2d_tests = [TestFileMetadata(path=p, test_type="d2d") for p in d2d_paths]
    return PyatsDiscoveryResult(
        api_tests=api_tests,
        d2d_tests=d2d_tests,
        filtered_by_tags=0,
    )


class TestOrchestratorDryRun:
    """Tests for PyATSOrchestrator dry_run parameter and behavior."""

    def test_dry_run_prints_summary_and_skips_execution(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test that dry_run mode prints test summary and skips execution."""
        # Create mock test files
        api_test = pyats_test_dirs.test_dir / "test_api.py"
        api_test.write_text("# API test")

        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            dry_run=True,
        )

        mock_plan = _make_execution_plan([api_test], [])

        with (
            patch.object(
                orchestrator.test_discovery, "discover_pyats_tests"
            ) as mock_discover,
        ):
            mock_discover.return_value = mock_plan
            result = orchestrator.run_tests()

        # Verify dry-run output
        captured = capsys.readouterr()
        assert "DRY-RUN MODE" in captured.out
        assert "test_api.py" in captured.out
        assert "dry-run complete" in captured.out

        # Verify results indicate not_run
        assert result.api is not None
        assert result.api.reason == DRY_RUN_REASON
        assert result.d2d is None  # No D2D tests

    def test_dry_run_returns_not_run_results_for_api_and_d2d(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Test that dry_run returns not_run results for both API and D2D tests."""
        d2d_dir = pyats_test_dirs.test_dir / "d2d"
        d2d_dir.mkdir()

        # Create mock test files
        api_test = pyats_test_dirs.test_dir / "test_api.py"
        api_test.write_text("# API test")
        d2d_test = d2d_dir / "test_d2d.py"
        d2d_test.write_text("# D2D test")

        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            dry_run=True,
        )

        mock_plan = _make_execution_plan([api_test], [d2d_test])

        with (
            patch.object(
                orchestrator.test_discovery, "discover_pyats_tests"
            ) as mock_discover,
        ):
            mock_discover.return_value = mock_plan
            result = orchestrator.run_tests()

        # Both API and D2D should have not_run results
        assert result.api is not None
        assert result.api.reason == DRY_RUN_REASON
        assert result.d2d is not None
        assert result.d2d.reason == DRY_RUN_REASON

    def test_dry_run_does_not_execute_tests(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Test that dry_run mode does not execute tests."""
        api_test = pyats_test_dirs.test_dir / "test_api.py"
        api_test.write_text("# API test")

        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            dry_run=True,
        )

        mock_plan = _make_execution_plan([api_test], [])

        with (
            patch.object(
                orchestrator.test_discovery, "discover_pyats_tests"
            ) as mock_discover,
            patch.object(orchestrator, "_execute_api_tests_standard") as mock_execute,
        ):
            mock_discover.return_value = mock_plan
            orchestrator.run_tests()

        mock_execute.assert_not_called()

    def test_dry_run_empty_test_lists(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test dry_run with no tests discovered returns empty PyATSResults."""
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            dry_run=True,
        )

        mock_plan = _make_execution_plan([], [])

        with (
            patch.object(
                orchestrator.test_discovery, "discover_pyats_tests"
            ) as mock_discover,
        ):
            mock_discover.return_value = mock_plan
            result = orchestrator.run_tests()

        # Should return empty results (no tests found message)
        captured = capsys.readouterr()
        assert "No PyATS test files" in captured.out
        assert result.api is None
        assert result.d2d is None
