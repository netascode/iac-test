# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Failure-mode integration tests for ConnectionBroker and BrokerClient.

These tests spin up a real ConnectionBroker with a real Unix socket but with
pyATS device connections mocked at the device.connect() boundary. This lets us
exercise the actual socket protocol, client/broker interaction, and error
handling paths that unit tests cannot reach.

Async methods are tested using asyncio.run() — same pattern as the rest of the
unit test suite (see test_subprocess_runner.py).

Note: This file is intended to live under tests/integration/ (see
tests/integration/test_broker.py for broader end-to-end broker validation).
"""

import asyncio
import json
import os
import socket as _socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from unicon.core.errors import SubCommandFailure

from nac_test.pyats_core.broker.broker_client import BrokerClient
from nac_test.pyats_core.broker.connection_broker import ConnectionBroker
from nac_test.pyats_core.constants import MAX_BROKER_MESSAGE_BYTES

pytestmark = pytest.mark.integration


@pytest.fixture
def good_device() -> MagicMock:
    """A device whose connect() succeeds."""
    device = MagicMock()
    device.connected = True
    device.spawn = True
    device.connect.return_value = None
    device.execute.return_value = "output"
    return device


@pytest.fixture
def bad_device() -> MagicMock:
    """A device whose connect() always fails."""
    device = MagicMock()
    device.connect.side_effect = ConnectionError(
        'failed to connect to bad-router\nFailed while bringing device to "any" state'
    )
    return device


@pytest.fixture
def make_broker(
    socket_dir: Path,
    tmp_path: Path,
) -> Callable[[dict[str, MagicMock]], ConnectionBroker]:
    """Factory fixture: call make_broker(devices) to get a wired ConnectionBroker."""

    def _factory(devices: dict[str, MagicMock]) -> ConnectionBroker:
        broker = ConnectionBroker(
            socket_path=socket_dir / "b.sock",
            output_dir=tmp_path,
        )
        broker.testbed = MagicMock()
        broker.testbed.devices = devices
        for hostname in devices:
            broker._device_locks[hostname] = asyncio.Lock()
        return broker

    return _factory


def _patch_executor(loop: asyncio.AbstractEventLoop) -> Any:
    """Patch run_in_executor to call device methods synchronously in tests."""

    async def fake_executor(executor, func, *args):  # type: ignore[no-untyped-def]
        return func(*args)

    return patch.object(loop, "run_in_executor", side_effect=fake_executor)


async def _run_broker(broker: ConnectionBroker, coro: Any) -> Any:
    await broker._start_socket_server()
    try:
        result = await coro
        return result
    finally:
        assert broker.server is not None
        broker.server.close()
        await broker.server.wait_closed()


async def _expect_broker_error(
    client: BrokerClient, broker_request: dict[str, Any], timeout: float = 1.0
) -> str:
    try:
        await asyncio.wait_for(client._send_request(broker_request), timeout=timeout)
    except ConnectionError as e:
        return str(e)

    raise AssertionError("Expected ConnectionError")


def _frame(obj: dict[str, Any]) -> bytes:
    data = json.dumps(obj).encode("utf-8")
    return len(data).to_bytes(4, byteorder="big") + data


async def _read_framed_json(
    reader: asyncio.StreamReader, timeout: float = 1.0
) -> dict[str, Any]:
    resp_len = int.from_bytes(
        await asyncio.wait_for(reader.readexactly(4), timeout=timeout),
        byteorder="big",
    )
    resp: dict[str, Any] = json.loads(
        (await asyncio.wait_for(reader.readexactly(resp_len), timeout=timeout)).decode(
            "utf-8"
        )
    )
    return resp


class TestBrokerUnavailable:
    def test_ensure_connection_returns_false_when_broker_not_running(
        self,
        tmp_path: Path,
    ) -> None:
        """ensure_connection returns False (does not raise) when broker is unreachable."""
        client = BrokerClient(socket_path=tmp_path / "no_such.sock")

        result = asyncio.run(client.ensure_connection("router-1"))

        assert result is False

    def test_ensure_connection_returns_false_for_stale_socket_file(
        self,
        socket_dir: Any,
    ) -> None:
        """If a Unix socket file exists but no server is listening (stale socket),
        ensure_connection should fail gracefully and not hang."""
        stale_path = Path(socket_dir) / "stale.sock"
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
            s.bind(str(stale_path))
            s.listen(1)

        client = BrokerClient(socket_path=stale_path)
        result = asyncio.run(
            asyncio.wait_for(client.ensure_connection("router-1"), timeout=1.0)
        )
        assert result is False


class TestDeviceConnection:
    def test_connect_command_returns_error_when_device_unreachable(
        self,
        make_broker: Any,
        bad_device: MagicMock,
    ) -> None:
        """When device.connect() fails the broker returns an error response with the
        actual exception message — not the generic 'Unknown broker error' fallback."""
        broker: ConnectionBroker = make_broker({"bad-router": bad_device})

        async def _run() -> dict[str, Any]:
            loop = asyncio.get_event_loop()

            async def _body() -> dict[str, Any]:
                with patch(
                    "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                    return_value=loop,
                ):
                    with _patch_executor(loop):
                        async with BrokerClient(
                            socket_path=broker.socket_path
                        ) as client:
                            try:
                                return await client._send_request(
                                    {"command": "connect", "hostname": "bad-router"}
                                )
                            except ConnectionError as e:
                                return {"status": "error", "error": str(e)}

            result: dict[str, Any] = await _run_broker(broker, _body())
            return result

        result = asyncio.run(_run())
        assert result["status"] == "error"
        assert result.get("error", "") != "Unknown broker error"
        assert "bad-router" in result.get("error", "")


class TestBrokerClientSocketPathValidation:
    def test_broker_client_fails_for_socket_path_that_is_a_directory(
        self,
        tmp_path: Path,
    ) -> None:
        client = BrokerClient(socket_path=tmp_path)
        result = asyncio.run(
            asyncio.wait_for(client.ensure_connection("router-1"), timeout=1.0)
        )
        assert result is False

    def test_broker_client_fails_for_socket_path_that_is_a_file(
        self,
        tmp_path: Path,
    ) -> None:
        sock = tmp_path / "not_a_socket"
        sock.write_text("not a socket")
        client = BrokerClient(socket_path=sock)
        result = asyncio.run(
            asyncio.wait_for(client.ensure_connection("router-1"), timeout=1.0)
        )
        assert result is False


class TestSocketDeletedMidRun:
    def test_existing_client_still_works_after_socket_unlinked(
        self,
        make_broker: Any,
    ) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> str:
            loop = asyncio.get_event_loop()

            async def _body() -> str:
                with patch(
                    "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                    return_value=loop,
                ):
                    async with BrokerClient(socket_path=broker.socket_path) as client:
                        broker.socket_path.unlink()
                        result = await asyncio.wait_for(
                            client._send_request({"command": "ping"}), timeout=1.0
                        )
                        assert result == {"status": "success", "result": "pong"}
                        return str(result["result"])

            result: str = await _run_broker(broker, _body())
            return result

        assert asyncio.run(_run()) == "pong"

    def test_new_client_fails_after_socket_unlinked(
        self,
        make_broker: Any,
    ) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> bool:
            loop = asyncio.get_event_loop()

            async def _body() -> bool:
                with patch(
                    "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                    return_value=loop,
                ):
                    async with BrokerClient(socket_path=broker.socket_path) as client:
                        await asyncio.wait_for(
                            client._send_request({"command": "ping"}), timeout=1.0
                        )
                        broker.socket_path.unlink()

                        new_client = BrokerClient(socket_path=broker.socket_path)
                        return await asyncio.wait_for(
                            new_client.ensure_connection("router-1"),
                            timeout=1.0,
                        )

            result: bool = await _run_broker(broker, _body())
            return result

        assert asyncio.run(_run()) is False


class TestConcurrency:
    def test_concurrent_connect_requests_only_connect_once(
        self,
        make_broker: Any,
        good_device: MagicMock,
    ) -> None:
        broker: ConnectionBroker = make_broker({"router-1": good_device})

        async def _run() -> None:
            loop = asyncio.get_event_loop()

            async def _body() -> None:
                with patch(
                    "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                    return_value=loop,
                ):
                    # Avoid threads; run the connect lambda synchronously
                    with _patch_executor(loop):
                        async with (
                            BrokerClient(socket_path=broker.socket_path) as c1,
                            BrokerClient(socket_path=broker.socket_path) as c2,
                        ):
                            results = await asyncio.gather(
                                c1.ensure_connection("router-1"),
                                c2.ensure_connection("router-1"),
                            )
                            assert results == [True, True]

            await _run_broker(broker, _body())

        asyncio.run(_run())

        # Both clients raced to connect; the broker should only call device.connect once.
        assert good_device.connect.call_count == 1

    def test_multiple_processes_can_connect_concurrently(
        self,
        make_broker: Any,
        good_device: MagicMock,
    ) -> None:
        broker: ConnectionBroker = make_broker({"router-1": good_device})

        async def _run() -> None:
            env = {
                "NAC_TEST_BROKER_SOCKET": str(broker.socket_path),
                **dict(os.environ),
            }

            code = (
                "import asyncio\n"
                "from nac_test.pyats_core.broker.broker_client import BrokerClient\n"
                "async def main():\n"
                "    c = BrokerClient()\n"
                "    ok = await asyncio.wait_for(c.ensure_connection('router-1'), timeout=2.0)\n"
                "    raise SystemExit(0 if ok else 1)\n"
                "asyncio.run(main())\n"
            )

            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", code],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(5)
            ]

            async def _body() -> None:
                try:
                    loop = asyncio.get_event_loop()

                    async def _communicate(p: subprocess.Popen[str]) -> tuple[str, str]:
                        return await loop.run_in_executor(
                            None, lambda: p.communicate(timeout=5)
                        )

                    results = await asyncio.gather(*[_communicate(p) for p in procs])

                    for p, (stdout, stderr) in zip(procs, results, strict=True):
                        assert p.returncode == 0, f"stdout={stdout}\nstderr={stderr}"

                finally:
                    for p in procs:
                        if p.poll() is None:
                            p.kill()
                            p.communicate(timeout=1)

            await _run_broker(broker, _body())

        asyncio.run(_run())

        assert good_device.connect.call_count == 1


class TestMalformedRequests:
    def test_unknown_command_returns_error(self, make_broker: Any) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> str:
            async def _body() -> str:
                async with BrokerClient(socket_path=broker.socket_path) as client:
                    try:
                        await asyncio.wait_for(
                            client._send_request({"command": "nope"}),
                            timeout=1.0,
                        )
                    except ConnectionError as e:
                        return str(e)

                    raise AssertionError("Expected ConnectionError")

            result: str = await _run_broker(broker, _body())
            return result

        err = asyncio.run(_run())
        assert "Unknown command" in err

    def test_missing_command_returns_error(self, make_broker: Any) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> str:
            async def _body() -> str:
                async with BrokerClient(socket_path=broker.socket_path) as client:
                    try:
                        await asyncio.wait_for(
                            client._send_request({}),
                            timeout=1.0,
                        )
                    except ConnectionError as e:
                        return str(e)

                    raise AssertionError("Expected ConnectionError")

            result: str = await _run_broker(broker, _body())
            return result

        err = asyncio.run(_run())
        assert "Unknown command" in err

    @pytest.mark.parametrize(
        ("payload", "use_payload_len"),
        [
            ((10).to_bytes(4, byteorder="big") + b"123", False),
            (b"not json", True),
            ((0).to_bytes(4, byteorder="big"), False),
            (b"\xff\xfe\xff", True),
            ((1_000_000).to_bytes(4, byteorder="big"), False),
        ],
        ids=[
            "truncated-frame",
            "non-json",
            "zero-length",
            "bad-utf8",
            "absurd-length",
        ],
    )
    def test_invalid_protocol_closes_connection(
        self,
        make_broker: Any,
        payload: bytes,
        use_payload_len: bool,
    ) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> None:
            async def _body() -> None:
                reader, writer = await asyncio.open_unix_connection(
                    str(broker.socket_path)
                )

                if use_payload_len:
                    writer.write(len(payload).to_bytes(4, byteorder="big") + payload)
                else:
                    writer.write(payload)

                await writer.drain()
                writer.write_eof()

                data = await asyncio.wait_for(reader.read(), timeout=1.0)
                assert data == b""

                writer.close()
                await writer.wait_closed()

            await _run_broker(broker, _body())

        asyncio.run(_run())

    def test_oversized_frame_closes_connection(self, make_broker: Any) -> None:
        """Frames exceeding MAX_BROKER_MESSAGE_BYTES are rejected before read."""
        broker: ConnectionBroker = make_broker({})

        async def _run() -> None:
            async def _body() -> None:
                reader, writer = await asyncio.open_unix_connection(
                    str(broker.socket_path)
                )

                # Send a length prefix just over the limit (don't send the body)
                oversized_len = MAX_BROKER_MESSAGE_BYTES + 1
                writer.write(oversized_len.to_bytes(4, byteorder="big"))
                await writer.drain()
                writer.write_eof()

                data = await asyncio.wait_for(reader.read(), timeout=1.0)
                assert data == b""

                writer.close()
                await writer.wait_closed()

            await _run_broker(broker, _body())

        asyncio.run(_run())

    def test_valid_json_non_object_returns_error_response(
        self, make_broker: Any
    ) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> dict[str, Any]:
            async def _body() -> dict[str, Any]:
                reader, writer = await asyncio.open_unix_connection(
                    str(broker.socket_path)
                )

                payload = b"[]"
                writer.write(len(payload).to_bytes(4, byteorder="big") + payload)
                await writer.drain()

                response = await _read_framed_json(reader)

                writer.close()
                await writer.wait_closed()
                return response

            result: dict[str, Any] = await _run_broker(broker, _body())
            return result

        result = asyncio.run(_run())
        assert result["status"] == "error"
        assert "get" in result.get("error", "")

    @pytest.mark.parametrize(
        ("broker_request", "expected_substrings"),
        [
            (
                {"command": "execute", "hostname": "r1"},
                ["Missing", "cmd"],
            ),
            (
                {"command": "connect"},
                ["Missing", "hostname"],
            ),
            (
                {"command": "disconnect"},
                ["Missing", "hostname"],
            ),
        ],
    )
    def test_missing_required_parameters_return_error(
        self,
        make_broker: Any,
        broker_request: dict[str, Any],
        expected_substrings: list[str],
    ) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> str:
            async def _body() -> str:
                async with BrokerClient(socket_path=broker.socket_path) as client:
                    return await _expect_broker_error(client, broker_request)

            result: str = await _run_broker(broker, _body())
            return result

        err = asyncio.run(_run())
        for s in expected_substrings:
            assert s in err

    def test_disconnect_succeeds_when_device_not_connected(
        self, make_broker: Any
    ) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> dict[str, Any]:
            async def _body() -> dict[str, Any]:
                async with BrokerClient(socket_path=broker.socket_path) as client:
                    return await asyncio.wait_for(
                        client._send_request(
                            {"command": "disconnect", "hostname": "router-1"}
                        ),
                        timeout=1.0,
                    )

            result: dict[str, Any] = await _run_broker(broker, _body())
            return result

        result = asyncio.run(_run())
        assert result == {"status": "success", "result": True}

    def test_error_responses_are_framed_json(self, make_broker: Any) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> dict[str, Any]:
            async def _body() -> dict[str, Any]:
                reader, writer = await asyncio.open_unix_connection(
                    str(broker.socket_path)
                )

                payload = json.dumps({"command": "connect"}).encode("utf-8")
                writer.write(len(payload).to_bytes(4, byteorder="big") + payload)
                await writer.drain()

                response = await _read_framed_json(reader)

                writer.close()
                await writer.wait_closed()
                return response

            result: dict[str, Any] = await _run_broker(broker, _body())
            return result

        result = asyncio.run(_run())
        assert result["status"] == "error"
        assert "hostname" in result.get("error", "")

    def test_sequential_requests_over_single_connection(self, make_broker: Any) -> None:
        broker: ConnectionBroker = make_broker({})

        async def _run() -> None:
            async def _body() -> None:
                reader, writer = await asyncio.open_unix_connection(
                    str(broker.socket_path)
                )

                # 1) ping
                writer.write(_frame({"command": "ping"}))
                await writer.drain()
                assert await _read_framed_json(reader) == {
                    "status": "success",
                    "result": "pong",
                }

                # 2) handled error response (missing hostname)
                writer.write(_frame({"command": "connect"}))
                await writer.drain()
                resp2 = await _read_framed_json(reader)
                assert resp2["status"] == "error"

                # 3) ping still works after a handled error
                writer.write(_frame({"command": "ping"}))
                await writer.drain()
                assert await _read_framed_json(reader) == {
                    "status": "success",
                    "result": "pong",
                }

                # 4) invalid protocol closes connection (send bad JSON)
                bad = b"not json"
                writer.write(len(bad).to_bytes(4, byteorder="big") + bad)
                await writer.drain()
                writer.write_eof()

                data = await asyncio.wait_for(reader.read(), timeout=1.0)
                assert data == b""

                writer.close()
                await writer.wait_closed()

            await _run_broker(broker, _body())

        asyncio.run(_run())


class TestCleanup:
    def test_shutdown_disconnects_all_connected_devices(
        self,
        make_broker: Any,
        good_device: MagicMock,
    ) -> None:
        """shutdown() disconnects all devices and removes the socket file."""
        broker: ConnectionBroker = make_broker({"router-1": good_device})
        broker.connected_devices["router-1"] = good_device

        async def _run() -> None:
            await broker._start_socket_server()
            loop = asyncio.get_event_loop()
            with patch(
                "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                return_value=loop,
            ):
                with _patch_executor(loop):
                    await broker.shutdown()

        asyncio.run(_run())

        assert broker.connected_devices == {}
        assert not broker.socket_path.exists()


class TestCommandRejectionVsTransportFailure:
    """Contrast SubCommandFailure (device rejects command, session healthy) with
    transport-level failures (SSH dies, cache must be wiped).

    Both tests populate the command cache with a successful first command, then
    trigger a failure on a second command and verify the divergent outcomes.
    """

    def test_sub_command_failure_preserves_connection_and_cache(
        self,
        make_broker: Any,
        good_device: MagicMock,
    ) -> None:
        """SubCommandFailure re-raises to the client but does NOT disconnect
        the device or wipe the command cache."""
        broker: ConnectionBroker = make_broker({"router-1": good_device})

        async def _run() -> None:
            loop = asyncio.get_event_loop()

            async def _body() -> None:
                with patch(
                    "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                    return_value=loop,
                ):
                    with _patch_executor(loop):
                        async with BrokerClient(
                            socket_path=broker.socket_path
                        ) as client:
                            # 1) Successful command — populates cache
                            good_device.execute.return_value = "good output"
                            r1 = await asyncio.wait_for(
                                client._send_request(
                                    {
                                        "command": "execute",
                                        "hostname": "router-1",
                                        "cmd": "show version",
                                    }
                                ),
                                timeout=2.0,
                            )
                            assert r1["status"] == "success"
                            assert r1["result"] == "good output"

                            # 2) SubCommandFailure on second command
                            good_device.execute.side_effect = SubCommandFailure(
                                "Invalid command at '^' marker"
                            )
                            err = await _expect_broker_error(
                                client,
                                {
                                    "command": "execute",
                                    "hostname": "router-1",
                                    "cmd": "show bgp all",
                                },
                                timeout=2.0,
                            )
                            assert "Invalid command" in err

                            # Connection still present
                            assert "router-1" in broker.connected_devices

                            # First command's cache entry still intact
                            cache = broker.command_cache.get("router-1")
                            assert cache is not None
                            assert cache.get("show version") == "good output"

                            # Device was never disconnected
                            good_device.disconnect.assert_not_called()

            await _run_broker(broker, _body())

        asyncio.run(_run())

    def test_transport_failure_disconnects_and_wipes_cache(
        self,
        make_broker: Any,
        good_device: MagicMock,
    ) -> None:
        """A transport-level exception (e.g. OSError) disconnects the device
        and wipes the command cache — regression guard for existing behavior."""
        broker: ConnectionBroker = make_broker({"router-1": good_device})

        async def _run() -> None:
            loop = asyncio.get_event_loop()

            async def _body() -> None:
                with patch(
                    "nac_test.pyats_core.broker.connection_broker.get_or_create_event_loop",
                    return_value=loop,
                ):
                    with _patch_executor(loop):
                        async with BrokerClient(
                            socket_path=broker.socket_path
                        ) as client:
                            # 1) Successful command — populates cache
                            good_device.execute.return_value = "good output"
                            r1 = await asyncio.wait_for(
                                client._send_request(
                                    {
                                        "command": "execute",
                                        "hostname": "router-1",
                                        "cmd": "show version",
                                    }
                                ),
                                timeout=2.0,
                            )
                            assert r1["status"] == "success"
                            assert r1["result"] == "good output"

                            # 2) Transport failure on second command
                            good_device.execute.side_effect = OSError(
                                "Socket is closed"
                            )
                            err = await _expect_broker_error(
                                client,
                                {
                                    "command": "execute",
                                    "hostname": "router-1",
                                    "cmd": "show interfaces",
                                },
                                timeout=2.0,
                            )
                            assert "Socket is closed" in err

                            # OSError is a transport failure, so this exercises the full #899 path:
                            # fail -> disconnect -> reconnect -> fail -> disconnect -> raise
                            assert good_device.execute.call_count == 3
                            assert good_device.disconnect.call_count == 2

                            # Connection removed
                            assert "router-1" not in broker.connected_devices

                            # Command cache wiped for this device
                            assert "router-1" not in broker.command_cache

            await _run_broker(broker, _body())

        asyncio.run(_run())
