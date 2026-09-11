# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for CombinedOrchestrator controller detection integration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from _pytest.monkeypatch import MonkeyPatch

from nac_test.combined_orchestrator import CombinedOrchestrator
from nac_test.core.controller_auth import AuthCheckResult, AuthOutcome
from nac_test.core.types import ControllerContext, PyATSResults
from nac_test.utils.logging import DEFAULT_LOGLEVEL
from tests.unit.conftest import AUTH_SUCCESS

PYATS_TEST_FILE_CONTENT = """
# PyATS test file
from pyats import aetest
from nac_test_pyats_common.iosxe import IOSXETestBase
class Test(IOSXETestBase):
    @aetest.test
    def test(self):
        pass
"""


class TestCombinedOrchestratorController:
    """Tests for CombinedOrchestrator controller detection."""

    def test_controller_type_is_none_after_init(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        """Controller type should be None after __init__ (detection deferred to run_tests)."""
        monkeypatch.setenv("ACI_URL", "https://apic.test.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "password")

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        output_dir = tmp_path / "output"

        orchestrator = CombinedOrchestrator(
            data_paths=[data_dir],
            templates_dir=templates_dir,
            output_dir=output_dir,
        )

        # Controller detection is now deferred to run_tests()
        assert orchestrator.controller_context is None

    def test_controller_detected_during_run_tests(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        """Controller type should be detected when run_tests() is called with PyATS tests."""
        monkeypatch.setenv("ACI_URL", "https://apic.test.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "password")

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        orchestrator = CombinedOrchestrator(
            data_paths=[data_dir],
            templates_dir=templates_dir,
            output_dir=output_dir,
            dev_pyats_only=True,
        )

        assert orchestrator.controller_context is None

        with (
            patch.object(
                orchestrator, "_discover_test_types", return_value=(True, False)
            ),
            patch(
                "nac_test.combined_orchestrator.preflight_auth_check",
                return_value=AUTH_SUCCESS,
            ),
            patch("nac_test.combined_orchestrator.PyATSOrchestrator") as mock_pyats,
            patch("nac_test.combined_orchestrator.CombinedReportGenerator") as mock_gen,
            patch("typer.echo"),
            patch("typer.secho"),
            patch.object(CombinedOrchestrator, "_check_python_version"),
        ):
            mock_instance = MagicMock()
            mock_instance.run_tests.return_value = PyATSResults()
            mock_pyats.return_value = mock_instance
            mock_gen_instance = MagicMock()
            mock_gen_instance.generate_combined_summary.return_value = None
            mock_gen.return_value = mock_gen_instance

            orchestrator.run_tests()

        # Controller should now be detected
        assert orchestrator.controller_context is not None
        # Note: mypy flags this as unreachable because it loses type narrowing after
        # the method call above. The assertion at line 107 narrows the type, but mypy
        # conservatively assumes run_tests() could have mutated controller_context back
        # to None. This is a known mypy limitation with attribute narrowing across calls.
        assert orchestrator.controller_context.controller_type == "ACI"  # type: ignore[unreachable]

    def test_detection_failure_continues_with_preflight_failure(
        self, tmp_path: Path
    ) -> None:
        """Detection failure sets pre_flight_failure and continues (does not exit)."""
        # No controller credentials set (already cleaned by fixture)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # __init__ should succeed without credentials
        orchestrator = CombinedOrchestrator(
            data_paths=[data_dir],
            templates_dir=templates_dir,
            output_dir=output_dir,
            dev_pyats_only=True,
        )

        # run_tests() should set pre_flight_failure when PyATS tests found but no credentials
        with (
            patch.object(
                orchestrator, "_discover_test_types", return_value=(True, False)
            ),
            patch("typer.secho"),
            patch("nac_test.combined_orchestrator.CombinedReportGenerator") as mock_gen,
        ):
            mock_gen_instance = MagicMock()
            mock_gen_instance.generate_combined_summary.return_value = (
                output_dir / "combined_summary.html"
            )
            mock_gen.return_value = mock_gen_instance

            # Should NOT raise - instead returns results with pre_flight_failure
            results = orchestrator.run_tests()

            # Pre-flight failure should be set with detection type
            assert results.pre_flight_failure is not None
            assert results.pre_flight_failure.failure_type.value == "detection"
            assert results.pre_flight_failure.controller_type is None
            assert results.pre_flight_failure.controller_url is None

    def test_combined_orchestrator_passes_controller_to_pyats(
        self, tmp_path: Path, monkeypatch: MonkeyPatch, sdwan_context: ControllerContext
    ) -> None:
        """Test that CombinedOrchestrator passes controller type to PyATSOrchestrator."""
        # Set up SDWAN credentials
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.test.com")
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        # Create test directories and files
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_file = data_dir / "test.yaml"
        data_file.write_text("test: data")

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        test_file = templates_dir / "test_verify.py"
        test_file.write_text(PYATS_TEST_FILE_CONTENT)

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        merged_file = output_dir / "merged.json"
        merged_file.write_text('{"merged": "data"}')

        sdwan_auth = AuthCheckResult(
            success=True,
            reason=AuthOutcome.SUCCESS,
            controller_type="SDWAN",
            controller_url="https://vmanage.test.com",
            detail="OK",
        )

        # Initialize CombinedOrchestrator
        orchestrator = CombinedOrchestrator(
            data_paths=[data_dir],
            templates_dir=templates_dir,
            output_dir=output_dir,
            dev_pyats_only=True,  # Run PyATS only mode
        )

        # Mock discovery to return PyATS tests found
        with patch.object(
            orchestrator, "_discover_test_types", return_value=(True, False)
        ):
            # Mock PyATSOrchestrator to verify it receives the controller type
            with patch(
                "nac_test.combined_orchestrator.PyATSOrchestrator"
            ) as mock_pyats:
                mock_instance = MagicMock()
                # PyATS now returns PyATSResults
                mock_instance.run_tests.return_value = PyATSResults()
                mock_pyats.return_value = mock_instance

                # Mock CombinedReportGenerator (called in unified flow)
                with patch(
                    "nac_test.combined_orchestrator.CombinedReportGenerator"
                ) as mock_generator:
                    mock_gen_instance = MagicMock()
                    mock_gen_instance.generate_combined_summary.return_value = (
                        output_dir / "combined_summary.html"
                    )
                    mock_generator.return_value = mock_gen_instance

                    # Mock preflight auth and typer functions
                    with (
                        patch(
                            "nac_test.combined_orchestrator.preflight_auth_check",
                            return_value=sdwan_auth,
                        ),
                        patch("typer.secho"),
                        patch("typer.echo"),
                        patch.object(CombinedOrchestrator, "_check_python_version"),
                    ):
                        # Run tests
                        orchestrator.run_tests()

                # Verify PyATSOrchestrator was called with controller_context
                mock_pyats.assert_called_once_with(
                    data_paths=[data_dir],
                    test_dir=templates_dir,
                    output_dir=output_dir,
                    minimal_reports=False,
                    custom_testbed_path=None,
                    controller_context=sdwan_context,
                    dry_run=False,
                    verbose=False,
                    loglevel=DEFAULT_LOGLEVEL,
                    include_tags=[],
                    exclude_tags=[],
                )

                # Verify run_tests was called on the instance
                mock_instance.run_tests.assert_called_once()

    def test_render_only_mode_does_not_instantiate_pyats_orchestrator(
        self, tmp_path: Path
    ) -> None:
        """Test that render-only mode NEVER instantiates PyATSOrchestrator.

        This is a critical invariant: render-only mode should only render templates
        without any test execution. PyATSOrchestrator should never be called,
        while Robot would be called to render Robot templates.

        This also verifies backward compatibility: render-only mode should work
        without any controller credentials being set, so we also test the controller
        detection logic to be skipped.
        """
        # No controller credentials set (already cleaned by fixture)
        # This also verifies the fix from PR #509: no controller check in render-only mode

        # Create test directories and files
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_file = data_dir / "test.yaml"
        data_file.write_text("test: data")

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        # Create a PyATS test file to trigger the PyATS code path
        pyats_test = templates_dir / "test_verify.py"
        pyats_test.write_text(PYATS_TEST_FILE_CONTENT)

        # Also create a Robot template to ensure Robot orchestrator runs
        robot_template = templates_dir / "test.robot"
        robot_template.write_text("*** Test Cases ***\nTest\n    Log    Hello")

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        merged_file = output_dir / "merged.json"
        merged_file.write_text('{"test": "data"}')

        # Initialize CombinedOrchestrator with render_only=True
        # This should NOT raise typer.Exit despite missing credentials
        orchestrator = CombinedOrchestrator(
            data_paths=[data_dir],
            templates_dir=templates_dir,
            output_dir=output_dir,
            render_only=True,  # Critical: render-only mode
        )

        # Verify controller_type is empty (no detection occurred)
        assert orchestrator.controller_context is None

        # Mock PyATSOrchestrator to verify it's never instantiated
        with patch("nac_test.combined_orchestrator.PyATSOrchestrator") as mock_pyats:
            # Mock RobotOrchestrator to verify it is called
            with patch(
                "nac_test.combined_orchestrator.RobotOrchestrator"
            ) as mock_robot:
                mock_robot_instance = MagicMock()
                mock_robot.return_value = mock_robot_instance

                with (
                    patch(
                        "nac_test.combined_orchestrator.resolve_controller"
                    ) as mock_resolve,
                    patch("typer.echo"),
                    patch("typer.secho"),
                ):
                    # Run tests
                    orchestrator.run_tests()

            # resolve_controller should NOT be called in render-only mode
            mock_resolve.assert_not_called()
            # CRITICAL ASSERTION: PyATSOrchestrator must NEVER be instantiated
            mock_pyats.assert_not_called()
            # Robot must be called
            mock_robot.assert_called_once()

    def test_combined_orchestrator_production_mode_passes_controller(
        self, tmp_path: Path, monkeypatch: MonkeyPatch, cc_context: ControllerContext
    ) -> None:
        """Test that CombinedOrchestrator passes controller type in production mode."""
        # Set up CC credentials
        monkeypatch.setenv("CC_URL", "https://cc.test.com")
        monkeypatch.setenv("CC_USERNAME", "admin")
        monkeypatch.setenv("CC_PASSWORD", "password")

        # Create test directories and files
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_file = data_dir / "test.yaml"
        data_file.write_text("test: data")

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        test_file = templates_dir / "test_verify.py"
        test_file.write_text(PYATS_TEST_FILE_CONTENT)

        output_dir = tmp_path / "output"
        output_dir.mkdir()
        merged_file = output_dir / "merged.json"
        merged_file.write_text('{"merged": "data"}')

        cc_auth = AuthCheckResult(
            success=True,
            reason=AuthOutcome.SUCCESS,
            controller_type="CC",
            controller_url="https://cc.test.com",
            detail="OK",
        )

        # Initialize CombinedOrchestrator (production mode - no dev flags)
        orchestrator = CombinedOrchestrator(
            data_paths=[data_dir],
            templates_dir=templates_dir,
            output_dir=output_dir,
        )

        # Controller type should be None after init (deferred to run_tests)
        assert orchestrator.controller_context is None

        # Mock PyATSOrchestrator and discovery
        with patch("nac_test.combined_orchestrator.PyATSOrchestrator") as mock_pyats:
            mock_instance = MagicMock()
            # PyATS now returns PyATSResults
            mock_instance.run_tests.return_value = PyATSResults()
            mock_pyats.return_value = mock_instance

            # Mock TestDiscovery so CombinedOrchestrator sees PyATS files
            with patch(
                "nac_test.combined_orchestrator.TestDiscovery"
            ) as mock_discovery:
                mock_discovery_instance = MagicMock()
                mock_discovery_instance.has_pyats_tests.return_value = True
                mock_discovery.return_value = mock_discovery_instance

                # Mock preflight auth and typer functions
                with (
                    patch(
                        "nac_test.combined_orchestrator.preflight_auth_check",
                        return_value=cc_auth,
                    ),
                    patch("typer.echo"),
                    patch.object(CombinedOrchestrator, "_check_python_version"),
                ):
                    # Run tests
                    orchestrator.run_tests()

                # Verify PyATSOrchestrator was called with controller_context

                mock_pyats.assert_called_once_with(
                    data_paths=[data_dir],
                    test_dir=templates_dir,
                    output_dir=output_dir,
                    minimal_reports=False,
                    custom_testbed_path=None,
                    controller_context=cc_context,
                    dry_run=False,
                    verbose=False,
                    loglevel=DEFAULT_LOGLEVEL,
                    include_tags=[],
                    exclude_tags=[],
                )

                # Verify run_tests was called on the instance
                mock_instance.run_tests.assert_called_once()
