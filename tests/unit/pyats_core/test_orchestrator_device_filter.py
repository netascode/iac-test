# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2026 Cisco Systems, Inc.

"""Unit tests for PyATSOrchestrator device filter handling and diagnostics."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nac_test.pyats_core.orchestrator import PyATSOrchestrator

from ..conftest import PyATSTestDirs


class TestOrchestratorDeviceFilter:
    """Unit tests for device filter handling in PyATSOrchestrator."""

    def test_filter_zero_matches_returns_empty_results(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When device filter excludes all devices, orchestrator returns empty PyATSResults."""
        d2d_test_paths = [Path("/fake/tests/d2d/test_one.py")]
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            device_filters=["hostname=nonexistent"],
        )

        mock_discovery_result = MagicMock()
        mock_discovery_result.total_count = 1
        mock_discovery_result.api_tests = []
        mock_discovery_result.d2d_tests = [MagicMock(path=p) for p in d2d_test_paths]
        mock_discovery_result.api_paths = []
        mock_discovery_result.d2d_paths = d2d_test_paths
        mock_discovery_result.all_tests = mock_discovery_result.d2d_tests
        mock_discovery_result.filtered_by_tags = False

        mock_inv = MagicMock()
        mock_inv.get_device_inventory.return_value = []
        mock_inv.filter_diagnostics = {
            "count_before": 2,
            "count_after": 0,
            "unknown_fields": [],
            "keys_seen": {"hostname", "role"},
            "filters": ["hostname=nonexistent"],
        }
        mock_inv.skipped_devices = []

        with (
            patch.object(
                orchestrator.test_discovery,
                "discover_pyats_tests",
                return_value=mock_discovery_result,
            ),
            patch.object(orchestrator, "device_inventory_discovery", mock_inv),
            patch("nac_test.pyats_core.orchestrator.SubprocessRunner"),
            caplog.at_level("WARNING"),
        ):
            results = orchestrator.run_tests()

        assert results.api is None
        assert results.d2d is None
        assert (
            "No devices matched the device filter(s): hostname=nonexistent"
            in caplog.text
        )

    def test_filter_unknown_field_returns_empty_results(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When device filter specifies an unknown field, orchestrator logs error and returns empty PyATSResults."""
        d2d_test_paths = [Path("/fake/tests/d2d/test_one.py")]
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            device_filters=["nonexistent_field=val"],
        )

        mock_discovery_result = MagicMock()
        mock_discovery_result.total_count = 1
        mock_discovery_result.api_tests = []
        mock_discovery_result.d2d_tests = [MagicMock(path=p) for p in d2d_test_paths]
        mock_discovery_result.api_paths = []
        mock_discovery_result.d2d_paths = d2d_test_paths
        mock_discovery_result.all_tests = mock_discovery_result.d2d_tests
        mock_discovery_result.filtered_by_tags = False

        mock_inv = MagicMock()
        mock_inv.get_device_inventory.return_value = []
        mock_inv.filter_diagnostics = {
            "count_before": 2,
            "count_after": 0,
            "unknown_fields": ["nonexistent_field"],
            "keys_seen": {"hostname", "role"},
            "filters": ["nonexistent_field=val"],
        }
        mock_inv.skipped_devices = []

        with (
            patch.object(
                orchestrator.test_discovery,
                "discover_pyats_tests",
                return_value=mock_discovery_result,
            ),
            patch.object(orchestrator, "device_inventory_discovery", mock_inv),
            patch("nac_test.pyats_core.orchestrator.SubprocessRunner"),
            caplog.at_level("ERROR"),
        ):
            results = orchestrator.run_tests()

        assert results.api is None
        assert results.d2d is None
        assert (
            "Device filter field(s) not found in data model: 'nonexistent_field'"
            in caplog.text
        )

    def test_repeated_positive_filters_emits_warning(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Multiple positive filters on the same field emit a warning."""
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            device_filters=["role=leaf", "role=spine"],
        )

        mock_discovery_result = MagicMock()
        mock_discovery_result.total_count = 0
        mock_discovery_result.api_tests = []
        mock_discovery_result.d2d_tests = []
        mock_discovery_result.api_paths = []
        mock_discovery_result.d2d_paths = []
        mock_discovery_result.all_tests = []
        mock_discovery_result.filtered_by_tags = False

        with (
            patch.object(
                orchestrator.test_discovery,
                "discover_pyats_tests",
                return_value=mock_discovery_result,
            ),
            caplog.at_level("WARNING"),
        ):
            orchestrator.run_tests()

        assert "Multiple positive filters for field 'role' detected" in caplog.text

    def test_device_filter_warns_when_no_d2d_tests(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When --device-filter is specified but only API tests exist, warning is logged."""
        api_test_paths = [Path("/fake/tests/api/test_one.py")]
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            device_filters=["role=leaf"],
            dry_run=True,
        )

        mock_discovery_result = MagicMock()
        mock_discovery_result.total_count = 1
        mock_discovery_result.api_tests = [MagicMock(path=p) for p in api_test_paths]
        mock_discovery_result.d2d_tests = []
        mock_discovery_result.api_paths = api_test_paths
        mock_discovery_result.d2d_paths = []
        mock_discovery_result.all_tests = mock_discovery_result.api_tests
        mock_discovery_result.filtered_by_tags = False

        with (
            patch.object(
                orchestrator.test_discovery,
                "discover_pyats_tests",
                return_value=mock_discovery_result,
            ),
            caplog.at_level("WARNING"),
        ):
            orchestrator.run_tests()

        assert (
            "--device-filter was specified but no D2D tests were executed"
            in caplog.text
        )
