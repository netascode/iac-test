# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

import asyncio
import concurrent.futures
import json
import logging
import os
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

from pyats import aetest

from nac_test.pyats_core.broker.broker_client import BrokerClient, BrokerCommandExecutor
from nac_test.pyats_core.common.base_test import NACTestBase
from nac_test.pyats_core.constants import DEVICE_EXECUTE_TIMEOUT
from nac_test.pyats_core.ssh.command_cache import CommandCache
from nac_test.utils import get_or_create_event_loop
from nac_test.utils.device_validation import validate_device_inventory


class SSHTestBase(NACTestBase):
    """Base class for all SSH-based device tests.

    This class provides the core framework for SSH test execution, including
    automatic context setup and command execution capabilities.

    The class also provides access to the PyATS testbed object when available,
    enabling the use of Genie parsers and other PyATS/Genie features.
    #TODO: Move this to its own thing to better adhere for SRP. Hustling the MVP.
    """

    # Instance variables set during setup (type annotations for mypy)
    # Optional attributes (may be None before setup completes or if setup fails)
    connection: BrokerCommandExecutor | Any | None = (
        None  # BrokerCommandExecutor or testbed device
    )
    hostname: str | None = None
    device_info: dict[str, Any] | None = None
    device_data: dict[str, Any] | None = None
    command_cache: CommandCache | None = None
    execute_command: Callable[[str], Coroutine[Any, Any, str]] | None = None

    # Required attribute — always assigned in setup() before any test method runs.
    # Intentionally non-optional: accessing before setup() is a programming error.
    broker_client: BrokerClient

    @property
    def testbed(self) -> Any | None:
        """Access the PyATS testbed object if available.

        When tests are run via PyATS with --testbed-file, the testbed is loaded
        and made available through the runtime. This property provides convenient
        access to it.

        Returns:
            The PyATS testbed object if available, None otherwise.
        """
        # In PyATS aetest, testbed is passed as an internal parameter
        # self.parameters is always available (PyATS property on TestItem)
        # Check internal parameters (where PyATS stores testbed)
        if (
            hasattr(self.parameters, "internal")
            and "testbed" in self.parameters.internal
        ):
            return self.parameters.internal["testbed"]
        # Fallback to regular parameters
        if "testbed" in self.parameters:
            return self.parameters["testbed"]
        return None

    @property
    def testbed_device(self) -> Any | None:
        """Access the current device from the PyATS testbed.

        This provides a convenient way to access the current device's testbed
        object, which includes Genie parsing capabilities.

        Returns:
            The device object from the testbed if available, None otherwise.
        """
        if self.testbed and self.hostname is not None:
            # Look up device by hostname in the testbed
            if self.hostname in self.testbed.devices:
                return self.testbed.devices[self.hostname]
        return None

    @aetest.setup  # type: ignore[misc]
    def setup(self) -> None:
        """
        Combined setup that calls parent setup then sets up SSH context.

        This lifecycle hook is called by PyATS automatically. It first calls
        the parent NACTestBase setup, then reads device info and establishes
        SSH connections with necessary tools (like self.execute_command).

        If a PyATS testbed is available, it also ensures the device connection
        is established through the testbed for Genie parser access.
        """
        # Call parent setup first
        super().setup()

        # Then do SSH-specific setup
        # These environment variables are not set by the user, but are passed
        # by the nac-test orchestrator to provide context to this isolated
        # PyATS job process.
        device_info_json = os.environ.get("DEVICE_INFO")
        data_file_path = os.environ.get("MERGED_DATA_MODEL_TEST_VARIABLES_FILEPATH")

        if not device_info_json or not data_file_path:
            self.failed(
                "Framework Error: DEVICE_INFO and MERGED_DATA_MODEL_TEST_VARIABLES_FILEPATH env vars must be set by the orchestrator."
            )
            return

        try:
            self.device_info = json.loads(device_info_json)
        except json.JSONDecodeError as e:
            self.failed(
                f"Framework Error: Could not parse device info JSON from environment variable DEVICE_INFO: {e}\n"
                f"Raw content: {device_info_json}"
            )
            return

        # Validate device info has all required fields before proceeding
        # This catches resolver bugs early with clear error messages
        try:
            validate_device_inventory([self.device_info])
        except ValueError as e:
            self.failed(f"Framework Error: Device validation failed.\n{e}")
            return

        # try:
        #     with open(data_file_path, "r") as f:
        #         self.data_model = json.load(f)
        # except FileNotFoundError:
        #     self.failed(
        #         f"Framework Error: Could not find data model file at path: {data_file_path}"
        #     )
        #     return
        # except json.JSONDecodeError as e:
        #     try:
        #         with open(data_file_path, "r") as f:
        #             file_content = f.read()
        #     except Exception:
        #         file_content = "[Could not read file content]"

        #     self.failed(
        #         f"Framework Error: Could not parse JSON from data model file '{data_file_path}': {e}\n"
        #         f"File content: {file_content}"
        #     )
        #     return

        # The BrokerClient communicates with the centralized connection broker
        # We'll attach it to the runtime object for the test's duration
        # NOTE: hasattr is correct here — self.parent is a PyATS TestItem
        # (external framework object) with no guaranteed attribute schema.
        if not hasattr(self.parent, "broker_client"):
            self.parent.broker_client = BrokerClient()
        self.broker_client = self.parent.broker_client

        try:
            hostname = self.device_info["hostname"]
        except KeyError:
            self.failed(
                "Framework Error: device_info from resolver MUST contain a 'hostname' field. "
                "This is a required field per the nac-test contract."
            )
            return

        # Store hostname early so testbed_device property can use it
        self.hostname = hostname

        # Add hostname to metadata as a separate field for d2d tests
        # Do this BEFORE connection attempt so it's set even if connection fails
        if self.result_collector is not None:
            self.result_collector.metadata["hostname"] = hostname

        # The rest of the setup is async, we'll run it in the event loop
        try:
            # Get or create event loop for Python 3.10+ compatibility
            loop = get_or_create_event_loop()
            loop.run_until_complete(self._async_setup(hostname))
        except ConnectionError as e:
            # Connection failed - fail the test with clear message
            self.failed(str(e))
            return

    async def _async_setup(self, hostname: str) -> None:
        """Helper for async setup operations with connection error handling."""
        # 1. Enforce testbed invariant: both broker mode (needs testbed for Genie
        # parser support) and direct mode (connects via testbed) require it.
        if not self.testbed_device:
            raise ConnectionError(
                f"No testbed device available for {hostname}. "
                "SSHTestBase requires a PyATS testbed device in both broker "
                "and direct connection modes."
            )

        # 2. Create command cache early (needed by broker patch)
        self.command_cache = CommandCache(hostname)

        try:
            # Check if broker is active (priority over testbed to enable connection pooling)
            broker_socket_env = os.environ.get("NAC_TEST_BROKER_SOCKET")
            broker_socket = Path(broker_socket_env) if broker_socket_env else None

            if broker_socket is not None and not broker_socket.is_socket():
                self.logger.warning(
                    f"NAC_TEST_BROKER_SOCKET is set but {broker_socket} is not a valid "
                    f"Unix socket, falling back to direct connection"
                )
                broker_socket = None

            if broker_socket is not None:
                # Use broker client for connection management
                self.logger.info(
                    f"Connecting to device {hostname} via connection broker"
                )
                # Connect to broker service
                await self.broker_client.connect()

                # Create broker command executor for this device
                self.connection = BrokerCommandExecutor(hostname, self.broker_client)

                # Ensure device connection through broker
                await self.connection.connect()

                # Patch testbed device execute so ALL commands (both explicit
                # test calls and Genie supplementary calls) route through the
                # broker. This is the unified execution path in broker mode.
                self._patch_device_execute_for_broker()
            else:
                # Connect via testbed to enable Genie features
                self.logger.info(f"Connecting to device {hostname} via PyATS testbed")
                loop = get_or_create_event_loop()
                await loop.run_in_executor(None, self.testbed_device.connect)
                # Store the testbed device connection for command execution
                self.connection = self.testbed_device

        except ConnectionError:
            # Already logged at source (broker or testbed layer) — just re-raise
            raise
        except Exception as e:
            # Unexpected error — log here since no lower layer will have done so
            error_msg = f"Failed to connect to {hostname}: {e}"
            self.logger.error(error_msg)
            raise ConnectionError(error_msg) from e

        # 3. Create and attach the execute_command helper method
        self.execute_command = self._create_execute_command_method(self.command_cache)

        # 4. Attach device_data for easy access in the test
        self.device_data = self.device_info
        # hostname already set in setup_ssh_context

    async def parse_output(
        self, command: str, output: str | None = None
    ) -> dict[str, Any] | None:
        """Parse command output using Genie parser if available.

        This method attempts to use Genie parsers when a PyATS testbed is available.
        If no testbed is available or parsing fails, it returns None.

        Runs Genie's synchronous parse() in a worker thread so that any
        supplementary device.execute() calls (patched to route through the
        broker) can safely use run_coroutine_threadsafe without deadlocking.

        Args:
            command: The command whose output should be parsed
            output: Optional pre-fetched command output. If not provided,
                   the command will be executed by Genie directly.

        Returns:
            Parsed output dictionary if successful, None otherwise.
        """
        if not self.testbed_device:
            return None
        try:
            loop = get_or_create_event_loop()
            device = self.testbed_device
            result = await loop.run_in_executor(
                None, lambda: device.parse(command, output=output)
            )
            return dict(result) if result is not None else None
        except Exception as e:
            self.logger.warning(f"Genie parser failed for '{command}': {e}")
            return None

    def _patch_device_execute_for_broker(self) -> None:
        """Patch testbed_device.execute to route all commands through the broker.

        This makes testbed_device.execute the unified execution engine in broker
        mode. Both explicit test commands (via execute_command → run_in_executor)
        and Genie's internal supplementary calls route through the same path.

        The patched method includes command caching so that supplementary commands
        fired by Genie parsers also benefit from the cache (avoiding duplicate
        round-trips to the device).

        Must be called after command_cache is created and from an async context
        (the event loop must be running) so we can capture a reference to it
        for run_coroutine_threadsafe.
        """
        broker_client = self.broker_client
        # cast: both are guaranteed non-None — hostname is set in setup_ssh_context
        # and command_cache is created at the top of _async_setup, before this call.
        hostname: str = cast(str, self.hostname)
        command_cache: CommandCache = cast(CommandCache, self.command_cache)
        test_instance = self
        loop = get_or_create_event_loop()

        def broker_execute(cmd: str, *args: Any, **kwargs: Any) -> str:
            """Sync execute that routes through the connection broker.

            Called from a worker thread (via run_in_executor in execute_command
            or parse_output), not from the event loop thread, to avoid deadlock.

            Includes caching so Genie supplementary calls don't re-execute
            commands already fetched by the test.

            Args:
                cmd: CLI command string to execute on the device.
                *args: Ignored — accepted for Unicon API compatibility.
                **kwargs: Ignored — accepted for Unicon API compatibility.

            Returns:
                Command output string from the device (or cache).

            Raises:
                TimeoutError: If the broker does not respond within
                    DEVICE_EXECUTE_TIMEOUT seconds.
            """
            # Check cache (thread-safe)
            cached = command_cache.get(cmd)
            if cached is not None:
                test_instance.logger.debug(
                    f"broker_execute cache hit for '{cmd}' on {hostname}"
                )
                return cached

            future = asyncio.run_coroutine_threadsafe(
                broker_client.execute_command(hostname, cmd), loop
            )
            try:
                output = future.result(timeout=DEVICE_EXECUTE_TIMEOUT)
            except (TimeoutError, concurrent.futures.TimeoutError):
                future.cancel()
                raise

            # Cache the result (thread-safe)
            command_cache.set(cmd, output)
            return output

        self.testbed_device.execute = broker_execute  # type: ignore[union-attr]
        # Mark device as "connected" so Genie's parse() will use device.execute()
        # instead of requiring pre-fetched output. The actual connection is managed
        # by the broker, but Genie checks device.connected before executing.
        self.testbed_device.connected = True  # type: ignore[union-attr]
        # Also patch connectionmgr.is_connected since Genie may check that
        self.testbed_device.connectionmgr.is_connected = lambda *args, **kwargs: True  # type: ignore[union-attr]

        # Genie's _get_parser_output accesses device.cli as a connection handle
        # that has an execute() method. Create a thin shim that delegates to our
        # broker_execute so parsers calling device.cli.execute() work correctly.
        class _BrokerCliShim:
            """Shim that mimics a Unicon connection for Genie parser dispatch.

            Only implements execute() — the single method Genie parsers use
            for command execution.  If a future pyATS/Genie release accesses
            additional attributes on device.cli, __getattr__ raises a clear
            diagnostic instead of a bare AttributeError deep in Genie internals.
            """

            execute = staticmethod(broker_execute)

            def __getattr__(self, name: str) -> Any:
                raise AttributeError(
                    f"_BrokerCliShim does not implement '{name}'. "
                    f"This may indicate an incompatible pyATS/Genie version — "
                    f"only 'execute' is supported in broker mode."
                )

        self.testbed_device.cli = _BrokerCliShim()  # type: ignore[union-attr]
        self.logger.debug(
            f"Patched testbed_device.execute for {hostname} to route through broker"
        )

    def _create_execute_command_method(
        self, command_cache: CommandCache
    ) -> Callable[[str], Coroutine[Any, Any, str]]:
        """Create an async command execution method for the test.

        In both broker and direct modes, commands are executed via
        testbed_device.execute (which is patched in broker mode to route
        through the broker). This eliminates mode-specific branching.

        Precondition: testbed_device is guaranteed non-None — enforced by the
        invariant check at the top of _async_setup().

        Args:
            command_cache: Command cache for the device.

        Returns:
            Async method for command execution with caching and tracking.
        """
        # Capture self reference for use in the closure
        test_instance = self
        # cast: testbed_device is guaranteed non-None — broker mode requires a
        # testbed for Genie support, and direct mode connects via testbed_device.
        device: Any = cast(Any, test_instance.testbed_device)

        async def execute_command(command: str) -> str:
            """Execute command with caching and tracking.

            Args:
                command: Command to execute.

            Returns:
                Command output.
            """
            # Check cache first
            cached_output = command_cache.get(command)
            if cached_output is not None:
                logging.debug(f"Using cached output for command: {command}")
                # Track cached command execution for reporting
                test_instance._track_ssh_command(command, cached_output)
                return cached_output

            # Execute command via testbed device (unified path for both modes).
            # In broker mode: the patched broker_execute is sync — it schedules
            # the async broker call via run_coroutine_threadsafe and blocks. This
            # means explicit test commands take an extra thread-hop (event loop →
            # thread pool → back to event loop). We accept this cost because:
            #   1. The overhead (µs) is negligible vs device I/O latency (ms–s).
            #   2. parse_output() must use run_in_executor regardless (Genie's
            #      sync parse() may fire supplementary device.execute() calls),
            #      so restoring a direct-await here would only help half the
            #      broker call sites while re-introducing mode-specific branching.
            #   3. The unified path eliminates a class of mode-dependent bugs.
            # In direct mode: real pyATS device.execute (sync, via run_in_executor)
            logging.debug(f"Executing command: {command}")
            loop = get_or_create_event_loop()
            output = await loop.run_in_executor(None, device.execute, command)

            # Convert output to string to ensure consistent type
            output_str = str(output)

            # Cache the output
            command_cache.set(command, output_str)

            # Track the command execution for reporting
            test_instance._track_ssh_command(command, output_str)

            return output_str

        return execute_command

    def _track_ssh_command(self, command: str, output: str) -> None:
        """Track SSH command execution for HTML reporting.

        This method integrates with the base class's result collector to track
        SSH commands for the HTML report generation.

        Args:
            command: The command that was executed
            output: The command output
        """
        if self.result_collector is None or self.device_info is None:
            # Safety check - collector/device_info might not be initialized in some edge cases
            return

        try:
            # Get device name from device info
            _fallback = "Unknown Device"
            device_name: str = (
                self.device_info.get(
                    "hostname", self.device_info.get("host", _fallback)
                )
                or _fallback
            )

            # Get current test context if available (set by base class methods)
            test_context = self._current_test_context

            # Track the command execution using the base class's result collector
            self.result_collector.add_command_api_execution(
                device_name=device_name,
                command=command,
                output=output[:50000],  # Pre-truncate to 50KB to prevent memory issues
                data=None,  # SSH commands don't have structured data like APIs
                test_context=test_context,
            )

            # Log at debug level
            self.logger.debug(f"Tracked SSH command: {command} on {device_name}")

        except Exception as e:
            # Don't let tracking errors break the test
            self.logger.warning(f"Failed to track SSH command: {e}")

    def run_async_verification_test(self, steps: Any) -> None:
        """Run async verification test using existing event loop.

        This method orchestrates the async verification process for SSH-based tests:
        1. Uses the existing event loop (created in SSHTestBase.setup)
        2. Calls NACTestBase.run_verification_async() to execute verifications
        3. Calls NACTestBase.process_results_smart() to process results
        4. Handles SSH-specific cleanup (broker client and connections)

        The actual verification logic is handled by:
        - get_items_to_verify() - implemented by the test class
        - verify_item() - implemented by the test class

        Args:
            steps: PyATS steps object for test reporting

        Note:
            This method does NOT close the event loop as it's managed by the
            PyATS framework. The loop was created in setup() and will be
            properly cleaned up by the framework.
        """
        loop = get_or_create_event_loop()

        try:
            # Call the base class generic orchestration
            results = loop.run_until_complete(self.run_verification_async())

            # Process results using smart configuration-driven processing
            self.process_results_smart(results, steps)

        finally:
            # SSH-specific cleanup
            try:
                # NOTE: When using broker, do NOT disconnect here!
                # The broker manages connection lifecycle and keeps connections alive
                # across tests for pooling efficiency.
                # The broker will clean up all connections when it shuts down.

                # Only disconnect for non-broker connections
                if (
                    self.connection is not None
                    and not isinstance(self.connection, BrokerCommandExecutor)
                    and hasattr(
                        self.connection, "disconnect"
                    )  # Check duck-typed method on external PyATS object
                ):
                    self.logger.debug("Disconnecting non-broker connection")
                    loop.run_until_complete(self.connection.disconnect())

            except Exception as e:
                # Log cleanup errors but don't fail the test
                self.logger.warning(f"Error during SSH cleanup: {e}")

            # Note: We do NOT close the event loop here as it's managed by PyATS
