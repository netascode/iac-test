# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt
"""Unit tests for pre-flight controller authentication.

Tests verify the business logic of the pre-flight auth check,
ensuring authentication failures are identified and classified appropriately.
"""

from typing import get_args

from _pytest.monkeypatch import MonkeyPatch
from pytest_mock import MockerFixture

from nac_test.core.controller_auth import (
    CONTROLLER_REGISTRY,
    AuthOutcome,
    _get_auth_callable,
    preflight_auth_check,
)
from nac_test.core.types import AuthMethod, ControllerContext, ControllerTypeKey


class TestControllerRegistry:
    """Tests for CONTROLLER_REGISTRY configuration."""

    def test_registry_covers_all_supported_controllers(self) -> None:
        """Registry includes all supported controller types with valid configs."""
        # After consolidation: CONTROLLER_REGISTRY now includes ALL controllers
        expected_controllers = set(get_args(ControllerTypeKey))
        assert set(CONTROLLER_REGISTRY.keys()) == expected_controllers

        for controller_type, config in CONTROLLER_REGISTRY.items():
            assert config.display_name, f"{controller_type} missing display_name"
            assert config.url_env_var, f"{controller_type} missing url_env_var"
            assert config.env_var_prefix, f"{controller_type} missing env_var_prefix"


class TestGetAuthCallable:
    """Tests for _get_auth_callable helper function."""

    def test_returns_none_for_unknown_controller(self) -> None:
        """Returns None for unknown controller types."""
        result = _get_auth_callable("UNKNOWN_CONTROLLER")

        assert result is None

    def test_returns_none_for_empty_string(self) -> None:
        """Returns None for empty string controller type."""
        result = _get_auth_callable("")

        assert result is None

    def test_returns_none_for_iosxe(self) -> None:
        """Returns None for IOSXE (no controller auth needed)."""
        result = _get_auth_callable("IOSXE")

        assert result is None

    def test_returns_none_for_nxos(self) -> None:
        """Returns None for NXOS (direct device access, no controller auth)."""
        result = _get_auth_callable("NXOS")

        assert result is None


class TestPreflightAuthCheck:
    """Tests for preflight_auth_check main function."""

    def test_returns_skipped_when_no_auth_adapter(
        self, monkeypatch: MonkeyPatch, iosxe_context: ControllerContext
    ) -> None:
        """Returns skipped (not success) when no auth adapter is available."""
        monkeypatch.setenv("IOSXE_URL", "https://device.example.com")

        result = preflight_auth_check(iosxe_context)

        assert result.success is True
        assert result.reason == AuthOutcome.SKIPPED
        assert "skipped" in result.detail.lower()

    def test_returns_success_when_adapters_not_installed(
        self,
        monkeypatch: MonkeyPatch,
        aci_context: ControllerContext,
        mocker: MockerFixture,
    ) -> None:
        """Returns success when nac-test-pyats-common not installed."""
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")

        mocker.patch(
            "nac_test.core.controller_auth._get_auth_callable",
            return_value=None,
        )
        result = preflight_auth_check(aci_context)

        assert result.success is True
        assert "skipped" in result.detail.lower()

    def test_returns_success_when_auth_succeeds(
        self,
        monkeypatch: MonkeyPatch,
        aci_context: ControllerContext,
        mocker: MockerFixture,
    ) -> None:
        """Returns success when authentication succeeds."""
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")

        mock_auth = mocker.MagicMock(return_value="token123")
        mocker.patch(
            "nac_test.core.controller_auth._get_auth_callable",
            return_value=mock_auth,
        )
        result = preflight_auth_check(aci_context)

        assert result.success is True
        assert result.reason == AuthOutcome.SUCCESS
        assert result.controller_type == "ACI"
        assert result.controller_url == "https://apic.example.com"
        mock_auth.assert_called_once()

    def test_returns_failure_for_bad_credentials(
        self,
        monkeypatch: MonkeyPatch,
        aci_context: ControllerContext,
        mocker: MockerFixture,
    ) -> None:
        """Returns failure when credentials are rejected."""
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")

        mock_auth = mocker.MagicMock(side_effect=Exception("HTTP 401: Unauthorized"))
        mocker.patch(
            "nac_test.core.controller_auth._get_auth_callable",
            return_value=mock_auth,
        )
        result = preflight_auth_check(aci_context)

        assert result.success is False
        assert result.reason == AuthOutcome.BAD_CREDENTIALS
        assert "401" in result.detail

    def test_returns_failure_for_unreachable(
        self,
        monkeypatch: MonkeyPatch,
        sdwan_context: ControllerContext,
        mocker: MockerFixture,
    ) -> None:
        """Returns failure when controller is unreachable."""
        monkeypatch.setenv("SDWAN_URL", "https://sdwan.example.com")

        mock_auth = mocker.MagicMock(side_effect=Exception("Connection timed out"))
        mocker.patch(
            "nac_test.core.controller_auth._get_auth_callable",
            return_value=mock_auth,
        )
        result = preflight_auth_check(sdwan_context)

        assert result.success is False
        assert result.reason == AuthOutcome.UNREACHABLE
        assert result.controller_type == "SDWAN"

    def test_returns_success_when_missing_env_vars(
        self,
        monkeypatch: MonkeyPatch,
        cc_context: ControllerContext,
        mocker: MockerFixture,
    ) -> None:
        """Returns success when env vars are missing (let real auth fail later)."""
        monkeypatch.setenv("CC_URL", "https://catc.example.com")

        # ValueError is raised when env vars are missing
        mock_auth = mocker.MagicMock(
            side_effect=ValueError(
                "Missing required environment variables: CC_USERNAME"
            )
        )
        mocker.patch(
            "nac_test.core.controller_auth._get_auth_callable",
            return_value=mock_auth,
        )
        result = preflight_auth_check(cc_context)

        # Should succeed to let the actual auth call fail with proper error
        assert result.success is True
        assert "skipped" in result.detail.lower()

    def test_includes_controller_url_in_result(
        self,
        monkeypatch: MonkeyPatch,
        aci_context: ControllerContext,
        mocker: MockerFixture,
    ) -> None:
        """Auth result includes the controller URL for error messages."""
        monkeypatch.setenv("ACI_URL", "https://apic.lab.local")

        mock_auth = mocker.MagicMock(side_effect=Exception("HTTP 403: Forbidden"))
        mocker.patch(
            "nac_test.core.controller_auth._get_auth_callable",
            return_value=mock_auth,
        )
        result = preflight_auth_check(aci_context)

        assert result.controller_url == "https://apic.lab.local"

    def test_propagates_http_status_code(
        self,
        monkeypatch: MonkeyPatch,
        aci_context: ControllerContext,
        mocker: MockerFixture,
    ) -> None:
        """Auth result includes the HTTP status code from the error."""
        monkeypatch.setenv("ACI_URL", "https://apic.lab.local")

        mock_auth = mocker.MagicMock(side_effect=Exception("HTTP 403: Forbidden"))
        mocker.patch(
            "nac_test.core.controller_auth._get_auth_callable",
            return_value=mock_auth,
        )
        result = preflight_auth_check(aci_context)

        assert result.status_code == 403

    def test_status_code_none_for_non_http_errors(
        self,
        monkeypatch: MonkeyPatch,
        sdwan_context: ControllerContext,
        mocker: MockerFixture,
    ) -> None:
        """Auth result has None status_code for non-HTTP failures."""
        monkeypatch.setenv("SDWAN_URL", "https://sdwan.example.com")

        mock_auth = mocker.MagicMock(side_effect=Exception("Connection timed out"))
        mocker.patch(
            "nac_test.core.controller_auth._get_auth_callable",
            return_value=mock_auth,
        )
        result = preflight_auth_check(sdwan_context)

        assert result.status_code is None

    def test_handles_unknown_controller_type(self) -> None:
        """Unknown controller types are handled gracefully (skipped)."""
        result = preflight_auth_check(
            ControllerContext(
                controller_type="UNKNOWN_CONTROLLER",  # type: ignore[arg-type]
                auth_method=AuthMethod.SESSION,
            )
        )

        assert result.success is True
        assert "skipped" in result.detail.lower()
