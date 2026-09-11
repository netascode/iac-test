# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Tests for PyATSOrchestrator handling of SubprocessRunner init failures."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nac_test.core.constants import ENV_CONTROLLER_CONTEXT
from nac_test.pyats_core.orchestrator import PyATSOrchestrator
from tests.conftest import PyATSTestDirs


class TestOrchestratorSubprocessRunnerInitError:
    """Tests for RuntimeError handling in PyATSOrchestrator when SubprocessRunner cannot initialize."""

    @pytest.mark.parametrize(
        ("has_api", "has_d2d"),
        [
            (True, False),
            (False, True),
            (True, True),
        ],
    )
    def test_subprocess_runner_init_error_returns_from_error_results(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
        has_api: bool,
        has_d2d: bool,
    ) -> None:
        """OSError in SubprocessRunner._create_config_files raises RuntimeError, returns from_error results."""
        api_test_paths = [Path("/fake/tests/api/test_one.py")] if has_api else []
        d2d_test_paths = [Path("/fake/tests/d2d/test_two.py")] if has_d2d else []

        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
        )

        # Build a mock DiscoveryResult
        mock_discovery_result = MagicMock()
        mock_discovery_result.total_count = len(api_test_paths) + len(d2d_test_paths)
        mock_discovery_result.api_tests = [MagicMock(path=p) for p in api_test_paths]
        mock_discovery_result.d2d_tests = [MagicMock(path=p) for p in d2d_test_paths]
        mock_discovery_result.api_paths = api_test_paths
        mock_discovery_result.d2d_paths = d2d_test_paths
        mock_discovery_result.all_tests = (
            mock_discovery_result.api_tests + mock_discovery_result.d2d_tests
        )
        mock_discovery_result.filtered_by_tags = False

        with (
            patch.object(
                orchestrator.test_discovery,
                "discover_pyats_tests",
                return_value=mock_discovery_result,
            ),
            patch(
                "nac_test.pyats_core.execution.subprocess_runner.Path.write_text",
                side_effect=OSError("disk full"),
            ),
        ):
            result = orchestrator.run_tests()

        if has_api:
            assert result.api is not None
            assert result.api.has_error is True
            assert result.api.reason is not None
            assert "disk full" in result.api.reason
        else:
            assert result.api is None

        if has_d2d:
            assert result.d2d is not None
            assert result.d2d.has_error is True
            assert result.d2d.reason is not None
            assert "disk full" in result.d2d.reason
        else:
            assert result.d2d is None


class TestOrchestratorControllerContextEnvVar:
    """Tests for PyATSOrchestrator controller context handling."""

    def test_orchestrator_populates_controller_context_env_var(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
    ) -> None:
        """PyATSOrchestrator stores controller_context without polluting os.environ."""
        # Clear any existing context from prior tests
        os.environ.pop(ENV_CONTROLLER_CONTEXT, None)

        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.output_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
        )

        # __init__ should NOT set the env var (moved to subprocess launch)
        assert ENV_CONTROLLER_CONTEXT not in os.environ

        # But the orchestrator should have the context stored
        assert orchestrator.controller_context is not None
        assert orchestrator.controller_context.controller_type == "ACI"
        assert orchestrator.controller_context.auth_method == "session"
        assert orchestrator.controller_type == "ACI"
