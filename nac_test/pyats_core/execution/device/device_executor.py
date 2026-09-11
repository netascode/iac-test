# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Device-centric test execution functionality."""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from nac_test.pyats_core.constants import ENV_TEST_DIR
from nac_test.pyats_core.execution.device.testbed_generator import TestbedGenerator
from nac_test.pyats_core.execution.job_generator import JobGenerator
from nac_test.pyats_core.execution.subprocess_runner import SubprocessRunner
from nac_test.utils.cleanup import get_cleanup_manager

logger = logging.getLogger(__name__)


class DeviceExecutor:
    """Handles device-centric test execution."""

    def __init__(
        self,
        job_generator: JobGenerator,
        subprocess_runner: SubprocessRunner,
        test_status: dict[str, Any],
        test_dir: Path,
        base_output_dir: Path,
        merged_data_path: Path,
        custom_testbed_path: Path | None = None,
    ):
        """Initialize device executor.

        Args:
            job_generator: JobGenerator instance for creating job files
            subprocess_runner: SubprocessRunner instance for executing jobs
            test_status: Dictionary for tracking test status
            test_dir: Directory containing PyATS test files (user-specified)
            base_output_dir: Base output directory for test results
            merged_data_path: Path to the merged data model file
            custom_testbed_path: Optional path to custom PyATS testbed YAML
        """
        self.job_generator = job_generator
        self.subprocess_runner = subprocess_runner
        self.test_status = test_status
        self.test_dir = test_dir
        self.base_output_dir = base_output_dir
        self.merged_data_path = merged_data_path
        self.custom_testbed_path = custom_testbed_path

    async def run_device_job_with_semaphore(
        self,
        device: dict[str, Any],
        test_files: list[Path],
        semaphore: asyncio.Semaphore,
    ) -> Path | None:
        """Run PyATS tests for a specific device with semaphore control.

        This method:
        1. Acquires a semaphore slot to limit concurrent device testing
        2. Generates a device-specific job file
        3. Generates a testbed YAML for the device
        4. Executes the tests via subprocess
        5. Returns the path to the device's test archive

        Args:
            device: Device dictionary with connection info
            test_files: List of test files to run
            semaphore: Asyncio semaphore for concurrency control

        Returns:
            Path to the device's test archive if successful, None otherwise
        """
        hostname = device["hostname"]  # Required field per nac-test contract

        async with semaphore:
            logger.info(f"Starting tests for device {hostname}")

            try:
                # Create temporary files for job and testbed
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False, encoding="utf-8"
                ) as job_file:
                    job_content = self.job_generator.generate_device_centric_job(
                        device, test_files
                    )
                    job_file.write(job_content)
                    job_file_path = Path(job_file.name)

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".yaml", delete=False, encoding="utf-8"
                ) as testbed_file:
                    testbed_content = TestbedGenerator.generate_testbed_yaml(
                        device, base_testbed_path=self.custom_testbed_path
                    )
                    testbed_file.write(testbed_content)
                    testbed_file_path = Path(testbed_file.name)

                # Register temp files for deletion on exit (kept when NAC_TEST_DEBUG is set).
                cleanup = get_cleanup_manager()
                cleanup.register(job_file_path, keep_if_debug=True)
                cleanup.register(testbed_file_path, keep_if_debug=True)

                # Set up environment for this device
                # Always start with a copy of os.environ to preserve PATH and other variables
                env = os.environ.copy()
                env.update(
                    {
                        "HOSTNAME": hostname,
                        "DEVICE_INFO": json.dumps(device),
                        "MERGED_DATA_MODEL_TEST_VARIABLES_FILEPATH": str(
                            self.merged_data_path
                        ),
                        "NAC_TEST_TYPE": "d2d",
                        ENV_TEST_DIR: str(self.test_dir),
                    }
                )

                # Track test status for this device.
                #
                # NOTE: This status tracking has known issues:
                # 1. Uses "hostname::test_stem" key format vs OutputProcessor's "full.module.path"
                # 2. Lines 147-151 mark status based on archive existence (buggy - archives are
                #    created even for failed tests)
                #
                # The orchestrator clears d2d_test_status before populating it from
                # OutputProcessor's correctly-parsed results. HOWEVER, we keep this code because:
                # - Lines 175-179 handle the edge case where the subprocess fails to START
                #   (e.g., job file generation error). In that case, OutputProcessor never
                #   sees the test, so this error tracking provides visibility.
                # - The error is still logged regardless, but this ensures it appears in status.
                #
                # TODO: Consider refactoring to only track errors, not success/failure.
                for test_file in test_files:
                    test_name = f"{hostname}::{test_file.stem}"
                    self.test_status[test_name] = {
                        "status": "pending",
                        "device": hostname,
                        "test_file": str(test_file),
                    }

                # Execute the job with testbed
                archive_path = await self.subprocess_runner.execute_job_with_testbed(
                    job_file_path, testbed_file_path, env
                )

                # Update test status based on result
                status = "passed" if archive_path else "failed"
                for test_file in test_files:
                    test_name = f"{hostname}::{test_file.stem}"
                    if test_name in self.test_status:
                        self.test_status[test_name]["status"] = status

                if archive_path:
                    logger.info(
                        f"Completed tests for device {hostname}: {archive_path}"
                    )
                else:
                    logger.error(f"Failed to run tests for device {hostname}")

                return Path(archive_path) if archive_path else None

            except Exception as e:
                logger.error(
                    f"Error running tests for device {hostname}: {e}", exc_info=True
                )

                # Mark all tests as errored
                for test_file in test_files:
                    test_name = f"{hostname}::{test_file.stem}"
                    if test_name in self.test_status:
                        self.test_status[test_name]["status"] = "errored"
                        self.test_status[test_name]["error"] = str(e)

                return None
