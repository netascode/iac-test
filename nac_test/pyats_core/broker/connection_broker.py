# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Connection broker service for managing persistent device connections.

This service runs as a long-lived daemon process that:
1. Loads a consolidated testbed with all devices
2. Manages persistent pyATS testbed connections
3. Provides command execution API via Unix socket
4. Handles connection pooling and resource limits
"""

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from nac_test.pyats_core.constants import (
    BROKER_SHUTDOWN_DEVICE_TIMEOUT,
    MAX_BROKER_MESSAGE_BYTES,
)
from nac_test.pyats_core.ssh.command_cache import CommandCache
from nac_test.utils import get_or_create_event_loop

logger = logging.getLogger(__name__)


class ConnectionBroker:
    """Broker service that manages persistent device connections."""

    def __init__(
        self,
        testbed_path: Path | None = None,
        socket_path: Path | None = None,
        max_connections: int = 50,
        output_dir: Path | None = None,
    ):
        """Initialize the connection broker.

        Args:
            testbed_path: Path to consolidated testbed YAML file
            socket_path: Path for Unix domain socket (auto-generated if None)
            max_connections: Maximum concurrent connections to maintain
            output_dir: Directory for Unicon CLI logs (defaults to system temp dir if None)
        """
        self.testbed_path = testbed_path
        self.socket_path = socket_path or self._generate_socket_path()
        self.max_connections = max_connections
        self.output_dir = (
            Path(output_dir) if output_dir else Path(tempfile.gettempdir())
        )

        # Connection management
        self.testbed: Any | None = None
        self.connected_devices: dict[str, Any] = {}  # hostname -> device connection
        self._device_locks: dict[str, asyncio.Lock] = {}
        self.connection_semaphore = asyncio.Semaphore(max_connections)

        # Command caching - shared across all clients
        self.command_cache: dict[str, CommandCache] = {}  # hostname -> CommandCache

        # Socket server
        self.server: asyncio.Server | None = None
        self.active_clients: set[asyncio.StreamWriter] = set()

        # Shutdown flag
        self._shutdown_event = asyncio.Event()

        # Statistics tracking
        self.stats_connection_cache_hits = 0
        self.stats_connection_cache_misses = 0
        self.stats_command_cache_hits = 0
        self.stats_command_cache_misses = 0

    def _generate_socket_path(self) -> Path:
        """Generate a unique socket path in temp directory."""
        temp_dir = Path(tempfile.gettempdir())
        return temp_dir / f"nac_test_broker_{os.getpid()}.sock"

    async def start(self) -> None:
        """Start the broker service."""
        logger.info(f"Starting connection broker with socket: {self.socket_path}")

        # Load testbed if provided
        if self.testbed_path:
            await self._load_testbed()

        # Start Unix socket server
        await self._start_socket_server()

        logger.info("Connection broker started successfully")

    async def _load_testbed(self) -> None:
        """Load pyATS testbed from YAML file."""
        try:
            # Import pyATS components here to delay initialization
            from pyats.topology import loader

            logger.info(f"Loading testbed from: {self.testbed_path}")

            # Load testbed using pyATS loader
            self.testbed = loader.load(str(self.testbed_path))
            assert self.testbed is not None, "loader.load() should never return None"

            logger.info(f"Loaded testbed with {len(self.testbed.devices)} devices")  # type: ignore[attr-defined]

            # Initialize per-device locks for all devices
            for hostname in self.testbed.devices:  # type: ignore[attr-defined]
                self._device_locks[hostname] = asyncio.Lock()

        except Exception as e:
            logger.error(f"Failed to load testbed: {e}", exc_info=True)
            raise

    async def _start_socket_server(self) -> None:
        """Start Unix domain socket server for client communication."""
        # Remove existing socket file if it exists
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Create socket server
        self.server = await asyncio.start_unix_server(
            self._handle_client, path=str(self.socket_path)
        )

        # Set socket permissions (readable/writable by owner only)
        os.chmod(self.socket_path, 0o600)

        logger.info(f"Socket server listening on: {self.socket_path}")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle incoming client connections."""
        client_addr = writer.get_extra_info("peername", "unknown")
        logger.debug(f"Client connected: {client_addr}")

        self.active_clients.add(writer)

        try:
            while not self._shutdown_event.is_set():
                # Read message length (4 bytes, big-endian)
                length_data = await reader.readexactly(4)
                message_length = int.from_bytes(length_data, byteorder="big")

                if message_length == 0:
                    break

                if message_length > MAX_BROKER_MESSAGE_BYTES:
                    logger.warning(
                        f"Client {client_addr} sent oversized frame "
                        f"({message_length} bytes, limit {MAX_BROKER_MESSAGE_BYTES})"
                    )
                    break

                # Read message data
                message_data = await reader.readexactly(message_length)
                message = json.loads(message_data.decode("utf-8"))

                # Process request
                response = await self._process_request(message)

                # Send response
                response_data = json.dumps(response).encode("utf-8")
                response_length = len(response_data).to_bytes(4, byteorder="big")

                writer.write(response_length + response_data)
                await writer.drain()

        except asyncio.IncompleteReadError:
            # Client disconnected normally
            logger.debug(f"Client disconnected: {client_addr}")
        except Exception as e:
            logger.error(f"Error handling client {client_addr}: {e}", exc_info=True)
        finally:
            self.active_clients.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def _process_request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Process a client request and return response."""
        try:
            command = message.get("command")

            if command == "ping":
                return {"status": "success", "result": "pong"}

            elif command == "execute":
                hostname = message.get("hostname")
                cmd_string = message.get("cmd")

                if not hostname or not cmd_string:
                    return {
                        "status": "error",
                        "error": "Missing hostname or cmd parameter",
                    }

                result = await self._execute_command(hostname, cmd_string)
                return {"status": "success", "result": result}

            elif command == "connect":
                hostname = message.get("hostname")

                if not hostname:
                    return {"status": "error", "error": "Missing hostname parameter"}

                success, error_msg = await self._ensure_connection(hostname)
                if success:
                    return {"status": "success", "result": True}
                else:
                    return {
                        "status": "error",
                        "error": error_msg or f"Failed to connect to {hostname}",
                    }

            elif command == "disconnect":
                hostname = message.get("hostname")

                if not hostname:
                    return {"status": "error", "error": "Missing hostname parameter"}

                await self._disconnect_device(hostname)
                return {"status": "success", "result": True}

            elif command == "status":
                status = await self._get_broker_status()
                return {"status": "success", "result": status}

            else:
                return {"status": "error", "error": f"Unknown command: {command}"}

        except Exception as e:
            logger.error(f"Error processing request: {e}")
            return {"status": "error", "error": str(e)}

    def _get_command_cache(self, hostname: str) -> CommandCache:
        """Get or create the broker-level command cache for a device."""
        cache = self.command_cache.get(hostname)
        if cache is None:
            cache = CommandCache(hostname, ttl=3600)  # 1 hour TTL
            self.command_cache[hostname] = cache
            logger.info(f"Created command cache for device: {hostname}")
        return cache

    async def _execute_command(self, hostname: str, cmd: str) -> str:
        """Execute command on device via established connection with caching.

        This method implements command caching at the broker level, ensuring
        that identical commands are only executed once across all test subprocesses.

        Connections are handed out without a liveness probe (see
        :meth:`_get_connection`), so a session that died since its last use
        surfaces here as a transport failure. Such a failure is healed by
        disconnecting, reconnecting and retrying the command exactly once,
        which recovers the current request instead of only cleaning up for the
        next one.

        A single per-device lock serialises the entire get-connection →
        execute → failure-handling → retry cycle.  This prevents two hazards:
        (1) interleaved PTY I/O from concurrent execute() calls on Unicon's
        non-thread-safe spawn, and (2) a stale caller tearing down a
        successor's connection during the reconnect-and-retry window.
        Cross-device parallelism is unaffected.
        """
        cache = self._get_command_cache(hostname)

        # Check cache first (no lock needed — cache is per-device and read-only here)
        cached_output = cache.get(cmd)
        if cached_output is not None:
            self.stats_command_cache_hits += 1
            logger.debug(f"Broker cache hit for '{cmd}' on {hostname}")
            return cached_output

        # Command not in cache, need to execute
        self.stats_command_cache_misses += 1
        logger.debug(f"Broker cache miss for '{cmd}' on {hostname}, executing...")

        if hostname not in self._device_locks:
            self._device_locks[hostname] = asyncio.Lock()

        async with self._device_locks[hostname]:
            # Re-check cache under lock — another caller may have populated it
            cached_output = cache.get(cmd)
            if cached_output is not None:
                self.stats_command_cache_hits += 1
                logger.debug(f"Broker cache hit (under lock) for '{cmd}' on {hostname}")
                return cached_output

            connection = await self._get_connection(hostname)
            try:
                return await self._run_and_cache(hostname, connection, cmd)
            except Exception as e:
                if not self._is_transport_failure(e):
                    raise
                logger.warning(
                    f"Transport failure executing '{cmd}' on {hostname} ({e}); "
                    f"reconnecting and retrying once"
                )

            # Final attempt — the failed attempt disconnected the device, so
            # this creates a fresh connection. Failures propagate to the caller.
            connection = await self._get_connection(hostname)
            return await self._run_and_cache(hostname, connection, cmd)

    async def _run_and_cache(self, hostname: str, connection: Any, cmd: str) -> str:
        """Run a command on a connection once and cache its output.

        The caller must hold ``_device_locks[hostname]``.

        Raises:
            SubCommandFailure: Re-raised as-is. The device answered and rejected
                the command, so the session and its cache are left intact.
            Exception: Any other failure, after tearing the connection down so it
                is not handed to the next caller.
        """
        from unicon.core.errors import SubCommandFailure

        # Execute command in thread pool (since Unicon is synchronous)
        loop = get_or_create_event_loop()
        try:
            output = await loop.run_in_executor(None, connection.execute, cmd)
        except Exception as e:
            if isinstance(e, SubCommandFailure):
                logger.warning(f"Command rejected by {hostname} (session intact): {e}")
                raise
            logger.error(f"Command execution failed on {hostname}: {e}")
            await self._disconnect_device_internal(hostname)
            raise

        output_str = str(output)

        # Cache the output for future requests. The cache is re-resolved rather
        # than reused because a preceding failed attempt drops it on disconnect.
        self._get_command_cache(hostname).set(cmd, output_str)
        logger.info(
            f"Cached command output for '{cmd}' on {hostname} ({len(output_str)} chars)"
        )

        return output_str

    @staticmethod
    def _is_transport_failure(error: Exception) -> bool:
        """Check whether an error indicates a dead or desynchronized session.

        Only these failures are worth a reconnect-and-retry. Anything else
        would fail identically on a fresh connection: a ``SubCommandFailure``
        means the device answered and rejected the command, and
        ``CredentialsExhaustedError`` or a bug in the broker will not be fixed
        by a new session.
        """
        from unicon.core.errors import EOF as UniconEOF
        from unicon.core.errors import (
            ConnectionError as UniconConnectionError,
        )
        from unicon.core.errors import SessionConnectionError, StateMachineError

        return isinstance(
            error,
            (
                OSError,
                UniconConnectionError,
                SessionConnectionError,
                UniconEOF,
                StateMachineError,
            ),
        )

    async def _get_connection(self, hostname: str) -> Any:
        """Get or create connection to device.

        When called from ``_execute_command`` the caller already holds
        ``_device_locks[hostname]``, so this method must not re-acquire it.

        A cached connection is returned as-is, without probing it. Evaluating
        ``device.connected`` performs a live SSH round-trip (~0.37s) on the
        broker's event loop, blocking traffic for every device, and it cannot
        rule out the session dying between the probe and the command anyway.
        Dead sessions are detected and healed by ``_execute_command``'s
        reconnect-and-retry instead.
        """
        # Return existing connection
        if hostname in self.connected_devices:
            self.stats_connection_cache_hits += 1
            logger.info(
                f"[BROKER] Reusing existing connection for {hostname} "
                f"(total connections: {len(self.connected_devices)})"
            )
            return self.connected_devices[hostname]

        # Create new connection
        self.stats_connection_cache_misses += 1
        logger.info(
            f"[BROKER] Creating NEW connection for {hostname} "
            f"(current connections: {len(self.connected_devices)})"
        )
        return await self._create_connection(hostname)

    async def _create_connection(self, hostname: str) -> Any:
        """Create new connection to device using testbed."""
        if not self.testbed:
            raise ConnectionError(f"No testbed loaded for {hostname}")
        if hostname not in self.testbed.devices:
            raise ConnectionError(f"Device {hostname} not found in testbed")

        async with self.connection_semaphore:
            try:
                device = self.testbed.devices[hostname]
                logger.info(f"Connecting to device: {hostname}")

                # Create unique log file path in output directory
                import time

                timestamp = (
                    int(time.time() * 1000000) % 10000000000
                )  # Last 10 digits of microsecond timestamp
                logfile_path = self.output_dir / f"{hostname}-cli-{timestamp}.log"

                logger.info(f"Unicon CLI log will be written to: {logfile_path}")

                # Connect using pyATS testbed with custom logfile location
                loop = get_or_create_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: device.connect(log_stdout=False, logfile=str(logfile_path)),
                )

                # Store connection
                self.connected_devices[hostname] = device
                logger.info(f"Successfully connected to device: {hostname}")

                return device

            except Exception as e:
                # pyATS exceptions often embed the hostname already
                # (e.g. "failed to connect to iosxe-r1"), so only prepend
                # our "Failed to connect to <host>:" prefix when the
                # hostname is absent — otherwise the log looks redundant.
                msg = f"{type(e).__name__}: {e}"
                if hostname not in str(e):
                    msg = f"Failed to connect to {hostname}: {msg}"
                logger.error(msg)
                raise

    async def _ensure_connection(self, hostname: str) -> tuple[bool, str]:
        """Ensure device is connected, return (success, error_message)."""
        if hostname not in self._device_locks:
            self._device_locks[hostname] = asyncio.Lock()

        try:
            async with self._device_locks[hostname]:
                await self._get_connection(hostname)
            return True, ""
        except Exception as e:
            return False, str(e)

    async def _disconnect_device(self, hostname: str) -> None:
        """Disconnect from device and clean up."""
        if hostname in self._device_locks:
            async with self._device_locks[hostname]:
                await self._disconnect_device_internal(hostname)

    async def _disconnect_device_internal(self, hostname: str) -> None:
        """Internal disconnect without locking."""
        if hostname in self.connected_devices:
            try:
                connection = self.connected_devices[hostname]
                loop = get_or_create_event_loop()
                await loop.run_in_executor(None, connection.disconnect)
                logger.info(f"Disconnected from device: {hostname}")
            except Exception as e:
                logger.warning(f"Error disconnecting from {hostname}: {e}")
            finally:
                del self.connected_devices[hostname]

        # Clear command cache for this device when disconnecting
        if hostname in self.command_cache:
            cache_stats = self.command_cache[hostname].get_cache_stats()
            logger.info(f"Clearing command cache for {hostname}: {cache_stats}")
            del self.command_cache[hostname]

    async def _get_broker_status(self) -> dict[str, Any]:
        """Get broker status information."""
        # Collect cache statistics for all devices
        cache_stats = {}
        total_cached_commands = 0

        for hostname, cache in self.command_cache.items():
            stats = cache.get_cache_stats()
            cache_stats[hostname] = stats
            total_cached_commands += stats["valid_entries"]

        return {
            "socket_path": str(self.socket_path),
            "max_connections": self.max_connections,
            "connected_devices": list(self.connected_devices.keys()),
            "active_clients": len(self.active_clients),
            "testbed_loaded": self.testbed is not None,
            "testbed_devices": list(self.testbed.devices.keys())
            if self.testbed
            else [],
            "command_cache_stats": {
                "devices_with_cache": list(self.command_cache.keys()),
                "total_cached_commands": total_cached_commands,
                "per_device_stats": cache_stats,
            },
        }

    async def shutdown(self) -> None:
        """Shutdown the broker service."""
        logger.info("Shutting down connection broker...")

        # Signal shutdown
        self._shutdown_event.set()

        # Close all client connections
        for writer in list(self.active_clients):
            writer.close()
            await writer.wait_closed()

        # Disconnect all devices concurrently with per-device timeouts.
        # Serial disconnects take ~10-12s each (Unicon waits for graceful SSH
        # session teardown), so 19 devices serial = ~200s.  Concurrent brings
        # this down to the single slowest disconnect (~12s).  Per-device
        # timeouts prevent a stuck device lock from blocking the entire
        # shutdown.
        async def _bounded_disconnect(hostname: str) -> None:
            try:
                await asyncio.wait_for(
                    self._disconnect_device(hostname),
                    timeout=BROKER_SHUTDOWN_DEVICE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Timed out waiting for device lock on {hostname} during shutdown"
                )

        if self.connected_devices:
            await asyncio.gather(*[
                _bounded_disconnect(hostname)
                for hostname in list(self.connected_devices.keys())
            ], return_exceptions=True)

        # Stop socket server
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # Remove socket file
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to remove socket file: {e}")

        # Log statistics for validation
        logger.info(
            f"BROKER_STATISTICS: "
            f"connection_hits={self.stats_connection_cache_hits}, "
            f"connection_misses={self.stats_connection_cache_misses}, "
            f"command_hits={self.stats_command_cache_hits}, "
            f"command_misses={self.stats_command_cache_misses}"
        )

        logger.info("Connection broker shutdown complete")

    @asynccontextmanager
    async def run_context(self) -> AsyncIterator["ConnectionBroker"]:
        """Context manager for running the broker."""
        try:
            await self.start()
            yield self
        finally:
            await self.shutdown()
