# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Integration test for controller detection across the framework."""

from pathlib import Path

import pytest

from nac_test.core.controller import detect_controller_type
from nac_test.pyats_core.orchestrator import PyATSOrchestrator


class TestControllerDetectionIntegration:
    """Integration tests for controller detection across components.

    Note: Controller credentials are cleared by the autouse
    clear_controller_credentials fixture in tests/integration/conftest.py.
    """

    def test_end_to_end_controller_detection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test controller detection flows through the entire orchestration stack."""
        # Set up SDWAN environment variables
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        # Create required directories and files
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Create a dummy merged data file
        merged_file = output_dir / "merged_data.json"
        merged_file.write_text('{"test": "data"}')

        # Create a dummy test file
        test_file = test_dir / "test_dummy.py"
        test_file.write_text("""
from pyats import aetest

class TestDummy(aetest.Testcase):
    @aetest.test
    def test_method(self):
        pass
""")

        # Initialize orchestrator (should detect SDWAN)
        orchestrator = PyATSOrchestrator(
            data_paths=[tmp_path / "data.yaml"],
            test_dir=test_dir,
            output_dir=output_dir,
        )

        # Verify controller type was detected correctly
        assert orchestrator.controller_type == "SDWAN"

    def test_controller_switch_scenario(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test switching between different controller types."""
        # Start with ACI
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "password")

        assert detect_controller_type() == "ACI"

        # Clear ACI and switch to FMC
        monkeypatch.delenv("ACI_URL")
        monkeypatch.delenv("ACI_USERNAME")
        monkeypatch.delenv("ACI_PASSWORD")

        monkeypatch.setenv("FMC_URL", "https://fmc.example.com")
        monkeypatch.setenv("FMC_USERNAME", "admin")
        monkeypatch.setenv("FMC_PASSWORD", "password")

        assert detect_controller_type() == "FMC"

        # Clear FMC and switch to ISE
        monkeypatch.delenv("FMC_URL")
        monkeypatch.delenv("FMC_USERNAME")
        monkeypatch.delenv("FMC_PASSWORD")

        monkeypatch.setenv("ISE_URL", "https://ise.example.com")
        monkeypatch.setenv("ISE_USERNAME", "admin")
        monkeypatch.setenv("ISE_PASSWORD", "password")

        assert detect_controller_type() == "ISE"
