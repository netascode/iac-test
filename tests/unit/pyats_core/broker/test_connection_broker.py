# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for ConnectionBroker.

Async methods are tested using asyncio.run() since pytest-asyncio is not
installed in this project (see test_subprocess_runner.py for the same pattern).
"""

import asyncio
import json
import logging
import struct
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nac_test.pyats_core.broker.broker_client import BrokerClient, BrokerCommandExecutor
from nac_test.pyats_core.broker.connection_broker import ConnectionBroker


@pytest.fixture
def broker(tmp_path: Path) -> ConnectionBroker:
    """Return a ConnectionBroker with a pre-wired fake testbed (no real pyATS)."""
    b = ConnectionBroker(output_dir=tmp_path)
    b.testbed = MagicMock()
    b.testbed.devices = {"router-1": MagicMock(), "router-2": MagicMock()}
    for hostname in b.testbed.devices:
        b._device_locks[hostname] = asyncio.Lock()
    return b


# ---------------------------------------------------------------------------
# _create_connection — error logging (#539)
# ---------------------------------------------------------------------------


def _run_failing_connect(
    broker: ConnectionBroker, hostname: str, exc: Exception
) -> None:
    """Drive _create_connection to failure via a mocked device.connect()."""

    async def _run() -> None:
        loop = asyncio.get_event_loop()
        assert broker.testbed is not None
        broker.testbed.devices[hostname].connect.side_effect = exc
        with patch(
            "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
            return_value=loop,
        ):
            await broker._create_connection(hostname)

    with pytest.raises(type(exc)):
        asyncio.run(_run())


def test_create_connection_logs_fixed_format_error(
    broker: ConnectionBroker, caplog: pytest.LogCaptureFixture
) -> None:
    """Error log uses fixed format: 'Failed to connect to {hostname}: {type}: {msg}'."""
    with caplog.at_level(logging.ERROR):
        _run_failing_connect(broker, "router-1", ConnectionError("timed out after 60s"))

    assert len(caplog.records) == 1
    assert "Failed to connect to router-1" in caplog.records[0].message
    assert "ConnectionError" in caplog.records[0].message
    assert "timed out after 60s" in caplog.records[0].message


def test_create_connection_omits_redundant_hostname(
    broker: ConnectionBroker, caplog: pytest.LogCaptureFixture
) -> None:
    """Hostname prefix is suppressed when pyATS already embeds it in the error."""
    with caplog.at_level(logging.ERROR):
        _run_failing_connect(
            broker, "router-1", ConnectionError("failed to connect to router-1")
        )

    assert len(caplog.records) == 1
    msg = caplog.records[0].message
    # The hostname prefix must NOT be prepended (pyATS already includes it)
    assert not msg.startswith("Failed to connect to router-1:")
    assert "ConnectionError" in msg
    assert "failed to connect to router-1" in msg


# ---------------------------------------------------------------------------
# _process_request routing
# ---------------------------------------------------------------------------


class TestProcessRequest:
    def test_ping(self, broker: ConnectionBroker) -> None:
        result = asyncio.run(broker._process_request({"command": "ping"}))
        assert result == {"status": "success", "result": "pong"}

    def test_unknown_command(self, broker: ConnectionBroker) -> None:
        result = asyncio.run(broker._process_request({"command": "frobnicate"}))
        assert result["status"] == "error"
        assert "frobnicate" in result["error"]

    def test_connect_missing_hostname(self, broker: ConnectionBroker) -> None:
        result = asyncio.run(broker._process_request({"command": "connect"}))
        assert result["status"] == "error"
        assert "hostname" in result.get("error", "").lower()

    def test_execute_missing_params(self, broker: ConnectionBroker) -> None:
        result = asyncio.run(
            broker._process_request({"command": "execute", "hostname": "router-1"})
        )
        assert result["status"] == "error"
        assert "cmd" in result.get("error", "").lower()

    def test_connect_failure_propagates_error_message(
        self, broker: ConnectionBroker
    ) -> None:
        """Error message from _ensure_connection is surfaced in the response."""
        broker._ensure_connection = AsyncMock(  # type: ignore[method-assign]
            return_value=(False, "failed to connect to router-1")
        )
        result = asyncio.run(
            broker._process_request({"command": "connect", "hostname": "router-1"})
        )
        assert result["status"] == "error"
        assert "router-1" in result["error"]

    def test_execute_calls_execute_command(self, broker: ConnectionBroker) -> None:
        broker._execute_command = AsyncMock(return_value="show version output")  # type: ignore[method-assign]
        result = asyncio.run(
            broker._process_request(
                {"command": "execute", "hostname": "router-1", "cmd": "show version"}
            )
        )
        assert result == {"status": "success", "result": "show version output"}
        broker._execute_command.assert_called_once_with("router-1", "show version")


# ---------------------------------------------------------------------------
# _get_connection — cache hit/miss and stats
# ---------------------------------------------------------------------------


class TestGetConnection:
    def test_returns_existing_connection(self, broker: ConnectionBroker) -> None:
        conn = MagicMock()
        broker.connected_devices["router-1"] = conn

        result = asyncio.run(broker._get_connection("router-1"))

        assert result is conn
        assert broker.stats_connection_cache_hits == 1
        assert broker.stats_connection_cache_misses == 0

    def test_does_not_probe_cached_connection(self, broker: ConnectionBroker) -> None:
        """The cached connection is handed out without touching device.connected.

        Evaluating that property costs a live SSH round-trip on the event loop
        (#909); a dead session is healed by _execute_command's retry instead.
        """
        conn = MagicMock()
        probed = []

        def _connected(_self: Any) -> bool:
            probed.append(True)
            return True

        type(conn).connected = property(_connected)
        broker.connected_devices["router-1"] = conn
        broker._create_connection = AsyncMock()  # type: ignore[method-assign]

        result = asyncio.run(broker._get_connection("router-1"))

        assert result is conn
        assert probed == []
        broker._create_connection.assert_not_called()

    def test_creates_new_connection_when_none_exists(
        self, broker: ConnectionBroker
    ) -> None:
        fresh_conn = MagicMock()
        broker._create_connection = AsyncMock(return_value=fresh_conn)  # type: ignore[method-assign]

        result = asyncio.run(broker._get_connection("router-1"))

        assert result is fresh_conn
        assert broker.stats_connection_cache_misses == 1


# ---------------------------------------------------------------------------
# _execute_command — command cache hit/miss
# ---------------------------------------------------------------------------


class TestExecuteCommand:
    def test_cache_hit_skips_device(self, broker: ConnectionBroker) -> None:
        cache = MagicMock()
        cache.get.return_value = "cached output"
        broker.command_cache["router-1"] = cache
        broker._get_connection = AsyncMock()  # type: ignore[method-assign]

        result = asyncio.run(broker._execute_command("router-1", "show version"))

        assert result == "cached output"
        broker._get_connection.assert_not_called()
        assert broker.stats_command_cache_hits == 1

    def test_cache_miss_executes_and_stores(self, broker: ConnectionBroker) -> None:
        cache = MagicMock()
        cache.get.return_value = None
        broker.command_cache["router-1"] = cache

        conn = MagicMock()
        broker._get_connection = AsyncMock(return_value=conn)  # type: ignore[method-assign]

        async def _run() -> str:
            loop = asyncio.get_event_loop()
            with patch(
                "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                return_value=loop,
            ):
                with patch.object(
                    loop,
                    "run_in_executor",
                    new_callable=AsyncMock,
                    return_value="live output",
                ):
                    return await broker._execute_command("router-1", "show version")

        result = asyncio.run(_run())

        assert result == "live output"
        cache.set.assert_called_once_with("show version", "live output")
        assert broker.stats_command_cache_misses == 1

    def test_cache_miss_raises_when_connection_fails(
        self, broker: ConnectionBroker
    ) -> None:
        cache = MagicMock()
        cache.get.return_value = None
        broker.command_cache["router-1"] = cache
        broker._get_connection = AsyncMock(  # type: ignore[method-assign]
            side_effect=ConnectionError("No testbed loaded for router-1")
        )

        with pytest.raises(ConnectionError):
            asyncio.run(broker._execute_command("router-1", "show version"))


# ---------------------------------------------------------------------------
# BrokerCommandExecutor
# ---------------------------------------------------------------------------


class TestBrokerCommandExecutor:
    def test_connect_raises_when_ensure_connection_fails(self) -> None:
        client = MagicMock(spec=BrokerClient)
        client.ensure_connection = AsyncMock(return_value=False)
        executor = BrokerCommandExecutor("router-1", client)

        with pytest.raises(ConnectionError):
            asyncio.run(executor.connect())

    def test_connect_succeeds_when_ensure_connection_true(self) -> None:
        client = MagicMock(spec=BrokerClient)
        client.ensure_connection = AsyncMock(return_value=True)
        executor = BrokerCommandExecutor("router-1", client)

        asyncio.run(executor.connect())  # should not raise


# ---------------------------------------------------------------------------
# _create_connection — success path
# ---------------------------------------------------------------------------


def test_create_connection_stores_device_on_success(
    broker: ConnectionBroker, tmp_path: Path
) -> None:
    """Successful connect stores the device in connected_devices."""

    async def _run() -> None:
        loop = asyncio.get_event_loop()
        assert broker.testbed is not None
        broker.testbed.devices["router-1"].connect.return_value = None
        with patch(
            "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
            return_value=loop,
        ):
            await broker._create_connection("router-1")

    asyncio.run(_run())
    assert "router-1" in broker.connected_devices


# ---------------------------------------------------------------------------
# _execute_command — reconnect on failure
# ---------------------------------------------------------------------------


def test_execute_command_disconnects_on_execution_failure(
    broker: ConnectionBroker,
) -> None:
    """When command execution raises, the device is disconnected before re-raising."""
    cache = MagicMock()
    cache.get.return_value = None
    broker.command_cache["router-1"] = cache
    broker._get_connection = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
    broker._disconnect_device_internal = AsyncMock()  # type: ignore[method-assign]

    async def _run() -> None:
        loop = asyncio.get_event_loop()
        with patch(
            "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
            return_value=loop,
        ):
            with patch.object(
                loop,
                "run_in_executor",
                new_callable=AsyncMock,
                side_effect=Exception("timeout"),
            ):
                await broker._execute_command("router-1", "show version")

    with pytest.raises(Exception, match="timeout"):
        asyncio.run(_run())

    broker._disconnect_device_internal.assert_called_once_with("router-1")


# ---------------------------------------------------------------------------
# _execute_command — reconnect-and-retry on transport failure (#909)
# ---------------------------------------------------------------------------


def _run_execute(
    broker: ConnectionBroker, hostname: str, cmd: str, side_effect: Any
) -> str:
    """Drive _execute_command with run_in_executor stubbed by side_effect."""

    async def _run() -> str:
        loop = asyncio.get_event_loop()
        with patch(
            "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
            return_value=loop,
        ):
            with patch.object(
                loop,
                "run_in_executor",
                new_callable=AsyncMock,
                side_effect=side_effect,
            ):
                return await broker._execute_command(hostname, cmd)

    return asyncio.run(_run())


class TestExecuteCommandRetry:
    """A session that died since its last use must heal the current request."""

    def test_retries_once_after_transport_failure(
        self, broker: ConnectionBroker
    ) -> None:
        from unicon.core.errors import ConnectionError as UniconConnectionError

        broker._get_connection = AsyncMock(side_effect=[MagicMock(), MagicMock()])  # type: ignore[method-assign]
        broker._disconnect_device_internal = AsyncMock()  # type: ignore[method-assign]

        result = _run_execute(
            broker,
            "router-1",
            "show version",
            [UniconConnectionError("session closed"), "live output"],
        )

        assert result == "live output"
        assert broker._get_connection.await_count == 2
        broker._disconnect_device_internal.assert_awaited_once_with("router-1")

    def test_retry_result_is_cached(self, broker: ConnectionBroker) -> None:
        """The retry's output lands in a live cache, not the one disconnect dropped."""
        from unicon.core.errors import ConnectionError as UniconConnectionError

        broker._get_connection = AsyncMock(side_effect=[MagicMock(), MagicMock()])  # type: ignore[method-assign]

        # Real disconnect path: drops the command cache for the device
        async def _disconnect(hostname: str) -> None:
            broker.command_cache.pop(hostname, None)

        broker._disconnect_device_internal = AsyncMock(side_effect=_disconnect)  # type: ignore[method-assign]

        result = _run_execute(
            broker,
            "router-1",
            "show version",
            [UniconConnectionError("session closed"), "live output"],
        )

        assert result == "live output"
        assert broker.command_cache["router-1"].get("show version") == "live output"

    def test_raises_when_retry_also_fails(self, broker: ConnectionBroker) -> None:
        from unicon.core.errors import ConnectionError as UniconConnectionError

        broker._get_connection = AsyncMock(side_effect=[MagicMock(), MagicMock()])  # type: ignore[method-assign]
        broker._disconnect_device_internal = AsyncMock()  # type: ignore[method-assign]

        with pytest.raises(UniconConnectionError):
            _run_execute(
                broker,
                "router-1",
                "show version",
                [
                    UniconConnectionError("session closed"),
                    UniconConnectionError("still closed"),
                ],
            )

        assert broker._get_connection.await_count == 2
        # Both attempts tear the connection down; nothing dead is left cached
        assert broker._disconnect_device_internal.await_count == 2

    def test_does_not_retry_command_rejection(self, broker: ConnectionBroker) -> None:
        """A rejected command is not retried - a fresh session rejects it too.

        Whether a rejection should also skip the disconnect is a separate
        question, handled in #900.
        """
        from unicon.core.errors import SubCommandFailure

        broker._get_connection = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
        broker._disconnect_device_internal = AsyncMock()  # type: ignore[method-assign]

        with pytest.raises(SubCommandFailure):
            _run_execute(
                broker, "router-1", "show bogus", SubCommandFailure("invalid input")
            )

        assert broker._get_connection.await_count == 1

    def test_does_not_retry_unclassified_failure(
        self, broker: ConnectionBroker
    ) -> None:
        """An error that is neither rejection nor transport disconnects but is not retried."""
        broker._get_connection = AsyncMock(return_value=MagicMock())  # type: ignore[method-assign]
        broker._disconnect_device_internal = AsyncMock()  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="broker bug"):
            _run_execute(broker, "router-1", "show version", RuntimeError("broker bug"))

        assert broker._get_connection.await_count == 1
        broker._disconnect_device_internal.assert_awaited_once_with("router-1")

    def test_retry_path_stays_under_device_lock(self, broker: ConnectionBroker) -> None:
        """_get_connection on both attempts must run under _device_locks[hostname].

        This pins the contract that _get_connection and _run_and_cache are
        lock-free — callers hold the lock. If a future change narrows or
        drops the lock in _execute_command, this test catches it.
        """
        from unicon.core.errors import ConnectionError as UniconConnectionError

        observed: list[bool] = []
        broker._disconnect_device_internal = AsyncMock()  # type: ignore[method-assign]

        async def _spy(hostname: str) -> MagicMock:
            observed.append(broker._device_locks[hostname].locked())
            return MagicMock()

        broker._get_connection = _spy  # type: ignore[method-assign]

        _run_execute(
            broker,
            "router-1",
            "show version",
            [UniconConnectionError("socket closed"), "live output"],
        )

        assert observed == [True, True]

    def test_connection_errors_are_not_retried(self, broker: ConnectionBroker) -> None:
        """Failure to establish a connection must not double the connect attempts."""
        broker._get_connection = AsyncMock(  # type: ignore[method-assign]
            side_effect=ConnectionError("Device router-1 not found in testbed")
        )

        with pytest.raises(ConnectionError):
            asyncio.run(broker._execute_command("router-1", "show version"))

        assert broker._get_connection.await_count == 1


class TestFailureClassification:
    def test_transport_failures(self, broker: ConnectionBroker) -> None:
        from unicon.core.errors import EOF as UniconEOF
        from unicon.core.errors import ConnectionError as UniconConnectionError
        from unicon.core.errors import SessionConnectionError, StateMachineError
        from unicon.core.errors import TimeoutError as UniconTimeoutError

        for exc in (
            UniconConnectionError("closed"),
            SessionConnectionError("session lost"),
            UniconTimeoutError("no prompt"),
            StateMachineError("lost prompt"),
            BrokenPipeError("broken pipe"),
            UniconEOF("eof"),
        ):
            assert broker._is_transport_failure(exc) is True, type(exc).__name__

    def test_non_transport_failures(self, broker: ConnectionBroker) -> None:
        from unicon.core.errors import CredentialsExhaustedError, SubCommandFailure

        for exc in (
            SubCommandFailure("invalid input"),
            CredentialsExhaustedError("no creds left"),
            EOFError("builtin eof"),
            RuntimeError("broker bug"),
            ValueError("bad value"),
        ):
            assert broker._is_transport_failure(exc) is False, type(exc).__name__


# ---------------------------------------------------------------------------
# _process_request — remaining paths
# ---------------------------------------------------------------------------


def test_process_request_status(broker: ConnectionBroker) -> None:
    """Status command returns broker status dict."""
    broker._get_broker_status = AsyncMock(return_value={"connected_devices": []})  # type: ignore[method-assign]
    result = asyncio.run(broker._process_request({"command": "status"}))
    assert result["status"] == "success"
    assert "connected_devices" in result["result"]


def test_process_request_exception_returns_error(broker: ConnectionBroker) -> None:
    """Unexpected exception in request handling returns error response, does not raise."""
    broker._ensure_connection = AsyncMock(side_effect=RuntimeError("unexpected"))  # type: ignore[method-assign]
    result = asyncio.run(
        broker._process_request({"command": "connect", "hostname": "router-1"})
    )
    assert result["status"] == "error"
    assert "unexpected" in result["error"]


# ---------------------------------------------------------------------------
# BrokerClient._get_socket_path
# ---------------------------------------------------------------------------


class TestBrokerClientGetSocketPath:
    def test_uses_env_var_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NAC_TEST_BROKER_SOCKET", "/tmp/my.sock")
        client = BrokerClient()
        assert client.socket_path == Path("/tmp/my.sock")

    def test_falls_back_to_glob(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("NAC_TEST_BROKER_SOCKET", raising=False)
        sock = tmp_path / "nac_test_broker_1234.sock"
        sock.touch()
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            client = BrokerClient()
        assert client.socket_path == sock

    def test_raises_when_no_socket_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("NAC_TEST_BROKER_SOCKET", raising=False)
        with patch("tempfile.gettempdir", return_value=str(tmp_path)):
            with pytest.raises(ConnectionError, match="No broker socket found"):
                BrokerClient()


# ---------------------------------------------------------------------------
# BrokerClient — command passthroughs
# ---------------------------------------------------------------------------


class TestBrokerClientPassthroughs:
    """Verify that high-level methods send the correct command/params to _send_request."""

    def _make_client(self) -> tuple[BrokerClient, Any]:
        client = BrokerClient.__new__(BrokerClient)
        client._connected = True
        client._connection_lock = asyncio.Lock()
        mock = AsyncMock()
        client._send_request = mock  # type: ignore[method-assign]
        return client, mock

    def test_ping_returns_false_on_failure(self) -> None:
        client, mock = self._make_client()
        mock.side_effect = ConnectionError("gone")
        assert asyncio.run(client.ping()) is False


# ---------------------------------------------------------------------------
# Connection health recovery (relocated from integration tests — no socket involved)
# ---------------------------------------------------------------------------


class TestConnectionHealthRecovery:
    def test_recovers_when_cached_connection_is_dead(
        self, broker: ConnectionBroker
    ) -> None:
        """A dead cached session is replaced and the command still succeeds.

        Exercises the full path with the real _get_connection, _disconnect_device
        and _create_connection: the stale session fails, gets torn down, a fresh
        connection is created from the testbed, and the retry returns output.
        """
        from unicon.core.errors import ConnectionError as UniconConnectionError

        stale_device = MagicMock()
        stale_device.execute.side_effect = UniconConnectionError("session is closed")
        broker.connected_devices["router-1"] = stale_device

        assert broker.testbed is not None
        fresh_device = broker.testbed.devices["router-1"]
        fresh_device.execute.return_value = "recovered output"

        async def _run() -> str:
            loop = asyncio.get_event_loop()
            with patch(
                "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                return_value=loop,
            ):

                async def fake_executor(executor: Any, func: Any, *args: Any) -> Any:
                    return func(*args)

                with patch.object(loop, "run_in_executor", side_effect=fake_executor):
                    return await broker._execute_command("router-1", "show version")

        result = asyncio.run(_run())

        assert result == "recovered output"
        assert broker.connected_devices["router-1"] is fresh_device
        stale_device.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# Disconnect cleanup (relocated from integration tests — no socket involved)
# ---------------------------------------------------------------------------


class TestDisconnectCleanup:
    def test_disconnect_cleans_up_even_when_device_disconnect_raises(
        self, broker: ConnectionBroker
    ) -> None:
        """_disconnect_device removes the device even if device.disconnect() raises."""
        device = MagicMock()
        device.disconnect.side_effect = Exception("device hung")
        broker.connected_devices["router-1"] = device

        async def _run() -> None:
            loop = asyncio.get_event_loop()
            with patch(
                "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                return_value=loop,
            ):

                async def fake_executor(executor: Any, func: Any, *args: Any) -> Any:
                    return func(*args)

                with patch.object(loop, "run_in_executor", side_effect=fake_executor):
                    await broker._disconnect_device("router-1")

        asyncio.run(_run())

        assert "router-1" not in broker.connected_devices


# ---------------------------------------------------------------------------
# BrokerClient._request_lock — concurrent request serialization (#879)
# ---------------------------------------------------------------------------


class TestBrokerClientRequestLock:
    """Verify that concurrent _send_request calls are serialized.

    Without the _request_lock, concurrent coroutines can interleave their
    write/read frames on the shared socket, causing response misattribution.
    These tests use a mock Unix socket server that echoes a unique response
    per request, and verify that concurrent gather'd calls each receive their
    own correct response — which would fail without the lock.
    """

    def test_concurrent_requests_receive_correct_responses(
        self, socket_dir: Path
    ) -> None:
        """Multiple concurrent execute_command calls must each get their own response."""
        socket_path = socket_dir / "test_lock.sock"

        async def _run() -> None:
            # --- Fake broker server: echoes hostname+command back as the result ---
            async def handle_client(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                try:
                    while True:
                        length_data = await reader.readexactly(4)
                        msg_len = int.from_bytes(length_data, byteorder="big")
                        msg_data = await reader.readexactly(msg_len)
                        request = json.loads(msg_data.decode("utf-8"))

                        # Simulate variable processing delay to provoke interleaving
                        hostname = request.get("hostname", "?")
                        cmd = request.get("cmd", "?")
                        await asyncio.sleep(0.01)

                        response = json.dumps(
                            {"status": "success", "result": f"{hostname}:{cmd}"}
                        ).encode("utf-8")
                        writer.write(struct.pack(">I", len(response)) + response)
                        await writer.drain()
                except asyncio.IncompleteReadError:
                    pass
                finally:
                    writer.close()

            server = await asyncio.start_unix_server(
                handle_client, path=str(socket_path)
            )

            try:
                # --- Client: fire N concurrent requests on a shared BrokerClient ---
                client = BrokerClient(socket_path=socket_path)
                await client.connect()

                try:
                    num_requests = 20
                    tasks = [
                        client.execute_command(f"device-{i}", f"show cmd-{i}")
                        for i in range(num_requests)
                    ]
                    results = await asyncio.gather(*tasks)

                    # Each result must match exactly the request that produced it
                    for i, result in enumerate(results):
                        assert result == f"device-{i}:show cmd-{i}", (
                            f"Request {i} got wrong response: {result!r}. "
                            f"This indicates request/response interleaving — "
                            f"the _request_lock may be missing or broken."
                        )
                finally:
                    await client.disconnect()
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# BrokerClient cancellation and recovery (#925)
# ---------------------------------------------------------------------------


class TestBrokerClientCancellation:
    """Verify that cancelled requests tear down connection state without deadlocks."""

    def test_cancellation_resets_connection_and_allows_subsequent_commands(
        self, socket_dir: Path
    ) -> None:
        """Cancelled request tears down connection so next command does not queue or corrupt (#925)."""
        socket_path = socket_dir / "test_cancel.sock"

        async def _run() -> None:
            # --- Fake broker server ---
            async def handle_client(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                try:
                    while True:
                        length_data = await reader.readexactly(4)
                        msg_len = int.from_bytes(length_data, byteorder="big")
                        msg_data = await reader.readexactly(msg_len)
                        request = json.loads(msg_data.decode("utf-8"))

                        cmd = request.get("cmd", "")
                        if cmd == "hang":
                            # Never respond — simulate broker hang
                            await asyncio.sleep(100)
                        else:
                            response = json.dumps(
                                {"status": "success", "result": f"output:{cmd}"}
                            ).encode("utf-8")
                            writer.write(struct.pack(">I", len(response)) + response)
                            await writer.drain()
                except (asyncio.IncompleteReadError, ConnectionResetError):
                    pass
                finally:
                    writer.close()

            server = await asyncio.start_unix_server(
                handle_client, path=str(socket_path)
            )

            try:
                client = BrokerClient(socket_path=socket_path)
                await client.connect()

                # Step 1: Start a hanging command and cancel it (simulating timeout)
                hang_task = asyncio.create_task(
                    client.execute_command("router-1", "hang")
                )
                await asyncio.sleep(0.05)  # Wait for request to be sent
                hang_task.cancel()

                with pytest.raises(asyncio.CancelledError):
                    await hang_task

                # Connection should be torn down
                assert not client._connected
                assert client.writer is None

                # Step 2: Next command should reconnect cleanly and succeed immediately
                result = await client.execute_command("router-1", "show ok")
                assert result == "output:show ok"

                await client.disconnect()
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(_run())
