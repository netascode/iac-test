# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Global pytest fixtures shared across all test modules.

This module provides common fixtures used by both integration and E2E tests:
- Environment cleanup (controller credentials, proxy settings)
- Mock API server for simulating controller responses
- Class-scoped monkeypatch for environment variable management
- ControllerContext fixtures for common test scenarios
"""

import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import NamedTuple

import pytest

from nac_test.core.constants import ENV_CONTROLLER_CONTEXT
from nac_test.core.controller import CONTROLLER_REGISTRY
from nac_test.core.types import AuthMethod, ControllerContext
from tests.e2e.mocks.mock_server import MockAPIServer

# Path to the mock API configuration files
MOCK_API_CONFIG_PATH = Path(__file__).parent / "e2e" / "mocks" / "mock_api_config.yaml"
MOCK_API_CONFIG_PREFLIGHT_401_PATH = (
    Path(__file__).parent / "e2e" / "mocks" / "mock_api_config_preflight_401.yaml"
)


class PyATSTestDirs(NamedTuple):
    """Directory structure for PyATS orchestrator tests."""

    test_dir: Path
    output_dir: Path
    merged_file: Path


def assert_is_link_to(link: Path, source: Path) -> None:
    """Assert that link points to source as either a hard link or symlink."""
    if link.is_symlink():
        assert link.resolve() == source, (
            f"Symlink points to wrong location:\n"
            f"  Expected: {source}\n"
            f"  Got: {link.resolve()}"
        )
    else:
        assert link.stat().st_ino == source.stat().st_ino, (
            f"Hard link mismatch:\n"
            f"  Link inode: {link.stat().st_ino}\n"
            f"  Source inode: {source.stat().st_ino}"
        )


# =============================================================================
# Session-scoped fixtures (shared across all tests)
# =============================================================================


# Derive controller env var prefixes from registry - stays in sync automatically
CONTROLLER_ENV_PREFIXES = tuple(f"{key}_" for key in CONTROLLER_REGISTRY.keys())


@pytest.fixture(autouse=True)
def clean_controller_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all controller-related environment variables and caches.

    Ensures tests run in isolation regardless of env var leakage
    from other tests when running in parallel with pytest-xdist.
    """
    for key in list(os.environ.keys()):
        if any(key.startswith(prefix) for prefix in CONTROLLER_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)

    # Clear serialized controller context from previous tests
    monkeypatch.delenv(ENV_CONTROLLER_CONTEXT, raising=False)

    # Clear module-level credential cache to prevent cross-test pollution
    from nac_test.core import controller

    controller._matched_credential_sets.clear()


@pytest.fixture(scope="session", autouse=True)
def bypass_proxy_for_localhost() -> Generator[None, None, None]:
    """Ensure 127.0.0.1 is in no_proxy to bypass corporate proxy.

    The mock API server runs on 127.0.0.1 and must not route through proxies.
    This fixture ensures proxy bypass is configured for the entire test session.
    """
    original_no_proxy = os.environ.get("no_proxy", "")
    original_NO_PROXY = os.environ.get("NO_PROXY", "")

    if "127.0.0.1" not in original_no_proxy:
        new_no_proxy = (
            f"{original_no_proxy},127.0.0.1" if original_no_proxy else "127.0.0.1"
        )
        os.environ["no_proxy"] = new_no_proxy

    if "127.0.0.1" not in original_NO_PROXY:
        new_NO_PROXY = (
            f"{original_NO_PROXY},127.0.0.1" if original_NO_PROXY else "127.0.0.1"
        )
        os.environ["NO_PROXY"] = new_NO_PROXY

    yield

    if original_no_proxy:
        os.environ["no_proxy"] = original_no_proxy
    elif "no_proxy" in os.environ:
        del os.environ["no_proxy"]

    if original_NO_PROXY:
        os.environ["NO_PROXY"] = original_NO_PROXY
    elif "NO_PROXY" in os.environ:
        del os.environ["NO_PROXY"]


def _start_mock_server(config_path: Path) -> Generator[MockAPIServer, None, None]:
    """Start a MockAPIServer loaded from config_path and stop it after use.

    Shared factory used by scenario-specific server fixtures so each gets
    an isolated server instance with its own endpoint configuration.

    Args:
        config_path: Path to the YAML config file to load.

    Yields:
        A running MockAPIServer instance.
    """
    server = MockAPIServer()
    server.load_from_yaml(config_path)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def mock_api_server() -> Generator[MockAPIServer, None, None]:
    """Provide a mock API server for integration and E2E tests.

    The server starts automatically once per test session and loads
    configuration from tests/e2e/mocks/mock_api_config.yaml.

    Example usage in tests:
        def test_api_call(mock_api_server):
            response = requests.get(f"{mock_api_server.url}/api/devices")
            assert response.status_code == 200
    """
    yield from _start_mock_server(MOCK_API_CONFIG_PATH)


@pytest.fixture(scope="session")
def mock_api_server_preflight_401() -> Generator[MockAPIServer, None, None]:
    """Provide an isolated mock API server that returns 401 for all auth endpoints.

    Used by pre-flight failure scenarios where the auth check must fail while
    Robot Framework tests continue running. Kept separate from the shared
    mock_api_server so no mutation of the session-wide server is needed.
    """
    yield from _start_mock_server(MOCK_API_CONFIG_PREFLIGHT_401_PATH)


# =============================================================================
# Shared test fixtures (used by unit, integration, and e2e tests)
# =============================================================================


@pytest.fixture()
def socket_dir() -> Generator[Path, None, None]:
    """Short-path temp dir suitable for Unix socket paths (macOS 104-char limit)."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# =============================================================================
# ControllerContext fixtures
# =============================================================================


@pytest.fixture()
def aci_context() -> ControllerContext:
    """Pre-built ControllerContext for ACI with session auth."""
    return ControllerContext(controller_type="ACI", auth_method=AuthMethod.SESSION)


@pytest.fixture()
def sdwan_context() -> ControllerContext:
    """Pre-built ControllerContext for SDWAN with session auth."""
    return ControllerContext(controller_type="SDWAN", auth_method=AuthMethod.SESSION)


@pytest.fixture()
def cc_context() -> ControllerContext:
    """Pre-built ControllerContext for Catalyst Center with session auth."""
    return ControllerContext(controller_type="CC", auth_method=AuthMethod.SESSION)


@pytest.fixture()
def iosxe_context() -> ControllerContext:
    """Pre-built ControllerContext for IOS-XE with session auth."""
    return ControllerContext(controller_type="IOSXE", auth_method=AuthMethod.SESSION)


@pytest.fixture()
def pyats_test_dirs(tmp_path: Path) -> PyATSTestDirs:
    """Create standard directory structure for PyATS orchestrator tests.

    Returns:
        PyATSTestDirs with test_dir, output_dir, and merged_file paths.
    """
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    merged_file = output_dir / "merged.json"
    merged_file.write_text('{"test": "data"}')
    return PyATSTestDirs(
        test_dir=test_dir, output_dir=output_dir, merged_file=merged_file
    )
