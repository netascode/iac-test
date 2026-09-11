# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for SSHTestBase device validation and broker socket handling."""

import asyncio
import contextlib
import json
import socket as _socket
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from nac_test.pyats_core.common.ssh_base_test import SSHTestBase
from nac_test.pyats_core.constants import DEVICE_EXECUTE_TIMEOUT
from nac_test.pyats_core.ssh.command_cache import CommandCache


@pytest.fixture()
def temp_data_model_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Create a temporary data model file and set the environment variable."""
    data_model_path = tmp_path / "test_data.json"
    data_model_path.write_text(json.dumps({"test": "data"}))
    monkeypatch.setenv(
        "MERGED_DATA_MODEL_TEST_VARIABLES_FILEPATH", str(data_model_path)
    )
    return data_model_path


class TestSSHTestBaseValidation:
    """Test that SSHTestBase properly validates device info."""

    def _make_instance(self) -> SSHTestBase:
        instance = SSHTestBase()
        instance.logger = Mock()
        instance.failed = Mock()
        return instance

    def test_validation_called_for_valid_device(
        self,
        iosxe_controller_env: None,
        temp_data_model_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Validation passes for a fully-populated device info dict."""
        valid_device = {
            "hostname": "test-router",
            "host": "192.168.1.1",
            "os": "iosxe",
            "username": "admin",
            "password": "secret123",
        }
        monkeypatch.setenv("DEVICE_INFO", json.dumps(valid_device))
        instance = self._make_instance()

        mock_parent = Mock()
        mock_parent.broker_client = Mock()

        with (
            patch("nac_test.pyats_core.common.base_test.NACTestBase.setup"),
            patch.object(SSHTestBase, "parent", mock_parent, create=True),
            patch.object(instance, "_async_setup", new_callable=AsyncMock),
        ):
            instance.setup()

        instance.failed.assert_not_called()
        assert instance.device_info == valid_device

    def test_validation_fails_for_missing_fields(
        self,
        iosxe_controller_env: None,
        temp_data_model_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Validation fails with a clear message when required fields are absent."""
        invalid_device = {
            "hostname": "test-router",
            "host": "192.168.1.1",
            "os": "iosxe",
        }
        monkeypatch.setenv("DEVICE_INFO", json.dumps(invalid_device))
        instance = self._make_instance()

        with patch("nac_test.pyats_core.common.base_test.NACTestBase.setup"):
            instance.setup()

        instance.failed.assert_called_once()
        error_msg = instance.failed.call_args[0][0]
        assert "Framework Error: Device validation failed" in error_msg
        assert "Missing required fields: ['password', 'username']" in error_msg
        assert "Device validation failed: 'test-router'" in error_msg
        assert "This indicates a bug in the device resolver implementation" in error_msg

    def test_validation_not_called_for_json_parse_error(
        self,
        iosxe_controller_env: None,
        temp_data_model_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Validation is skipped when JSON parsing fails."""
        monkeypatch.setenv("DEVICE_INFO", "not valid json")
        instance = self._make_instance()

        with (
            patch(
                "nac_test.pyats_core.common.ssh_base_test.validate_device_inventory"
            ) as mock_validate,
            patch("nac_test.pyats_core.common.base_test.NACTestBase.setup"),
        ):
            instance.setup()

        mock_validate.assert_not_called()
        instance.failed.assert_called_once()
        assert "Could not parse device info JSON" in instance.failed.call_args[0][0]


class TestAsyncSetupBrokerSocketValidation:
    def test_uses_broker_when_socket_exists(
        self, monkeypatch: pytest.MonkeyPatch, socket_dir: Path, ssh_instance: Any
    ) -> None:
        """When NAC_TEST_BROKER_SOCKET points to a valid Unix socket, broker path is taken."""
        sock = socket_dir / "broker.sock"
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
            s.bind(str(sock))
        monkeypatch.setenv("NAC_TEST_BROKER_SOCKET", str(sock))

        mock_executor = Mock()
        mock_executor.connect = AsyncMock()
        mock_testbed_device = Mock()

        with (
            patch.object(
                SSHTestBase,
                "testbed_device",
                new_callable=lambda: property(lambda self: mock_testbed_device),
            ),
            patch(
                "nac_test.pyats_core.common.ssh_base_test.BrokerCommandExecutor",
                return_value=mock_executor,
            ),
            patch("nac_test.pyats_core.common.ssh_base_test.CommandCache"),
            patch.object(
                ssh_instance, "_create_execute_command_method", return_value=Mock()
            ),
            patch.object(ssh_instance, "_patch_device_execute_for_broker"),
        ):
            ssh_instance.device_info = {"hostname": "router-1"}
            asyncio.run(ssh_instance._async_setup("router-1"))

        ssh_instance.broker_client.connect.assert_called_once()
        mock_executor.connect.assert_called_once()
        ssh_instance.logger.warning.assert_not_called()

    @pytest.mark.parametrize(
        ("path_factory", "description"),
        [
            (lambda p: p / "no_such.sock", "non-existent path"),
            (
                lambda p: (p / "regular_file.sock").touch() or p / "regular_file.sock",
                "regular file",
            ),
            (lambda p: (p / "a_dir").mkdir() or p / "a_dir", "directory"),
        ],
        ids=["missing", "regular-file", "directory"],
    )
    def test_falls_back_for_non_socket_paths(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        ssh_instance: Any,
        path_factory: Any,
        description: str,
    ) -> None:
        """Fallback and warning are triggered whenever NAC_TEST_BROKER_SOCKET does not
        point to a valid Unix socket (missing path, regular file, or directory)."""
        bad_path = path_factory(tmp_path)
        monkeypatch.setenv("NAC_TEST_BROKER_SOCKET", str(bad_path))

        mock_testbed_device = Mock()
        mock_loop = Mock()
        mock_loop.run_in_executor = AsyncMock(return_value=None)

        with (
            patch.object(
                SSHTestBase,
                "testbed_device",
                new_callable=lambda: property(lambda self: mock_testbed_device),
            ),
            patch(
                "nac_test.pyats_core.common.ssh_base_test.get_or_create_event_loop",
                return_value=mock_loop,
            ),
            patch("nac_test.pyats_core.common.ssh_base_test.CommandCache"),
            patch.object(
                ssh_instance, "_create_execute_command_method", return_value=Mock()
            ),
        ):
            ssh_instance.device_info = {"hostname": "router-1"}
            asyncio.run(ssh_instance._async_setup("router-1"))

        ssh_instance.logger.warning.assert_called_once()
        assert "falling back" in ssh_instance.logger.warning.call_args[0][0]
        ssh_instance.broker_client.connect.assert_not_called()
        assert ssh_instance.connection is mock_testbed_device

    def test_raises_when_no_testbed_device(self, ssh_instance: Any) -> None:
        """When no testbed device is available, ConnectionError is raised immediately."""
        with patch.object(
            SSHTestBase,
            "testbed_device",
            new_callable=lambda: property(lambda self: None),
        ):
            with pytest.raises(ConnectionError, match="requires a PyATS testbed"):
                asyncio.run(ssh_instance._async_setup("router-1"))

    def test_raises_when_no_testbed_device_with_invalid_socket(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        ssh_instance: Any,
    ) -> None:
        """When socket is invalid and no testbed device, ConnectionError is raised
        (testbed invariant fires before socket validation)."""
        monkeypatch.setenv("NAC_TEST_BROKER_SOCKET", str(tmp_path / "no_such.sock"))

        with patch.object(
            SSHTestBase,
            "testbed_device",
            new_callable=lambda: property(lambda self: None),
        ):
            with pytest.raises(ConnectionError, match="requires a PyATS testbed"):
                asyncio.run(ssh_instance._async_setup("router-1"))


class TestPatchDeviceExecuteForBroker:
    """Test _patch_device_execute_for_broker method."""

    _RUN_CORO_PATH = (
        "nac_test.pyats_core.common.ssh_base_test.asyncio.run_coroutine_threadsafe"
    )

    # --- helper ---

    @staticmethod
    def _apply_broker_patch(
        ssh_instance: Any,
        *,
        extra_patches: list[Any] | None = None,
        call_after_patch: Any = None,
    ) -> tuple[Mock, Any]:
        """Set up ssh_instance for broker patching and call the method.

        Returns (mock_device, call_result).
        """
        mock_device = Mock()
        ssh_instance.hostname = "router-1"
        ssh_instance.command_cache = CommandCache("router-1")

        patches = [
            patch.object(
                SSHTestBase,
                "testbed_device",
                new_callable=lambda: property(lambda self: mock_device),
            ),
            patch("nac_test.pyats_core.common.ssh_base_test.get_or_create_event_loop"),
            *(extra_patches or []),
        ]

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            ssh_instance._patch_device_execute_for_broker()
            call_result = call_after_patch(mock_device) if call_after_patch else None

        return mock_device, call_result

    # --- tests ---

    def test_patches_testbed_device_execute(self, ssh_instance: Any) -> None:
        """After calling the method, testbed_device.execute is replaced with the broker_execute closure."""
        mock_device, _ = self._apply_broker_patch(ssh_instance)
        # execute should be our closure, not the original Mock auto-attribute
        assert callable(mock_device.execute)
        assert not isinstance(mock_device.execute, Mock)

    def test_patched_execute_calls_broker(self, ssh_instance: Any) -> None:
        """The patched execute calls broker_client.execute_command via run_coroutine_threadsafe."""
        ssh_instance.broker_client.execute_command = AsyncMock(
            return_value="command output"
        )
        mock_future = Mock()
        mock_future.result = Mock(return_value="command output")

        mock_device, result = self._apply_broker_patch(
            ssh_instance,
            extra_patches=[patch(self._RUN_CORO_PATH, return_value=mock_future)],
            call_after_patch=lambda dev: dev.execute("show version"),
        )

        mock_future.result.assert_called_with(timeout=DEVICE_EXECUTE_TIMEOUT)

    def test_patched_execute_propagates_timeout_error(self, ssh_instance: Any) -> None:
        """TimeoutError from broker cancels the future, propagates, and is NOT cached (#925)."""
        mock_future = Mock()
        mock_future.result = Mock(side_effect=TimeoutError("broker hung"))

        with pytest.raises(TimeoutError, match="broker hung"):
            self._apply_broker_patch(
                ssh_instance,
                extra_patches=[patch(self._RUN_CORO_PATH, return_value=mock_future)],
                call_after_patch=lambda dev: dev.execute("show version"),
            )

        # The future must be cancelled to avoid zombie coroutines (#925)
        mock_future.cancel.assert_called_once()
        # The failed command must NOT be cached
        assert ssh_instance.command_cache.get("show version") is None

    def test_patched_execute_uses_cache_on_hit(self, ssh_instance: Any) -> None:
        """When command is cached, returns cached output without contacting broker."""
        mock_run_coro = Mock()

        def setup_cache_and_call(dev: Mock) -> Any:
            ssh_instance.command_cache.set("show version", "cached output")
            return dev.execute("show version")

        _, result = self._apply_broker_patch(
            ssh_instance,
            extra_patches=[patch(self._RUN_CORO_PATH, mock_run_coro)],
            call_after_patch=setup_cache_and_call,
        )

        assert result == "cached output"
        mock_run_coro.assert_not_called()

    def test_patched_execute_caches_broker_result(self, ssh_instance: Any) -> None:
        """After executing via broker, result is cached for future calls."""
        ssh_instance.broker_client.execute_command = AsyncMock(
            return_value="new output"
        )
        mock_future = Mock()
        mock_future.result = Mock(return_value="new output")

        self._apply_broker_patch(
            ssh_instance,
            extra_patches=[patch(self._RUN_CORO_PATH, return_value=mock_future)],
            call_after_patch=lambda dev: dev.execute("show ip route"),
        )

        assert ssh_instance.command_cache.get("show ip route") == "new output"

    def test_sets_device_connected_true(self, ssh_instance: Any) -> None:
        """After patching, testbed_device.connected is set to True for Genie."""
        mock_device, _ = self._apply_broker_patch(ssh_instance)
        assert mock_device.connected is True

    def test_sets_connectionmgr_is_connected(self, ssh_instance: Any) -> None:
        """After patching, connectionmgr.is_connected returns True for any args."""
        mock_device, _ = self._apply_broker_patch(ssh_instance)
        assert mock_device.connectionmgr.is_connected() is True
        assert mock_device.connectionmgr.is_connected("alias", extra=True) is True

    def test_sets_cli_shim_with_broker_execute(self, ssh_instance: Any) -> None:
        """After patching, device.cli.execute delegates to broker_execute."""
        ssh_instance.broker_client.execute_command = AsyncMock(
            return_value="cli output"
        )
        mock_future = Mock()
        mock_future.result = Mock(return_value="cli output")

        mock_device, result = self._apply_broker_patch(
            ssh_instance,
            extra_patches=[patch(self._RUN_CORO_PATH, return_value=mock_future)],
            call_after_patch=lambda dev: dev.cli.execute("show version"),
        )

        assert result == "cli output"
        mock_future.result.assert_called_with(timeout=DEVICE_EXECUTE_TIMEOUT)


class TestExecuteCommandUnified:
    """Test _create_execute_command_method unified execution path."""

    def test_execute_command_routes_through_testbed_device(
        self, ssh_instance: Any
    ) -> None:
        """Execute command routes through testbed_device.execute via run_in_executor."""
        mock_device = Mock()
        mock_device.execute = Mock(return_value="device output")
        ssh_instance.hostname = "router-1"
        ssh_instance.command_cache = CommandCache("router-1")

        mock_loop = Mock()
        mock_loop.run_in_executor = AsyncMock(return_value="device output")

        with (
            patch.object(
                SSHTestBase,
                "testbed_device",
                new_callable=lambda: property(lambda self: mock_device),
            ),
            patch(
                "nac_test.pyats_core.common.ssh_base_test.get_or_create_event_loop",
                return_value=mock_loop,
            ),
        ):
            execute_command = ssh_instance._create_execute_command_method(
                ssh_instance.command_cache
            )
            result = asyncio.run(execute_command("show version"))

        assert result == "device output"
        mock_loop.run_in_executor.assert_called_once()
        call_args = mock_loop.run_in_executor.call_args
        assert call_args[0][0] is None  # executor=None
        assert call_args[0][1] == mock_device.execute
        assert call_args[0][2] == "show version"
        assert ssh_instance.command_cache.get("show version") == "device output"

    def test_execute_command_returns_cached_without_executing(
        self, ssh_instance: Any
    ) -> None:
        """When command is cached, returns cached result without executing."""
        mock_device = Mock()
        ssh_instance.hostname = "router-1"
        ssh_instance.command_cache = CommandCache("router-1")
        ssh_instance.command_cache.set("show version", "cached")

        mock_loop = Mock()
        mock_loop.run_in_executor = AsyncMock()

        with (
            patch.object(
                SSHTestBase,
                "testbed_device",
                new_callable=lambda: property(lambda self: mock_device),
            ),
            patch(
                "nac_test.pyats_core.common.ssh_base_test.get_or_create_event_loop",
                return_value=mock_loop,
            ),
        ):
            execute_command = ssh_instance._create_execute_command_method(
                ssh_instance.command_cache
            )
            result = asyncio.run(execute_command("show version"))

        assert result == "cached"
        mock_loop.run_in_executor.assert_not_called()


class TestParseOutput:
    """Test parse_output method."""

    def test_returns_none_without_testbed_device(self, ssh_instance: Any) -> None:
        """When testbed_device is None/falsy, returns None immediately."""
        with patch.object(
            SSHTestBase,
            "testbed_device",
            new_callable=lambda: property(lambda self: None),
        ):
            result = asyncio.run(ssh_instance.parse_output("show version"))

        assert result is None

    def test_parses_with_output(self, ssh_instance: Any) -> None:
        """Calls testbed_device.parse(cmd, output=output) via executor, returns dict result."""
        mock_device = Mock()
        mock_loop = Mock()
        parsed_result = {"key": "value"}
        mock_loop.run_in_executor = AsyncMock(return_value=parsed_result)

        with (
            patch.object(
                SSHTestBase,
                "testbed_device",
                new_callable=lambda: property(lambda self: mock_device),
            ),
            patch(
                "nac_test.pyats_core.common.ssh_base_test.get_or_create_event_loop",
                return_value=mock_loop,
            ),
        ):
            result = asyncio.run(
                ssh_instance.parse_output("show version", output="raw output")
            )

        assert result == parsed_result
        mock_loop.run_in_executor.assert_called_once()
        # Verify the partial was called with output parameter
        call_args = mock_loop.run_in_executor.call_args
        assert call_args[0][0] is None  # executor=None

    def test_parses_without_output(self, ssh_instance: Any) -> None:
        """Calls testbed_device.parse(cmd) via executor, returns dict result."""
        mock_device = Mock()
        mock_loop = Mock()
        parsed_result = {"key": "value"}
        mock_loop.run_in_executor = AsyncMock(return_value=parsed_result)

        with (
            patch.object(
                SSHTestBase,
                "testbed_device",
                new_callable=lambda: property(lambda self: mock_device),
            ),
            patch(
                "nac_test.pyats_core.common.ssh_base_test.get_or_create_event_loop",
                return_value=mock_loop,
            ),
        ):
            result = asyncio.run(ssh_instance.parse_output("show version"))

        assert result == parsed_result
        mock_loop.run_in_executor.assert_called_once()

    def test_returns_none_on_exception(self, ssh_instance: Any) -> None:
        """When parse raises, logs warning and returns None."""
        mock_device = Mock()
        mock_loop = Mock()
        mock_loop.run_in_executor = AsyncMock(side_effect=Exception("Parser error"))

        with (
            patch.object(
                SSHTestBase,
                "testbed_device",
                new_callable=lambda: property(lambda self: mock_device),
            ),
            patch(
                "nac_test.pyats_core.common.ssh_base_test.get_or_create_event_loop",
                return_value=mock_loop,
            ),
        ):
            result = asyncio.run(ssh_instance.parse_output("show version"))

        assert result is None
        ssh_instance.logger.warning.assert_called_once()
        assert "Genie parser failed" in ssh_instance.logger.warning.call_args[0][0]

    def test_returns_none_when_parse_returns_none(self, ssh_instance: Any) -> None:
        """When parse() returns None, returns None."""
        mock_device = Mock()
        mock_loop = Mock()
        mock_loop.run_in_executor = AsyncMock(return_value=None)

        with (
            patch.object(
                SSHTestBase,
                "testbed_device",
                new_callable=lambda: property(lambda self: mock_device),
            ),
            patch(
                "nac_test.pyats_core.common.ssh_base_test.get_or_create_event_loop",
                return_value=mock_loop,
            ),
        ):
            result = asyncio.run(ssh_instance.parse_output("show version"))

        assert result is None
