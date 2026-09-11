# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for PyATSOrchestrator controller_context parameter."""

from unittest.mock import patch

from nac_test.core.types import AuthMethod, ControllerContext
from nac_test.pyats_core.orchestrator import PyATSOrchestrator
from tests.conftest import PyATSTestDirs


class TestOrchestratorControllerParam:
    """Tests for PyATSOrchestrator controller_context parameter."""

    def test_orchestrator_uses_provided_controller_context(
        self, clean_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Test that PyATSOrchestrator uses provided controller_context instead of detecting."""
        controller_context = ControllerContext(
            controller_type="SDWAN", auth_method=AuthMethod.TOKEN
        )

        with patch(
            "nac_test.pyats_core.orchestrator.resolve_controller"
        ) as mock_resolve:
            orchestrator = PyATSOrchestrator(
                data_paths=[pyats_test_dirs.output_dir.parent / "data"],
                test_dir=pyats_test_dirs.test_dir,
                output_dir=pyats_test_dirs.output_dir,
                controller_context=controller_context,
            )

            assert orchestrator.controller_type == "SDWAN"
            mock_resolve.assert_not_called()

    def test_orchestrator_falls_back_to_detection_when_none(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Test that PyATSOrchestrator detects controller when controller_context is None."""
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            controller_context=None,
        )

        assert orchestrator.controller_type == "ACI"

    def test_orchestrator_defaults_to_detection(
        self, cc_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Test that PyATSOrchestrator detects controller when parameter not provided."""
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
        )

        assert orchestrator.controller_type == "CC"
