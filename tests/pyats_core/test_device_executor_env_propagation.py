# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for DeviceExecutor ENV_TEST_DIR env var propagation."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from nac_test.data_merger import DataMerger
from nac_test.pyats_core.constants import ENV_TEST_DIR
from nac_test.pyats_core.execution.device.device_executor import DeviceExecutor
from nac_test.pyats_core.execution.device.testbed_generator import TestbedGenerator
from nac_test.pyats_core.execution.job_generator import JobGenerator
from nac_test.pyats_core.execution.subprocess_runner import SubprocessRunner
from tests.conftest import PyATSTestDirs


class TestDeviceExecutorEnvPropagation:
    """ENV_TEST_DIR must appear in the env passed by DeviceExecutor to execute_job_with_testbed."""

    def test_run_device_job_includes_env_test_dir(
        self,
        pyats_test_dirs: PyATSTestDirs,
    ) -> None:
        """DeviceExecutor passes ENV_TEST_DIR=str(test_dir) to subprocess_runner.execute_job_with_testbed."""
        captured_env: dict[str, str] = {}

        async def capture_execute_job_with_testbed(
            _job: Path,
            _testbed: Path,
            env: dict[str, str],
        ) -> None:
            captured_env.update(env)
            return None

        mock_runner = MagicMock(spec=SubprocessRunner)
        mock_runner.execute_job_with_testbed = AsyncMock(
            side_effect=capture_execute_job_with_testbed
        )

        mock_job_generator = MagicMock(spec=JobGenerator)
        mock_job_generator.generate_device_centric_job.return_value = (
            "# fake job content"
        )

        executor = DeviceExecutor(
            job_generator=mock_job_generator,
            subprocess_runner=mock_runner,
            test_status={},
            test_dir=pyats_test_dirs.test_dir,
            base_output_dir=pyats_test_dirs.output_dir,
            merged_data_path=DataMerger.merged_data_path(pyats_test_dirs.output_dir),
        )

        device: dict[str, Any] = {
            "hostname": "router1",
            "host": "192.0.2.1",
            "username": "admin",
            "password": "secret",
        }
        test_file = pyats_test_dirs.test_dir / "verify_bgp.py"
        test_file.touch()
        semaphore = asyncio.Semaphore(1)

        with patch.object(TestbedGenerator, "generate_testbed_yaml", return_value=""):
            asyncio.run(
                executor.run_device_job_with_semaphore(device, [test_file], semaphore)
            )

        assert ENV_TEST_DIR in captured_env
        assert captured_env[ENV_TEST_DIR] == str(pyats_test_dirs.test_dir)
