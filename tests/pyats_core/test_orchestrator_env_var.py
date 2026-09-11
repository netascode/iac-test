# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for PyATSOrchestrator environment variable handling."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nac_test.core.constants import ENV_CONTROLLER_CONTEXT
from nac_test.core.controller import get_controller_context
from nac_test.core.types import AuthMethod, ControllerContext
from nac_test.pyats_core.constants import ENV_TEST_DIR
from nac_test.pyats_core.execution.subprocess_runner import SubprocessRunner
from nac_test.pyats_core.orchestrator import PyATSOrchestrator
from tests.conftest import PyATSTestDirs


class TestOrchestratorEnvVarProcesses:
    """Tests for NAC_TEST_PYATS_PROCESSES environment variable in orchestrator."""

    def test_orchestrator_respects_nac_test_pyats_processes_env_var(
        self,
        pyats_test_dirs: PyATSTestDirs,
        aci_controller_env: None,
        monkeypatch: pytest.MonkeyPatch,
        aci_context: ControllerContext,
    ) -> None:
        """Verify orchestrator passes correct env_var to calculate_worker_capacity."""
        monkeypatch.setenv("NAC_TEST_PYATS_PROCESSES", "123456")

        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.test_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            controller_context=aci_context,
        )

        assert orchestrator.max_workers == 123456


class TestOrchestratorEnvPropagation:
    """ENV_TEST_DIR must appear in the env passed by the orchestrator to execute_job."""

    def test_execute_api_tests_includes_env_test_dir(
        self,
        pyats_test_dirs: PyATSTestDirs,
        aci_controller_env: None,
        aci_context: ControllerContext,
    ) -> None:
        """Orchestrator passes ENV_TEST_DIR=str(test_dir) to subprocess_runner.execute_job."""
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.test_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            controller_context=aci_context,
        )

        captured_env: dict[str, str] = {}

        async def capture_execute_job(_: Path, env: dict[str, str]) -> None:
            captured_env.update(env)
            return None

        mock_runner = MagicMock(spec=SubprocessRunner)
        mock_runner.execute_job = AsyncMock(side_effect=capture_execute_job)
        orchestrator.subprocess_runner = mock_runner

        test_file = pyats_test_dirs.test_dir / "verify_bgp.py"
        test_file.touch()

        asyncio.run(orchestrator._execute_api_tests_standard([test_file]))

        assert ENV_TEST_DIR in captured_env
        assert captured_env[ENV_TEST_DIR] == str(pyats_test_dirs.test_dir)


class TestControllerContextRoundTrip:
    """End-to-end round-trip: orchestrator serializes → subprocess deserializes.

    The serialization side (PyATSOrchestrator._execute_api_tests_standard)
    and the deserialization side (get_controller_context → from_json) are
    each tested in isolation elsewhere. This test chains them to prove the
    full round-trip contract.
    """

    @pytest.mark.parametrize(
        "controller_type,auth_method",
        [
            ("ACI", AuthMethod.SESSION),
            ("SDWAN", AuthMethod.TOKEN),
            ("CC", AuthMethod.SESSION),
        ],
        ids=["aci-session", "sdwan-token", "cc-session"],
    )
    def test_orchestrator_serialized_context_round_trips(
        self,
        pyats_test_dirs: PyATSTestDirs,
        aci_controller_env: None,
        monkeypatch: pytest.MonkeyPatch,
        controller_type: str,
        auth_method: AuthMethod,
    ) -> None:
        """Serialized ENV_CONTROLLER_CONTEXT from orchestrator deserializes
        back to an identical ControllerContext via get_controller_context()."""
        original = ControllerContext(
            controller_type=controller_type,  # type: ignore[arg-type]
            auth_method=auth_method,
        )
        orchestrator = PyATSOrchestrator(
            data_paths=[pyats_test_dirs.test_dir.parent / "data"],
            test_dir=pyats_test_dirs.test_dir,
            output_dir=pyats_test_dirs.output_dir,
            controller_context=original,
        )

        # Capture the env dict that orchestrator passes to execute_job
        captured_env: dict[str, str] = {}

        async def capture_execute_job(_: Path, env: dict[str, str]) -> None:
            captured_env.update(env)
            return None

        mock_runner = MagicMock(spec=SubprocessRunner)
        mock_runner.execute_job = AsyncMock(side_effect=capture_execute_job)
        orchestrator.subprocess_runner = mock_runner

        test_file = pyats_test_dirs.test_dir / "verify_round_trip.py"
        test_file.touch()
        asyncio.run(orchestrator._execute_api_tests_standard([test_file]))

        # Verify ENV_CONTROLLER_CONTEXT was set in the subprocess env
        assert ENV_CONTROLLER_CONTEXT in captured_env

        # Simulate subprocess side: set env var and deserialize
        monkeypatch.setenv(ENV_CONTROLLER_CONTEXT, captured_env[ENV_CONTROLLER_CONTEXT])
        restored = get_controller_context()

        # Round-trip must produce an identical ControllerContext
        assert restored == original
        assert restored.controller_type == controller_type
        assert restored.auth_method == auth_method
