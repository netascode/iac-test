# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Test PyATS orchestrator controller detection integration."""

import pytest

from nac_test.core.constants import EXIT_ERROR
from nac_test.pyats_core.orchestrator import PyATSOrchestrator
from tests.conftest import PyATSTestDirs


class TestOrchestratorControllerDetection:
    """Test controller detection integration in PyATSOrchestrator."""

    def test_orchestrator_detects_controller_on_init(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Test that PyATSOrchestrator detects controller type during initialization."""
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data.yaml"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
        )

        assert orchestrator.controller_type == "ACI"

    def test_orchestrator_exits_on_detection_failure(
        self, clean_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Test that PyATSOrchestrator exits gracefully when controller detection fails."""
        with pytest.raises(SystemExit) as exc_info:
            PyATSOrchestrator(
                data_paths=[pyats_test_dirs.output_dir.parent / "data.yaml"],
                test_dir=pyats_test_dirs.test_dir,
                output_dir=pyats_test_dirs.output_dir,
            )

        # Verify it exits with EXIT_ERROR (255) for infrastructure errors
        assert exc_info.value.code == EXIT_ERROR

    def test_orchestrator_handles_multiple_controllers_error(
        self,
        aci_controller_env: None,
        sdwan_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
    ) -> None:
        """Test that PyATSOrchestrator handles multiple controller credentials error."""
        with pytest.raises(SystemExit) as exc_info:
            PyATSOrchestrator(
                data_paths=[pyats_test_dirs.output_dir.parent / "data.yaml"],
                test_dir=pyats_test_dirs.test_dir,
                output_dir=pyats_test_dirs.output_dir,
            )

        # Verify it exits with EXIT_ERROR (255) for infrastructure errors
        assert exc_info.value.code == EXIT_ERROR

    def test_orchestrator_no_longer_uses_controller_type_env_var(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that PyATSOrchestrator ignores CONTROLLER_TYPE environment variable."""
        monkeypatch.setenv("CONTROLLER_TYPE", "SDWAN")

        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data.yaml"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
        )

        assert orchestrator.controller_type == "ACI"
