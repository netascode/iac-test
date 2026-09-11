# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Device inventory discovery for SSH-based tests.

This module handles discovering device inventory from test architectures
in an architecture-agnostic way using the contract pattern.
"""

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DeviceInventoryDiscovery:
    """Handles device inventory discovery from test architectures."""

    def __init__(self, merged_data_filepath: Path):
        """Initialize device inventory discovery.

        Args:
            merged_data_filepath: Path to the merged data model file
        """
        self.merged_data_filepath = merged_data_filepath
        self.skipped_devices: list[dict[str, str]] = []

    def get_device_inventory(self, test_files: list[Path]) -> list[dict[str, Any]]:
        """Get device inventory from test architecture in an architecture-agnostic way.

        This method implements the contract pattern for SSH-based test architectures.
        It dynamically discovers which devices to test without knowing anything about
        the specific architecture (SD-WAN, NXOS, IOSXE, etc.).

        How it works:
        1. Takes D2D test files (already filtered to only contain tests from /d2d/ directory)
        2. Imports the first test file to find the SSH base class
        3. Calls the base class's get_ssh_device_inventory() method
        4. Returns whatever the architecture provides - no modifications

        Why we should only need the first test file:
        - All D2D tests within an architecture are likely to share the same SSH base class
        - E.g., all SD-WAN D2D tests inherit from SDWANTestBase
        - E.g., future NXOS D2D tests would inherit from NXOSTestBase
        - So any D2D test file gives us access to the inventory method

        Architecture "contract":
        Every SSH-based test architecture MUST provide a base class that:
        - Inherits from SSHTestBase (provided by nac-test)
        - Implements get_ssh_device_inventory(data_model) class method
        - Returns a list of dicts with REQUIRED fields: hostname, host, os, username, password
        - Optional fields: type, platform (for PyATS/Unicon compatibility)
        #TODO: Prob need to think about "type" and "platform" b/c PyATS/Unicon is picky.

        Example implementations:
        - nac-sdwan: SDWANTestBase parses sites.nac.yaml and resolves management IPs
        - future nac-nxos: NXOSTestBase might parse a different YAML structure
        - future nac-iosxe: IOSXETestBase might scan JSON files in data/devices/

        The orchestrator doesn't care HOW each architecture finds its devices,
        it just calls the contract method and uses what it gets back.

        Args:
            test_files: List of D2D test files (we'll use the first one)

        Returns:
            List of device dictionaries with connection information
        """
        if not test_files:
            logger.error("No test files provided for device inventory discovery")
            return []

        if not self.merged_data_filepath.exists():
            logger.error(f"Merged data model not found at {self.merged_data_filepath}")
            return []

        with open(self.merged_data_filepath, encoding="utf-8") as f:
            data_model = json.load(f)

        # Import the first D2D test file - all D2D tests in an architecture share the same SSH base class
        # For SD-WAN: all tests under /d2d/ inherit from SDWANTestBase
        # For future NXOS: all tests under /d2d/ would inherit from NXOSTestBase
        test_file = test_files[0]
        try:
            # I dont love this but we need to clean sys.argv before
            # importing to prevent PyATS argument parser conflict
            # PyATS configuration module parses sys.argv at import time looking for --pyats-configuration
            # Our --pyats flag gets interpreted as an incomplete --pyats-configuration argument
            # This follows the same pattern as PyATS's own aetest module
            original_argv = sys.argv.copy()
            try:
                # Remove --pyats from argv temporarily
                sys.argv = [arg for arg in sys.argv if arg != "--pyats"]

                # Dynamically import the test module
                spec = importlib.util.spec_from_file_location(
                    "test_module", str(test_file)
                )
                if spec is None or spec.loader is None:
                    logger.error(f"Could not load test module from {test_file}")
                    return []

                module = importlib.util.module_from_spec(spec)
                sys.modules["test_module"] = module
                spec.loader.exec_module(module)

            finally:
                # Always restore original argv
                sys.argv = original_argv

            # Look for a class with get_ssh_device_inventory method
            # We check all classes in the module and their inheritance chain (MRO)
            for _name, obj in vars(module).items():
                if hasattr(obj, "__mro__"):  # It's a class
                    # Check if this class or its parents have the method
                    # This handles inheritance: TestClass -> SDWANTestBase -> SSHTestBase
                    for cls in obj.__mro__:
                        if hasattr(cls, "get_ssh_device_inventory"):
                            # Found it! Call the method and return whatever it gives us
                            # The architecture handles all specifics: parsing files, resolving IPs, adding credentials

                            logger.info(
                                f"Found device inventory method in {cls.__name__}"
                            )
                            devices = cls.get_ssh_device_inventory(data_model)

                            # Capture skipped devices if the resolver exposes them
                            # Architecture resolvers store their last resolver instance
                            # with skipped_devices attribute
                            if hasattr(cls, "_last_resolver") and cls._last_resolver:
                                self.skipped_devices = getattr(
                                    cls._last_resolver, "skipped_devices", []
                                )

                            return list(devices)  # Ensure we return a list

        except Exception as e:
            logger.error(
                f"Failed to get device inventory from {test_file}: {e}", exc_info=True
            )

        # This should never happen if the architecture follows the contract btw

        logger.error("No test class with get_ssh_device_inventory() method found")
        return []
