# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Shared fixtures for unit tests."""

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from _pytest.monkeypatch import MonkeyPatch

from nac_test.core.controller_auth import AuthCheckResult, AuthOutcome
from nac_test.pyats_core.constants import (
    PYATS_GRACEFUL_DISCONNECT_WAIT_SECONDS,
    PYATS_POST_DISCONNECT_WAIT_SECONDS,
)

AUTH_SUCCESS = AuthCheckResult(
    success=True,
    reason=AuthOutcome.SUCCESS,
    controller_type="ACI",
    controller_url="https://apic.test.com",
    detail="OK",
)


def is_sublist(sublist: list[str], full_list: list[str]) -> bool:
    """Return True if sublist appears as a contiguous, ordered sequence within full_list.

    Useful for asserting that a group of CLI arguments was passed through verbatim
    and in the correct order, without assuming their absolute position in the list.

    Examples::

        is_sublist(["--loglevel", "DEBUG"], ["--outputdir", "/tmp", "--loglevel", "DEBUG", "/path"])
        # True

        is_sublist(["-L", "DEBUG"], ["--loglevel", "DEBUG"])
        # False
    """
    return any(
        full_list[i : i + len(sublist)] == sublist
        for i in range(len(full_list) - len(sublist) + 1)
    )


def assert_connection_has_optimizations(connection: dict[str, Any]) -> None:
    """Verify connection includes expected optimization settings.

    This helper consolidates assertions for connection optimization settings,
    making it easier to maintain tests when new optimizations are added.

    Args:
        connection: The connection dict from testbed["devices"][hostname]["connections"]["cli"]
    """
    assert connection["arguments"]["init_config_commands"] == []
    assert connection["arguments"]["operating_mode"] is True
    assert (
        connection["settings"]["GRACEFUL_DISCONNECT_WAIT_SEC"]
        == PYATS_GRACEFUL_DISCONNECT_WAIT_SECONDS
    )
    assert (
        connection["settings"]["POST_DISCONNECT_WAIT_SEC"]
        == PYATS_POST_DISCONNECT_WAIT_SECONDS
    )


@pytest.fixture()
def iosxe_controller_env(monkeypatch: MonkeyPatch) -> None:
    """Set up IOS-XE controller environment variables for testing.

    Provides consistent controller credentials for IOS-XE/D2D tests.
    Use this fixture instead of manually setting env vars in tests.
    """
    monkeypatch.setenv("IOSXE_URL", "https://test.example.com")
    monkeypatch.setenv("IOSXE_USERNAME", "test_user")
    monkeypatch.setenv("IOSXE_PASSWORD", "test_pass")


@pytest.fixture()
def aci_controller_env(monkeypatch: MonkeyPatch) -> None:
    """Set up ACI controller environment variables for testing."""
    monkeypatch.setenv("ACI_URL", "https://apic.test.com")
    monkeypatch.setenv("ACI_USERNAME", "admin")
    monkeypatch.setenv("ACI_PASSWORD", "test_pass")


@pytest.fixture()
def sdwan_controller_env(monkeypatch: MonkeyPatch) -> None:
    """Set up SD-WAN controller environment variables for testing."""
    monkeypatch.setenv("SDWAN_URL", "https://sdwan.test.com")
    monkeypatch.setenv("SDWAN_USERNAME", "admin")
    monkeypatch.setenv("SDWAN_PASSWORD", "test_pass")


@pytest.fixture()
def ssh_instance() -> Any:
    """Bare SSHTestBase with mocked logger and broker_client for _async_setup tests."""
    from nac_test.pyats_core.common.ssh_base_test import SSHTestBase

    instance = SSHTestBase.__new__(SSHTestBase)
    instance.logger = Mock()
    instance.broker_client = Mock()
    instance.broker_client.connect = AsyncMock()
    return instance


@pytest.fixture()
def cc_controller_env(monkeypatch: MonkeyPatch) -> None:
    """Set up Catalyst Center controller environment variables for testing."""
    monkeypatch.setenv("CC_URL", "https://cc.test.com")
    monkeypatch.setenv("CC_USERNAME", "admin")
    monkeypatch.setenv("CC_PASSWORD", "test_pass")
