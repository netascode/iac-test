# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Tests for controller type detection utilities."""

import json
import logging

import pytest

from nac_test.core.constants import ENV_CONTROLLER_CONTEXT
from nac_test.core.controller import (
    CONTROLLER_REGISTRY,
    CredentialSet,
    IncompleteCredentials,
    MultipleControllersFound,
    NoCredentialsFound,
    _find_credential_sets,
    _format_multiple_credentials_error,
    _format_no_credentials_error,
    detect_controller_type,
    format_resolution_error,
    get_connection_params,
    get_controller_context,
    get_controller_url,
    get_matched_credential_set,
    resolve_controller,
    should_verify_ssl,
)
from nac_test.core.types import AuthMethod, ControllerContext

# =============================================================================
# Test Data Constants
# =============================================================================

# Complete credential sets for all controllers with expected auth methods
CONTROLLER_CREDENTIALS: list[tuple[str, dict[str, str], str]] = [
    # (controller_type, env_vars, expected_auth_method)
    (
        "ACI",
        {
            "ACI_URL": "https://apic.local",
            "ACI_USERNAME": "admin",
            "ACI_PASSWORD": "pass",
        },
        "session",
    ),
    (
        "SDWAN",
        {"SDWAN_URL": "https://sdwan.local", "SDWAN_API_TOKEN": "tok123"},
        "token",
    ),
    (
        "SDWAN",
        {
            "SDWAN_URL": "https://sdwan.local",
            "SDWAN_USERNAME": "admin",
            "SDWAN_PASSWORD": "pass",
        },
        "session",
    ),
    (
        "CC",
        {"CC_URL": "https://cc.local", "CC_USERNAME": "admin", "CC_PASSWORD": "pass"},
        "session",
    ),
    (
        "MERAKI",
        {
            "MERAKI_URL": "https://meraki.local",
            "MERAKI_USERNAME": "admin",
            "MERAKI_PASSWORD": "pass",
        },
        "session",
    ),
    (
        "FMC",
        {
            "FMC_URL": "https://fmc.local",
            "FMC_USERNAME": "admin",
            "FMC_PASSWORD": "pass",
        },
        "session",
    ),
    (
        "ISE",
        {
            "ISE_URL": "https://ise.local",
            "ISE_USERNAME": "admin",
            "ISE_PASSWORD": "pass",
        },
        "session",
    ),
    (
        "IOSXE",
        {
            "IOSXE_URL": "https://iosxe.local",
            "IOSXE_USERNAME": "admin",
            "IOSXE_PASSWORD": "pass",
        },
        "session",
    ),
    (
        "IOSXE",
        {
            "IOSXE_HOST": "192.168.1.1",
            "IOSXE_USERNAME": "admin",
            "IOSXE_PASSWORD": "pass",
        },
        "session",
    ),
    (
        "NXOS",
        {
            "NXOS_HOST": "192.168.1.2",
            "NXOS_USERNAME": "admin",
            "NXOS_PASSWORD": "pass",
        },
        "session",
    ),
]

# Partial credential scenarios for error testing
PARTIAL_CREDENTIALS: list[tuple[str, dict[str, str], str]] = [
    # (expected_partial_controller, env_vars, description)
    (
        "ACI",
        {"ACI_URL": "https://apic.local", "ACI_USERNAME": "admin"},
        "missing password",
    ),
    ("SDWAN", {"SDWAN_URL": "https://sdwan.local"}, "URL only"),
    (
        "IOSXE",
        {"IOSXE_URL": "https://iosxe.local", "IOSXE_PASSWORD": "pass"},
        "missing username",
    ),
    ("CC", {"CC_URL": "https://cc.local"}, "URL only"),
    (
        "NXOS",
        {"NXOS_HOST": "192.168.1.2", "NXOS_USERNAME": "admin"},
        "missing password",
    ),
]


class TestControllerResolutionContract:
    """Contract tests verifying resolve_controller() and detect_controller_type() equivalence.

    These tests ensure the new API (resolve_controller) and deprecated API
    (detect_controller_type) return equivalent results. The deprecated function
    delegates to resolve_controller(), so these tests catch any drift.

    When Phase 3 removes detect_controller_type(), remove the deprecated assertions
    but keep the resolve_controller tests as the primary coverage.
    """

    @pytest.mark.parametrize(
        "controller_type,env_vars,expected_auth",
        CONTROLLER_CREDENTIALS,
        ids=[f"{c[0]}-{c[2]}" for c in CONTROLLER_CREDENTIALS],
    )
    def test_success_both_apis_match(
        self,
        monkeypatch: pytest.MonkeyPatch,
        controller_type: str,
        env_vars: dict[str, str],
        expected_auth: str,
    ) -> None:
        """Both APIs return same controller_type; resolve_controller includes auth_method."""
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

        # New API: returns ControllerContext with type and auth
        ctx = resolve_controller()
        assert ctx.controller_type == controller_type
        assert ctx.auth_method == expected_auth

        # Deprecated API: returns just controller_type (delegates to resolve_controller)
        deprecated_result = detect_controller_type()
        assert deprecated_result == ctx.controller_type, (
            f"Contract violation: detect_controller_type() returned {deprecated_result}, "
            f"but resolve_controller().controller_type is {ctx.controller_type}"
        )

    def test_no_credentials_both_apis_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No credentials: resolve raises NoCredentialsFound, detect raises ValueError."""
        # New API: typed exception
        with pytest.raises(NoCredentialsFound):
            resolve_controller()

        # Deprecated API: ValueError for backwards compat
        with pytest.raises(ValueError) as exc_info:
            detect_controller_type()
        assert "No controller credentials" in str(exc_info.value)

    @pytest.mark.parametrize(
        "expected_partial,env_vars,scenario",
        PARTIAL_CREDENTIALS,
        ids=[f"{c[0]}-{c[2]}" for c in PARTIAL_CREDENTIALS],
    )
    def test_incomplete_credentials_both_apis_raise(
        self,
        monkeypatch: pytest.MonkeyPatch,
        expected_partial: str,
        env_vars: dict[str, str],
        scenario: str,
    ) -> None:
        """Partial credentials: resolve raises IncompleteCredentials, detect raises ValueError."""
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

        # New API: typed exception with controller list
        with pytest.raises(IncompleteCredentials) as exc_info:
            resolve_controller()
        assert expected_partial in exc_info.value.partial_controllers

        # Deprecated API: ValueError with same info
        with pytest.raises(ValueError) as val_exc:
            detect_controller_type()
        assert f"{expected_partial}: incomplete credentials" in str(val_exc.value)

    def test_multiple_controllers_both_apis_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple complete controllers: both APIs raise appropriately."""
        # Set ACI credentials
        monkeypatch.setenv("ACI_URL", "https://apic.local")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "pass")
        # Set CC credentials
        monkeypatch.setenv("CC_URL", "https://cc.local")
        monkeypatch.setenv("CC_USERNAME", "admin")
        monkeypatch.setenv("CC_PASSWORD", "pass")

        # New API: typed exception
        with pytest.raises(MultipleControllersFound) as exc_info:
            resolve_controller()
        assert "ACI" in exc_info.value.controllers
        assert "CC" in exc_info.value.controllers

        # Deprecated API: ValueError
        with pytest.raises(ValueError) as val_exc:
            detect_controller_type()
        assert "Multiple controller credentials detected" in str(val_exc.value)


class TestGetControllerContext:
    """Tests for get_controller_context() subprocess accessor.

    This function is used by PyATS subprocesses to retrieve the resolved
    controller context. It reads from NAC_TEST_CONTROLLER_CONTEXT env var
    (primary path) or falls back to detect_controller_type() (transitional).
    """

    def test_reads_from_env_var(
        self, monkeypatch: pytest.MonkeyPatch, sdwan_context: ControllerContext
    ) -> None:
        """Primary path: deserializes from NAC_TEST_CONTROLLER_CONTEXT."""
        monkeypatch.setenv(ENV_CONTROLLER_CONTEXT, sdwan_context.to_json())
        result = get_controller_context()
        assert result.controller_type == "SDWAN"
        assert result.auth_method == "session"

    def test_fallback_to_detect_when_env_var_absent(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Transitional fallback: invokes detect_controller_type() with info log."""
        # Set controller credentials (fallback path will detect)
        monkeypatch.setenv("ACI_URL", "https://apic.local")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "pass")
        # NAC_TEST_CONTROLLER_CONTEXT deliberately not set

        with caplog.at_level(logging.INFO, logger="nac_test.core.controller"):
            ctx = get_controller_context()

        assert ctx.controller_type == "ACI"
        assert ctx.auth_method == "session"
        assert "falling back to detect_controller_type" in caplog.text


class TestControllerContextSerialization:
    """Contract tests for ControllerContext JSON serialization round-trip."""

    def test_to_json_round_trip(self, aci_context: ControllerContext) -> None:
        """ControllerContext serializes and deserializes correctly."""
        raw = aci_context.to_json()
        restored = ControllerContext.from_json(raw)
        assert restored == aci_context

    def test_from_json_ignores_unknown_fields(self) -> None:
        """Unknown JSON fields are silently ignored (forward compatibility)."""

        raw = json.dumps(
            {
                "controller_type": "ACI",
                "auth_method": "session",
                "future_field": "some_value",
            }
        )
        ctx = ControllerContext.from_json(raw)
        assert ctx.controller_type == "ACI"
        assert ctx.auth_method == "session"
        assert not hasattr(ctx, "future_field")

    def test_from_json_missing_field_raises(self) -> None:
        """Missing required fields raise KeyError."""

        raw = json.dumps({"controller_type": "ACI"})
        with pytest.raises(KeyError):
            ControllerContext.from_json(raw)

    def test_from_json_malformed_raises(self) -> None:
        """Malformed JSON raises ValueError."""

        with pytest.raises(ValueError):
            ControllerContext.from_json("not-json")


class TestFormatResolutionError:
    """Tests for format_resolution_error()."""

    def test_format_no_credentials(self) -> None:
        error = NoCredentialsFound("No creds")
        result = format_resolution_error(error)
        assert "No controller credentials" in result

    def test_format_multiple_controllers(self) -> None:
        error = MultipleControllersFound(["ACI", "SDWAN"])
        result = format_resolution_error(error)
        assert "ACI" in result
        assert "SDWAN" in result

    def test_format_incomplete_credentials(self) -> None:
        error = IncompleteCredentials(["ACI"])
        result = format_resolution_error(error)
        assert "ACI" in result
        assert "Incomplete" in result


class TestHelperFunctions:
    """Test helper functions for credential detection."""

    def test_find_credential_sets_complete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test finding complete credential sets."""
        # Set complete credentials for CC
        monkeypatch.setenv("CC_URL", "https://cc.example.com")
        monkeypatch.setenv("CC_USERNAME", "admin")
        monkeypatch.setenv("CC_PASSWORD", "password")

        complete, partial = _find_credential_sets()

        assert list(complete.keys()) == ["CC"]
        assert partial == []
        assert "CC" in complete
        assert complete["CC"].auth_method == "session"

    def test_find_credential_sets_partial(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test finding partial credential sets."""
        # Set partial credentials for FMC (missing password)
        monkeypatch.setenv("FMC_URL", "https://fmc.example.com")
        monkeypatch.setenv("FMC_USERNAME", "admin")
        # No FMC_PASSWORD

        complete, partial = _find_credential_sets()

        assert complete == {}
        assert "FMC" in partial

    def test_find_credential_sets_multiple_partial(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test finding multiple partial credential sets."""
        # Partial ISE credentials
        monkeypatch.setenv("ISE_URL", "https://ise.example.com")
        # Missing ISE_USERNAME and ISE_PASSWORD

        # Partial MERAKI credentials
        monkeypatch.setenv("MERAKI_USERNAME", "meraki_user")
        # Missing MERAKI_URL and MERAKI_PASSWORD

        complete, partial = _find_credential_sets()

        assert complete == {}
        assert len(partial) == 2
        assert "ISE" in partial
        assert "MERAKI" in partial

    def test_format_multiple_credentials_error(self) -> None:
        """Test formatting error message for multiple controllers."""
        error_msg = _format_multiple_credentials_error(["ACI", "SDWAN", "CC"])

        assert "Multiple controller credentials detected: ACI, SDWAN, CC" in error_msg
        assert "To use ACI only:" in error_msg
        # SDWAN has two credential sets, so all env vars from both sets appear
        assert (
            "unset SDWAN_URL SDWAN_API_TOKEN SDWAN_USERNAME SDWAN_PASSWORD" in error_msg
        )
        assert "CC_URL CC_USERNAME CC_PASSWORD" in error_msg
        assert "To use SDWAN only:" in error_msg
        assert (
            "unset ACI_URL ACI_USERNAME ACI_PASSWORD CC_URL CC_USERNAME CC_PASSWORD"
            in error_msg
        )
        assert "To use CC only:" in error_msg
        assert "Use a separate shell session" in error_msg

    def test_format_no_credentials_error(self) -> None:
        """Test formatting error message for no credentials."""
        error_msg = _format_no_credentials_error()

        assert "No controller credentials found in environment" in error_msg
        assert "Controller credentials are required for ALL test types" in error_msg
        assert "ACI:" in error_msg
        assert "export ACI_URL=<value>" in error_msg
        assert "SDWAN:" in error_msg
        assert "export SDWAN_URL=<value>" in error_msg
        assert "Example for ACI:" in error_msg
        assert "Set credentials for only ONE controller type at a time" in error_msg


class TestControllerEdgeCases:
    """Edge cases for controller detection: unicode, whitespace, case sensitivity, etc.

    These tests verify behavior with unusual input values that could break
    credential detection or URL handling.
    """

    def test_case_sensitivity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that environment variable names are case-sensitive."""
        # Set lowercase variables (should not be detected)
        monkeypatch.setenv("aci_url", "https://apic.example.com")
        monkeypatch.setenv("aci_username", "admin")
        monkeypatch.setenv("aci_password", "password")

        with pytest.raises(ValueError) as exc_info:
            detect_controller_type()

        assert "No controller credentials found" in str(exc_info.value)

    def test_special_characters_in_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test handling of special characters in credential values."""
        # Set credentials with special characters
        monkeypatch.setenv("CC_URL", "https://cc.example.com:8443/path")
        monkeypatch.setenv("CC_USERNAME", "user@domain.com")
        monkeypatch.setenv("CC_PASSWORD", "p@$$w0rd!#$%^&*()")

        result = detect_controller_type()
        assert result == "CC"

    def test_legacy_controller_type_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that legacy CONTROLLER_TYPE variable is ignored."""
        # Set legacy CONTROLLER_TYPE (should be ignored)
        monkeypatch.setenv("CONTROLLER_TYPE", "APIC")

        # Set actual SDWAN credentials
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        result = detect_controller_type()
        assert (
            result == "SDWAN"
        )  # Should use credential-based detection, not CONTROLLER_TYPE

    def test_mixed_complete_and_partial_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test scenario with one complete and one partial credential set."""
        # Complete FMC credentials
        monkeypatch.setenv("FMC_URL", "https://fmc.example.com")
        monkeypatch.setenv("FMC_USERNAME", "admin")
        monkeypatch.setenv("FMC_PASSWORD", "password")

        # Partial ISE credentials (missing password)
        monkeypatch.setenv("ISE_URL", "https://ise.example.com")
        monkeypatch.setenv("ISE_USERNAME", "ise_admin")

        result = detect_controller_type()
        assert result == "FMC"  # Should detect the complete set

    def test_whitespace_trimming_in_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that leading/trailing whitespace in values is handled correctly."""
        # Set credentials with extra whitespace (should still work)
        monkeypatch.setenv("MERAKI_URL", "  https://meraki.example.com  ")
        monkeypatch.setenv("MERAKI_USERNAME", "  admin  ")
        monkeypatch.setenv("MERAKI_PASSWORD", "  password  ")

        result = detect_controller_type()
        assert result == "MERAKI"

    def test_truly_empty_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test with a completely empty environment."""
        # Clear all controller-related environment variables
        for config in CONTROLLER_REGISTRY.values():
            for cred_set in config.credential_sets:
                for var in cred_set.env_vars:
                    monkeypatch.delenv(var, raising=False)

        with pytest.raises(ValueError) as exc_info:
            detect_controller_type()

        error_msg = str(exc_info.value)
        assert "No controller credentials found" in error_msg

    def test_three_way_multiple_controllers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test error message with three controllers configured."""
        # Set credentials for three controllers
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "aci_user")
        monkeypatch.setenv("ACI_PASSWORD", "aci_pass")

        monkeypatch.setenv("CC_URL", "https://cc.example.com")
        monkeypatch.setenv("CC_USERNAME", "cc_user")
        monkeypatch.setenv("CC_PASSWORD", "cc_pass")

        monkeypatch.setenv("ISE_URL", "https://ise.example.com")
        monkeypatch.setenv("ISE_USERNAME", "ise_user")
        monkeypatch.setenv("ISE_PASSWORD", "ise_pass")

        with pytest.raises(ValueError) as exc_info:
            detect_controller_type()

        error_msg = str(exc_info.value)
        assert "Multiple controller credentials detected: ACI, CC, ISE" in error_msg
        assert "To use ACI only:" in error_msg
        assert "To use CC only:" in error_msg
        assert "To use ISE only:" in error_msg

    def test_unicode_in_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test handling of unicode characters in credentials."""
        # Set credentials with unicode characters
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "用户名")  # Chinese characters
        monkeypatch.setenv("ACI_PASSWORD", "пароль")  # Cyrillic characters

        result = detect_controller_type()
        assert result == "ACI"

    def test_url_with_path_and_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test URL values with paths and query parameters."""
        monkeypatch.setenv(
            "SDWAN_URL", "https://vmanage.example.com:8443/api/v1?test=true"
        )
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        result = detect_controller_type()
        assert result == "SDWAN"

    def test_iosxe_partial_and_sdwan_partial_are_both_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression test: IOSXE_URL + IOSXE_PASSWORD (no IOSXE_USERNAME) combined
        with SDWAN_URL must NOT detect IOSXE — both controllers should be reported
        as partial, not complete.

        Before the fix that added IOSXE_USERNAME/IOSXE_PASSWORD to the IOSXE
        credential sets, setting only IOSXE_URL was enough to satisfy detection,
        so this combination incorrectly returned 'IOSXE' instead of raising.
        """
        monkeypatch.setenv("IOSXE_URL", "https://iosxe.example.com")
        monkeypatch.setenv("IOSXE_PASSWORD", "cisco123")
        # IOSXE_USERNAME deliberately omitted — credential set must not be satisfied
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        # No SDWAN credentials beyond URL

        with pytest.raises(ValueError) as exc_info:
            detect_controller_type()

        error_msg = str(exc_info.value)
        assert "Incomplete controller credentials detected" in error_msg
        assert "IOSXE: incomplete credentials" in error_msg
        assert "SDWAN: incomplete credentials" in error_msg

    def test_empty_string_handling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that empty string values are treated as missing."""
        # Set ACI credentials with empty password
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "")  # Empty string

        with pytest.raises(ValueError) as exc_info:
            detect_controller_type()

        error_msg = str(exc_info.value)
        assert "Incomplete controller credentials detected" in error_msg
        assert "ACI: incomplete credentials" in error_msg

    def test_whitespace_handling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that whitespace-only values are treated as missing."""
        # Set SDWAN credentials with whitespace-only password
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "   ")  # Only whitespace

        with pytest.raises(ValueError) as exc_info:
            detect_controller_type()

        error_msg = str(exc_info.value)
        assert "Incomplete controller credentials detected" in error_msg
        assert "SDWAN: incomplete credentials" in error_msg

    def test_d2d_scenario_with_dummy_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test D2D scenario where controller credentials are still required."""
        # Set complete ACI credentials (even for D2D tests)
        monkeypatch.setenv("ACI_URL", "https://dummy.controller.local")
        monkeypatch.setenv("ACI_USERNAME", "dummy")
        monkeypatch.setenv("ACI_PASSWORD", "dummy")

        # Also set device credentials (for D2D)
        monkeypatch.setenv("IOSXE_USERNAME", "device_user")
        monkeypatch.setenv("IOSXE_PASSWORD", "device_pass")

        result = detect_controller_type()
        assert result == "ACI"  # Controller type still detected


class TestIOSXEAlternativeURLEnvVar:
    """Test IOSXE controller detection with alternative URL environment variables.

    IOSXE supports both IOSXE_URL and IOSXE_HOST as the URL environment variable
    via separate credential sets. The first matching credential set wins.
    """

    def test_detect_iosxe_with_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test IOSXE detection with alternative IOSXE_HOST env var."""
        monkeypatch.setenv("IOSXE_HOST", "192.168.1.1")
        monkeypatch.setenv("IOSXE_USERNAME", "admin")
        monkeypatch.setenv("IOSXE_PASSWORD", "password")

        result = detect_controller_type()
        assert result == "IOSXE"

    def test_iosxe_url_takes_precedence_over_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When both IOSXE_URL and IOSXE_HOST are set, URL takes precedence."""
        monkeypatch.setenv("IOSXE_URL", "https://iosxe-url.example.com")
        monkeypatch.setenv("IOSXE_HOST", "192.168.1.1")
        monkeypatch.setenv("IOSXE_USERNAME", "admin")
        monkeypatch.setenv("IOSXE_PASSWORD", "password")

        result = detect_controller_type()
        assert result == "IOSXE"

        # Verify URL takes precedence in get_controller_url
        url = get_controller_url("IOSXE")
        assert url == "https://iosxe-url.example.com"

    def test_get_controller_url_returns_iosxe_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_controller_url returns IOSXE_HOST when IOSXE_URL is not set."""
        monkeypatch.setenv("IOSXE_HOST", "192.168.1.1")

        url = get_controller_url("IOSXE")
        assert url == "192.168.1.1"

    def test_get_controller_url_raises_when_neither_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_controller_url raises KeyError when neither URL nor HOST is set."""
        # Neither IOSXE_URL nor IOSXE_HOST is set

        with pytest.raises(KeyError) as exc_info:
            get_controller_url("IOSXE")

        assert "IOSXE_URL" in str(exc_info.value)

    def test_get_controller_url_strips_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_controller_url strips leading/trailing whitespace."""
        monkeypatch.setenv("IOSXE_HOST", "  192.168.1.1  ")

        url = get_controller_url("IOSXE")
        assert url == "192.168.1.1"

    def test_get_controller_url_empty_url_falls_back_to_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_controller_url uses IOSXE_HOST when IOSXE_URL is empty."""
        monkeypatch.setenv("IOSXE_URL", "")
        monkeypatch.setenv("IOSXE_HOST", "192.168.1.1")

        url = get_controller_url("IOSXE")
        assert url == "192.168.1.1"

    def test_get_controller_url_whitespace_url_falls_back_to_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_controller_url uses IOSXE_HOST when IOSXE_URL is only whitespace."""
        monkeypatch.setenv("IOSXE_URL", "   ")
        monkeypatch.setenv("IOSXE_HOST", "192.168.1.1")

        url = get_controller_url("IOSXE")
        assert url == "192.168.1.1"


class TestGetControllerUrl:
    """Tests for get_controller_url function."""

    @pytest.mark.parametrize(
        "controller_type,url_env_var",
        [
            ("ACI", "ACI_URL"),
            ("SDWAN", "SDWAN_URL"),
            ("CC", "CC_URL"),
            ("MERAKI", "MERAKI_URL"),
            ("FMC", "FMC_URL"),
            ("ISE", "ISE_URL"),
            ("IOSXE", "IOSXE_URL"),
            ("NXOS", "NXOS_HOST"),
        ],
    )
    def test_get_controller_url_returns_correct_value(
        self, monkeypatch: pytest.MonkeyPatch, controller_type: str, url_env_var: str
    ) -> None:
        """Test that get_controller_url returns the correct URL for each controller."""
        expected_url = f"https://{controller_type.lower()}.example.com"
        monkeypatch.setenv(url_env_var, expected_url)

        result = get_controller_url(controller_type)
        assert result == expected_url

    def test_get_controller_url_unknown_controller_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test fallback for unknown controller types."""
        monkeypatch.setenv("UNKNOWN_URL", "https://unknown.example.com")

        result = get_controller_url("UNKNOWN")
        assert result == "https://unknown.example.com"

    def test_get_controller_url_unknown_controller_raises_when_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that unknown controller type raises KeyError when env var not set."""
        with pytest.raises(KeyError) as exc_info:
            get_controller_url("NONEXISTENT")

        assert "NONEXISTENT_URL" in str(exc_info.value)


class TestSDWANCredentialSets:
    """Test SDWAN controller detection with multiple credential sets.

    SDWAN supports two credential methods:
    1. API Token (20.18+): SDWAN_URL + SDWAN_API_TOKEN  (first — wins when both present)
    2. Username/Password: SDWAN_URL + SDWAN_USERNAME + SDWAN_PASSWORD
    """

    def test_detect_sdwan_with_api_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test SDWAN detection with API token credentials."""
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_API_TOKEN", "eyJhbGciOiJSUzI1NiJ9.test.sig")

        result = detect_controller_type()
        assert result == "SDWAN"

        # Token set should be matched with auth_method="token"
        cred = get_matched_credential_set("SDWAN")
        assert cred is not None
        assert cred.auth_method == "token"
        assert cred.label == "API Token (20.18+)"

    def test_detect_sdwan_with_username_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test SDWAN detection with traditional username/password."""
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        result = detect_controller_type()
        assert result == "SDWAN"

        # Password set should be matched with auth_method="session"
        cred = get_matched_credential_set("SDWAN")
        assert cred is not None
        assert cred.auth_method == "session"
        assert cred.label == "Username/Password"

    def test_api_token_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both credential sets are satisfied, token set wins (listed first)."""
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_API_TOKEN", "eyJhbGciOiJSUzI1NiJ9.test.sig")
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        # Should still detect exactly one SDWAN (not duplicate)
        result = detect_controller_type()
        assert result == "SDWAN"

        # Token set wins because it's listed first
        cred = get_matched_credential_set("SDWAN")
        assert cred is not None
        assert cred.auth_method == "token"

    def test_partial_token_set_falls_back_to_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When SDWAN_API_TOKEN is missing but username/password present, detect SDWAN."""
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        # No SDWAN_API_TOKEN
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        result = detect_controller_type()
        assert result == "SDWAN"

        # Password set matched because token set was incomplete
        cred = get_matched_credential_set("SDWAN")
        assert cred is not None
        assert cred.auth_method == "session"

    def test_empty_api_token_falls_back_to_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty SDWAN_API_TOKEN should not satisfy the token credential set."""
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_API_TOKEN", "")
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        result = detect_controller_type()
        assert result == "SDWAN"

        # Should fall back to session auth
        cred = get_matched_credential_set("SDWAN")
        assert cred is not None
        assert cred.auth_method == "session"

    def test_url_only_is_partial(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SDWAN_URL alone (no token, no username/password) is partial."""
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")

        with pytest.raises(ValueError) as exc_info:
            detect_controller_type()

        error_msg = str(exc_info.value)
        assert "Incomplete controller credentials detected" in error_msg
        assert "SDWAN: incomplete credentials" in error_msg
        assert "API Token (20.18+)" in error_msg
        assert "Username/Password" in error_msg

    def test_get_matched_credential_set_before_detection(self) -> None:
        """get_matched_credential_set returns None before detect_controller_type runs."""
        assert get_matched_credential_set("SDWAN") is None

    def test_credential_set_auth_method_default(self) -> None:
        """CredentialSet.auth_method defaults to 'session'."""
        cs = CredentialSet(
            fields={"url": "X_URL", "username": "X_USER", "password": "X_PASS"},
            label="test",
        )
        assert cs.auth_method == "session"

    def test_aci_matched_credential_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ACI detection stores matched credential set with session auth."""
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "password")

        detect_controller_type()

        cred = get_matched_credential_set("ACI")
        assert cred is not None
        assert cred.auth_method == "session"
        assert cred.label == "Username/Password"


class TestGetControllerUrlSDWAN:
    """Tests for get_controller_url with multi-credential-set controllers (SDWAN)."""

    def test_sdwan_does_not_return_token_when_url_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_controller_url raises KeyError when SDWAN_URL is empty, not returning token."""
        monkeypatch.setenv("SDWAN_URL", "")
        monkeypatch.setenv("SDWAN_API_TOKEN", "eyJhbGciOiJSUzI1NiJ9.test.sig")

        with pytest.raises(KeyError) as exc_info:
            get_controller_url("SDWAN")

        assert "SDWAN_URL" in str(exc_info.value)

    def test_sdwan_does_not_return_token_when_url_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_controller_url raises KeyError when SDWAN_URL is whitespace-only."""
        monkeypatch.setenv("SDWAN_URL", "   ")
        monkeypatch.setenv("SDWAN_API_TOKEN", "some-token")

        with pytest.raises(KeyError) as exc_info:
            get_controller_url("SDWAN")

        assert "SDWAN_URL" in str(exc_info.value)

    def test_sdwan_does_not_return_username_when_url_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_controller_url raises KeyError, not returning username/password vars."""
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        with pytest.raises(KeyError) as exc_info:
            get_controller_url("SDWAN")

        assert "SDWAN_URL" in str(exc_info.value)


class TestGetConnectionParams:
    """Tests for get_connection_params()."""

    def test_aci_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ACI session auth resolves url/username/password by kind."""
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "password")

        params = get_connection_params("ACI", AuthMethod.SESSION)

        assert params == {
            "url": "https://apic.example.com",
            "username": "admin",
            "password": "password",
        }

    def test_sdwan_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SDWAN token auth resolves url/token by kind."""
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_API_TOKEN", "abc.def.ghi")

        params = get_connection_params("SDWAN", AuthMethod.TOKEN)

        assert params == {
            "url": "https://vmanage.example.com",
            "token": "abc.def.ghi",
        }

    def test_sdwan_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SDWAN session auth resolves url/username/password by kind."""
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")

        params = get_connection_params("SDWAN", AuthMethod.SESSION)

        assert params == {
            "url": "https://vmanage.example.com",
            "username": "admin",
            "password": "password",
        }

    def test_cc_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CC session auth resolves url/username/password by kind."""
        monkeypatch.setenv("CC_URL", "https://dnac.example.com")
        monkeypatch.setenv("CC_USERNAME", "admin")
        monkeypatch.setenv("CC_PASSWORD", "password")

        params = get_connection_params("CC", AuthMethod.SESSION)

        assert params == {
            "url": "https://dnac.example.com",
            "username": "admin",
            "password": "password",
        }

    def test_unknown_controller_type_raises_key_error(self) -> None:
        """Unknown controller_type raises KeyError."""
        with pytest.raises(KeyError):
            get_connection_params("BOGUS", AuthMethod.SESSION)

    def test_unmatched_auth_method_raises_value_error(self) -> None:
        """auth_method with no matching credential set raises ValueError."""
        with pytest.raises(ValueError, match="auth_method"):
            get_connection_params("ACI", AuthMethod.TOKEN)

    def test_missing_env_vars_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unset env vars raise ValueError listing the missing var names."""
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.delenv("ACI_USERNAME", raising=False)
        monkeypatch.delenv("ACI_PASSWORD", raising=False)

        with pytest.raises(ValueError) as exc_info:
            get_connection_params("ACI", AuthMethod.SESSION)

        assert "ACI_USERNAME" in str(exc_info.value)
        assert "ACI_PASSWORD" in str(exc_info.value)

    @pytest.mark.parametrize(
        "empty_value",
        ["", "   "],
        ids=["empty-string", "whitespace-only"],
    )
    def test_empty_env_vars_treated_as_missing(
        self, monkeypatch: pytest.MonkeyPatch, empty_value: str
    ) -> None:
        """Empty/whitespace-only env vars are treated as missing in get_connection_params.

        Ensures that os.environ vars set to '' or whitespace trigger the same
        ValueError as completely unset vars — guards against CI environments or
        docker-compose files that export VAR= with no value.
        """
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", empty_value)

        with pytest.raises(ValueError) as exc_info:
            get_connection_params("ACI", AuthMethod.SESSION)

        assert "ACI_PASSWORD" in str(exc_info.value)

    def test_meraki_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MERAKI session auth resolves url/username/password by kind."""
        monkeypatch.setenv("MERAKI_URL", "https://meraki.example.com")
        monkeypatch.setenv("MERAKI_USERNAME", "admin")
        monkeypatch.setenv("MERAKI_PASSWORD", "password")

        params = get_connection_params("MERAKI", AuthMethod.SESSION)

        assert params == {
            "url": "https://meraki.example.com",
            "username": "admin",
            "password": "password",
        }

    def test_env_vars_and_kinds_derived_from_fields(self) -> None:
        """env_vars/kinds are computed from `fields`, so they can never mismatch."""
        cs = CredentialSet(
            fields={"url": "BAD_URL", "username": "BAD_USERNAME"}, label="Broken"
        )
        assert cs.env_vars == ("BAD_URL", "BAD_USERNAME")
        assert cs.kinds == ("url", "username")

    def test_iosxe_host_variant_resolves_when_url_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IOSXE_HOST alone (no IOSXE_URL) resolves via the Host credential set.

        Both IOSXE_URL and IOSXE_HOST share auth_method="session", so the first
        fully-satisfied candidate must win - not just the first one in order.
        """
        monkeypatch.delenv("IOSXE_URL", raising=False)
        monkeypatch.setenv("IOSXE_HOST", "192.168.1.1")
        monkeypatch.setenv("IOSXE_USERNAME", "admin")
        monkeypatch.setenv("IOSXE_PASSWORD", "password")

        params = get_connection_params("IOSXE", AuthMethod.SESSION)

        assert params == {
            "url": "192.168.1.1",
            "username": "admin",
            "password": "password",
        }

    def test_iosxe_reports_url_variant_missing_vars_when_nothing_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With neither variant configured, the first (URL) set's vars are reported."""
        monkeypatch.delenv("IOSXE_URL", raising=False)
        monkeypatch.delenv("IOSXE_HOST", raising=False)
        monkeypatch.delenv("IOSXE_USERNAME", raising=False)
        monkeypatch.delenv("IOSXE_PASSWORD", raising=False)

        with pytest.raises(ValueError) as exc_info:
            get_connection_params("IOSXE", AuthMethod.SESSION)

        assert "IOSXE_URL" in str(exc_info.value)

    def test_iosxe_reports_host_variant_missing_vars_when_partially_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IOSXE_HOST set but username/password missing reports IOSXE_HOST vars,

        not IOSXE_URL - the caller never touched the URL variant, so the
        error must point at the variant they actually started configuring.
        """
        monkeypatch.delenv("IOSXE_URL", raising=False)
        monkeypatch.setenv("IOSXE_HOST", "192.168.1.1")
        monkeypatch.delenv("IOSXE_USERNAME", raising=False)
        monkeypatch.delenv("IOSXE_PASSWORD", raising=False)

        with pytest.raises(ValueError) as exc_info:
            get_connection_params("IOSXE", AuthMethod.SESSION)

        error_msg = str(exc_info.value)
        assert "IOSXE_USERNAME" in error_msg
        assert "IOSXE_PASSWORD" in error_msg
        assert "IOSXE_URL" not in error_msg


class TestShouldVerifySsl:
    """Tests for should_verify_ssl()."""

    def test_defaults_false_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset env var defaults to False (skip verify), matching prior adapter behavior."""
        monkeypatch.delenv("ACI_INSECURE", raising=False)

        assert should_verify_ssl("ACI") is False

    def test_defaults_false_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty string is treated the same as unset."""
        monkeypatch.setenv("CC_INSECURE", "")

        assert should_verify_ssl("CC") is False

    @pytest.mark.parametrize("raw", ["True", "true", "1", "yes", "YES"])
    def test_insecure_truthy_means_no_verify(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """When INSECURE env var is truthy, should_verify_ssl returns False."""
        monkeypatch.setenv("SDWAN_INSECURE", raw)

        assert should_verify_ssl("SDWAN") is False

    @pytest.mark.parametrize("raw", ["False", "false", "0", "no"])
    def test_insecure_falsy_means_verify(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """When INSECURE env var is falsy, should_verify_ssl returns True (verify)."""
        monkeypatch.setenv("SDWAN_INSECURE", raw)

        assert should_verify_ssl("SDWAN") is True

    def test_custom_default_used_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `default` param controls the unset fallback."""
        monkeypatch.delenv("ISE_INSECURE", raising=False)

        assert should_verify_ssl("ISE", default=True) is True

    def test_unknown_controller_type_raises_key_error(self) -> None:
        """Unknown controller_type raises KeyError."""
        with pytest.raises(KeyError):
            should_verify_ssl("BOGUS")
