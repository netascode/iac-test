# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Broker client for communicating with the connection broker service.

This client is used by test subprocesses to execute commands on devices
through the centralized connection broker.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BrokerClient:
    """Client for communicating with the connection broker service."""

    def __init__(self, socket_path: Path | None = None):
        """Initialize broker client.

        Args:
            socket_path: Path to broker's Unix domain socket.
                        If None, will look for NAC_TEST_BROKER_SOCKET env var.
        """
        self.socket_path = socket_path or self._get_socket_path()
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._connection_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._connected = False

    def _get_socket_path(self) -> Path:
        """Get socket path from environment or default location."""
        socket_env = os.environ.get("NAC_TEST_BROKER_SOCKET")
        if socket_env:
            return Path(socket_env)

        # Default: look for socket in temp directory
        import tempfile

        temp_dir = Path(tempfile.gettempdir())
        # Try to find existing broker socket
        for socket_file in temp_dir.glob("nac_test_broker_*.sock"):
            return socket_file

        raise ConnectionError(
            "No broker socket found. Set NAC_TEST_BROKER_SOCKET environment variable "
            "or ensure connection broker is running."
        )

    def _teardown_connection(self) -> None:
        """Reset connection state without acquiring the lock.

        Must be called while already holding _connection_lock. Extracted so
        that both connect()'s error path and disconnect() share identical
        teardown logic.

        Note: does not call writer.wait_closed() because on the connect()
        failure path there is nothing meaningful buffered to flush.
        disconnect() calls wait_closed() explicitly after this method returns.
        """
        if self.writer:
            self.writer.close()
        self.reader = None
        self.writer = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to the broker service."""
        async with self._connection_lock:
            if self._connected:
                return

            try:
                logger.debug(f"Connecting to broker at: {self.socket_path}")

                self.reader, self.writer = await asyncio.open_unix_connection(
                    str(self.socket_path)
                )

                self._connected = True
                logger.debug("Connected to broker successfully")

                # Test connection with ping
                await self._send_request({"command": "ping"})

            except Exception as e:
                logger.debug(f"Failed to connect to broker: {e}")
                self._teardown_connection()
                raise ConnectionError(f"Cannot connect to broker: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from the broker service."""
        async with self._connection_lock:
            writer = self.writer
            self._teardown_connection()
            if writer:
                await writer.wait_closed()

    async def _send_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send request to broker and return response.

        Uses _request_lock to prevent concurrent coroutines from interleaving
        write/read frames on the shared socket (the broker protocol is strictly
        sequential per connection).
        """
        if not self._connected:
            await self.connect()

        async with self._request_lock:
            try:
                # Serialize request
                request_data = json.dumps(request).encode("utf-8")
                request_length = len(request_data).to_bytes(4, byteorder="big")

                # Send request
                if self.writer is None:
                    raise RuntimeError("Writer must be connected")
                if self.reader is None:
                    raise RuntimeError("Reader must be connected")

                self.writer.write(request_length + request_data)
                await self.writer.drain()

                # Read response length
                response_length_data = await self.reader.readexactly(4)
                response_length = int.from_bytes(response_length_data, byteorder="big")

                # Read response data
                response_data = await self.reader.readexactly(response_length)
                response = json.loads(response_data.decode("utf-8"))

                # Check for errors
                if response.get("status") == "error":
                    error_msg = response.get("error", "Unknown broker error")
                    raise ConnectionError(f"Broker error: {error_msg}")

                return response  # type: ignore[no-any-return]

            except asyncio.CancelledError:
                # If cancelled mid-request (e.g. caller timeout), teardown socket so subsequent commands don't reuse a corrupted stream
                self._teardown_connection()
                raise
            except Exception as e:
                logger.debug(f"Error communicating with broker: {e}")
                # Reset connection on error
                await self.disconnect()
                raise

    async def execute_command(self, hostname: str, command: str) -> str:
        """Execute command on device through broker.

        Args:
            hostname: Device hostname
            command: Command to execute

        Returns:
            Command output

        Raises:
            ConnectionError: If broker communication fails
        """
        logger.debug(f"Executing command on {hostname}: {command}")

        response = await self._send_request(
            {"command": "execute", "hostname": hostname, "cmd": command}
        )

        result = response.get("result", "")
        logger.debug(f"Command output length: {len(result)} characters")

        return result  # type: ignore[no-any-return]

    async def ensure_connection(self, hostname: str) -> bool:
        """Ensure device is connected through broker.

        Args:
            hostname: Device hostname

        Returns:
            True if connection successful, False otherwise
        """
        try:
            response = await self._send_request(
                {"command": "connect", "hostname": hostname}
            )
            return response.get("result", False)  # type: ignore[no-any-return]

        except Exception as e:
            logger.debug(f"Failed to ensure connection to {hostname}: {e}")
            return False

    async def disconnect_device(self, hostname: str) -> None:
        """Disconnect device through broker.

        Args:
            hostname: Device hostname
        """
        try:
            await self._send_request({"command": "disconnect", "hostname": hostname})
            logger.debug(f"Disconnected device: {hostname}")

        except Exception as e:
            logger.warning(f"Failed to disconnect {hostname}: {e}")

    async def get_broker_status(self) -> dict[str, Any]:
        """Get broker status information.

        Returns:
            Status dictionary
        """
        response = await self._send_request({"command": "status"})
        return response.get("result", {})  # type: ignore[no-any-return]

    async def ping(self) -> bool:
        """Ping the broker to test connectivity.

        Returns:
            True if broker responds, False otherwise
        """
        try:
            response = await self._send_request({"command": "ping"})
            return response.get("result") == "pong"

        except Exception as e:
            logger.debug(f"Broker ping failed: {e}")
            return False

    async def __aenter__(self) -> "BrokerClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()


class BrokerCommandExecutor:
    """Command executor that uses broker client for device communication.

    This class provides the same interface as the direct connection approach
    but proxies all commands through the broker service.
    """

    def __init__(self, hostname: str, broker_client: BrokerClient):
        """Initialize command executor.

        Args:
            hostname: Device hostname
            broker_client: Connected broker client
        """
        self.hostname = hostname
        self.broker_client = broker_client

    async def execute(self, command: str) -> str:
        """Execute command on device via broker.

        Args:
            command: Command to execute

        Returns:
            Command output
        """
        return await self.broker_client.execute_command(self.hostname, command)

    async def connect(self) -> None:
        """Ensure device connection via broker."""
        success = await self.broker_client.ensure_connection(self.hostname)
        if not success:
            raise ConnectionError(f"Failed to connect to device: {self.hostname}")

    async def disconnect(self) -> None:
        """Disconnect device via broker."""
        await self.broker_client.disconnect_device(self.hostname)

    @property
    def connected(self) -> bool:
        """Check if connection is established (always True for broker)."""
        return True

    @property
    def spawn(self) -> bool:
        """Check if spawn is available (always True for broker)."""
        return True
