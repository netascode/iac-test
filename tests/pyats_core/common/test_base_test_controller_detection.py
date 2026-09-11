# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Test base_test.py controller detection integration."""

import logging
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from pyats import aetest

from nac_test.core.constants import ENV_CONTROLLER_CONTEXT
from nac_test.core.types import AuthMethod, ControllerContext
from nac_test.pyats_core.common.base_test import NACTestBase


@pytest.fixture
def setup_test_data_file_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """Create temp data file and set MERGED_DATA_MODEL_TEST_VARIABLES_FILEPATH."""
    temp_file = tmp_path / "test.json"
    temp_file.write_text('{"test": "data"}')
    monkeypatch.setenv("MERGED_DATA_MODEL_TEST_VARIABLES_FILEPATH", str(temp_file))
    yield temp_file


class TestBaseTestControllerDetection:
    """Test controller detection integration in NACTestBase."""

    def test_base_test_detects_controller_on_setup(
        self, monkeypatch: pytest.MonkeyPatch, setup_test_data_file_env: Path
    ) -> None:
        """Test that NACTestBase detects controller type during setup."""
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "password")

        class TestClass(NACTestBase):
            @aetest.test  # type: ignore[misc]
            def test_method(self) -> None:
                pass

        test_instance = TestClass()

        with patch.object(
            test_instance, "load_data_model", return_value={"test": "data"}
        ):
            test_instance.setup()

        assert test_instance.controller_type == "ACI"
        assert test_instance.controller_url == "https://apic.example.com"
        assert test_instance.username == "admin"
        assert test_instance.password == "password"
        assert test_instance.auth_method == "session"
        assert test_instance.connection_params == {
            "url": "https://apic.example.com",
            "username": "admin",
            "password": "password",
        }

    def test_base_test_connection_params_populated_for_iosxe(
        self, monkeypatch: pytest.MonkeyPatch, setup_test_data_file_env: Path
    ) -> None:
        """connection_params resolves for IOSXE too, now that kinds are populated."""
        for env_var in ["ACI_URL", "SDWAN_URL", "CC_URL"]:
            monkeypatch.delenv(env_var, raising=False)
        monkeypatch.setenv("IOSXE_URL", "10.0.0.1")
        monkeypatch.setenv("IOSXE_USERNAME", "admin")
        monkeypatch.setenv("IOSXE_PASSWORD", "password")

        class TestClass(NACTestBase):
            @aetest.test  # type: ignore[misc]
            def test_method(self) -> None:
                pass

        test_instance = TestClass()

        with patch.object(
            test_instance, "load_data_model", return_value={"test": "data"}
        ):
            test_instance.setup()

        assert test_instance.controller_type == "IOSXE"
        assert test_instance.connection_params == {
            "url": "10.0.0.1",
            "username": "admin",
            "password": "password",
        }

    def test_base_test_fails_setup_on_detection_error(
        self, monkeypatch: pytest.MonkeyPatch, setup_test_data_file_env: Path
    ) -> None:
        """Test that NACTestBase fails setup when controller detection fails."""
        for env_var in [
            "ACI_URL",
            "ACI_USERNAME",
            "ACI_PASSWORD",
            "SDWAN_URL",
            "SDWAN_USERNAME",
            "SDWAN_PASSWORD",
            "CC_URL",
            "CC_USERNAME",
            "CC_PASSWORD",
        ]:
            monkeypatch.delenv(env_var, raising=False)

        class TestClass(NACTestBase):
            @aetest.test  # type: ignore[misc]
            def test_method(self) -> None:
                pass

        test_instance = TestClass()

        with patch.object(
            test_instance, "load_data_model", return_value={"test": "data"}
        ):
            with pytest.raises(ValueError) as exc_info:
                test_instance.setup()

            assert "No controller credentials found" in str(exc_info.value)

    def test_base_test_no_longer_uses_controller_type_env_var(
        self, monkeypatch: pytest.MonkeyPatch, setup_test_data_file_env: Path
    ) -> None:
        """Test that NACTestBase ignores CONTROLLER_TYPE environment variable."""
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_USERNAME", "admin")
        monkeypatch.setenv("SDWAN_PASSWORD", "password")
        monkeypatch.setenv("CONTROLLER_TYPE", "ACI")

        class TestClass(NACTestBase):
            @aetest.test  # type: ignore[misc]
            def test_method(self) -> None:
                pass

        test_instance = TestClass()

        with patch.object(
            test_instance, "load_data_model", return_value={"test": "data"}
        ):
            test_instance.setup()

        assert test_instance.controller_type == "SDWAN"
        assert test_instance.controller_url == "https://vmanage.example.com"

    def test_base_test_handles_multiple_controllers_error(
        self, monkeypatch: pytest.MonkeyPatch, setup_test_data_file_env: Path
    ) -> None:
        """Test that NACTestBase handles multiple controller credentials error during setup."""
        monkeypatch.setenv("ACI_URL", "https://apic.example.com")
        monkeypatch.setenv("ACI_USERNAME", "admin")
        monkeypatch.setenv("ACI_PASSWORD", "password")
        monkeypatch.setenv("CC_URL", "https://cc.example.com")
        monkeypatch.setenv("CC_USERNAME", "admin")
        monkeypatch.setenv("CC_PASSWORD", "password")

        class TestClass(NACTestBase):
            @aetest.test  # type: ignore[misc]
            def test_method(self) -> None:
                pass

        test_instance = TestClass()

        with patch.object(
            test_instance, "load_data_model", return_value={"test": "data"}
        ):
            with pytest.raises(ValueError) as exc_info:
                test_instance.setup()

            assert "Multiple controller credentials detected" in str(exc_info.value)

    def test_base_test_uses_serialized_controller_context(
        self, monkeypatch: pytest.MonkeyPatch, setup_test_data_file_env: Path
    ) -> None:
        """Test that NACTestBase uses NAC_TEST_CONTROLLER_CONTEXT when present (primary path)."""
        # Set serialized context (primary path) AND the underlying env vars
        ctx = ControllerContext(controller_type="SDWAN", auth_method=AuthMethod.TOKEN)
        monkeypatch.setenv(ENV_CONTROLLER_CONTEXT, ctx.to_json())
        # Need SDWAN env vars for get_controller_url() and get_connection_params()
        monkeypatch.setenv("SDWAN_URL", "https://vmanage.example.com")
        monkeypatch.setenv("SDWAN_API_TOKEN", "test-token-value")

        class TestClass(NACTestBase):
            @aetest.test  # type: ignore[misc]
            def test_method(self) -> None:
                pass

        test_instance = TestClass()
        with patch.object(
            test_instance, "load_data_model", return_value={"test": "data"}
        ):
            test_instance.setup()

        assert test_instance.controller_type == "SDWAN"
        assert test_instance.auth_method == "token"
        assert test_instance.connection_params["token"] == "test-token-value"


class TestBaseTestSetupErrorLogging:
    """Test that setup() logs errors via self.logger before re-raising.

    Uses real env var injection instead of patching get_controller_context so
    the tests are immune to mock-machinery differences across Python versions.
    """

    def _make_test_instance(self) -> "NACTestBase":
        class TestClass(NACTestBase):
            @aetest.test  # type: ignore[misc]
            def test_method(self) -> None:
                pass

        return TestClass()

    @pytest.mark.parametrize(
        "context_env,exc_type",
        [
            # NAC_TEST_CONTROLLER_CONTEXT absent + no controller env vars → ValueError
            (None, ValueError),
            # valid JSON but missing controller_type field → KeyError
            ('{"auth_method": "basic"}', KeyError),
            # malformed JSON → ValueError (wrapped from JSONDecodeError)
            ("not-valid-json", ValueError),
        ],
        ids=[
            "value_error",
            "key_error-missing_field",
            "value_error-malformed_json",
        ],
    )
    def test_setup_logs_error_before_reraise(
        self,
        context_env: str | None,
        exc_type: type[Exception],
        setup_test_data_file_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """setup() calls self.logger.error with 'Controller detection failed' before
        re-raising ValueError, KeyError, or JSONDecodeError from get_controller_context().
        """
        if context_env is None:
            monkeypatch.delenv(ENV_CONTROLLER_CONTEXT, raising=False)
        else:
            monkeypatch.setenv(ENV_CONTROLLER_CONTEXT, context_env)

        test_instance = self._make_test_instance()

        with patch.object(
            test_instance, "load_data_model", return_value={"test": "data"}
        ):
            with caplog.at_level(logging.ERROR):
                with pytest.raises(exc_type):
                    test_instance.setup()

        assert any(
            "Controller detection failed" in r.message
            for r in caplog.records
            if r.levelno == logging.ERROR
        )
