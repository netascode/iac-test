# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Pytest fixtures for E2E tests.

This module provides E2E-specific fixtures:
- E2EResults dataclass for capturing test run results
- Scenario execution helper and individual scenario fixtures
- Unified user testbed for D2D tests

Common fixtures (mock_api_server, etc.) are inherited
from the global tests/conftest.py.
"""

import os
import subprocess
import tempfile
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
import ruamel.yaml

from tests.e2e.config import (
    ALL_FAIL_SCENARIO,
    DRY_RUN_PYATS_ONLY_SCENARIO,
    DRY_RUN_ROBOT_FAIL_SCENARIO,
    DRY_RUN_SCENARIO,
    MIXED_SCENARIO,
    PREFLIGHT_AUTH_FAILURE_SCENARIO,
    PYATS_API_ONLY_SCENARIO,
    PYATS_CC_SCENARIO,
    PYATS_D2D_ONLY_SCENARIO,
    PYATS_NXOS_D2D_SCENARIO,
    ROBOT_ONLY_SCENARIO,
    SUCCESS_SCENARIO,
    VERBOSE_SCENARIO,
    VERBOSE_WITH_INFO_SCENARIO,
    WINDOWS_PYATS_SKIP_SCENARIO,
    E2EScenario,
)
from tests.e2e.mocks.mock_server import MockAPIServer

# Sentinel value for credential exposure detection (#689)
# All test passwords use this value so we can detect if credentials leak into artifacts
TEST_CREDENTIAL_SENTINEL: str = "CRED_SENTINEL_MUST_NOT_APPEAR_IN_ARTIFACTS"

# Mock device definitions for D2D testing
MOCK_DEVICES: dict[str, dict[str, str]] = {
    "sd-dc-c8kv-01": {"os": "iosxe", "type": "router"},
    "sd-dc-c8kv-02": {"os": "iosxe", "type": "router"},
    "nxos-switch-01": {"os": "nxos", "type": "switch"},
}

CONTROLLER_ARCHITECTURES = {"SDWAN", "ACI", "CC", "ISE", "FMC"}
D2D_ARCHITECTURES = {"NXOS", "IOSXE"}


@dataclass
class E2EResults:
    """Results from an E2E test run.

    Attributes:
        scenario: The E2E scenario that was executed.
        output_dir: Path to the output directory containing all reports.
        exit_code: CLI exit code.
        stdout: CLI standard output.
        stderr: CLI standard error.
        filtered_stdout: Stdout with logger lines (INFO -, DEBUG -, etc.) removed.
        cli_result: The full CliRunner result object.
    """

    scenario: E2EScenario
    output_dir: Path
    exit_code: int
    stdout: str
    stderr: str
    filtered_stdout: str
    cli_result: subprocess.CompletedProcess[str]

    @property
    def has_robot_results(self) -> bool:
        """Robot ran and produced output files (output.xml, log.html, etc.)."""
        return self.scenario.has_robot_tests

    @property
    def robot_dir_exists(self) -> bool:
        """Pabot was invoked; robot_results/ directory exists on disk.

        True whenever the fixture contains .robot files, even if tag filters
        matched zero tests and no output files were produced inside the directory.
        """
        return self.scenario.robot_invoked

    @property
    def has_pyats_api_results(self) -> bool:
        """PyATS API results exist only when not in dry-run mode."""
        return self.scenario.has_pyats_api_tests and not self.scenario.is_dry_run

    @property
    def has_pyats_d2d_results(self) -> bool:
        """PyATS D2D results exist only when not in dry-run mode."""
        return self.scenario.has_pyats_d2d_tests and not self.scenario.is_dry_run

    @property
    def has_pyats_results(self) -> bool:
        """Any PyATS results exist."""
        return self.has_pyats_api_results or self.has_pyats_d2d_results


# =============================================================================
# Session-scoped fixtures
# =============================================================================


@pytest.fixture(scope="session")
def user_testbed() -> Generator[str, None, None]:
    """Create a unified user testbed YAML with mock device connections for D2D tests.

    This fixture creates a temporary testbed file that configures mock device
    connections using the mock_unicon.py script for all supported mock devices.

    Returns:
        Path string to the testbed YAML file.
    """
    project_root = Path(__file__).parent.parent.parent.absolute()
    mock_script = project_root / "tests" / "e2e" / "mocks" / "mock_unicon.py"

    devices = {
        name: {
            "os": info["os"],
            "type": info["type"],
            "connections": {
                "cli": {
                    "command": f"python {mock_script} {info['os']} --hostname {name}"
                }
            },
        }
        for name, info in MOCK_DEVICES.items()
    }

    testbed_data = {
        "testbed": {
            "name": "e2e_mock_testbed",
            "credentials": {
                "default": {
                    "username": "admin",
                    "password": "admin",
                }
            },
        },
        "devices": devices,
    }

    yaml = ruamel.yaml.YAML()
    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_testbed.yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.dump(testbed_data, f)
        testbed_path = Path(f.name)

    try:
        yield str(testbed_path)
    finally:
        if testbed_path.exists():
            testbed_path.unlink()


# =============================================================================
# E2E scenario execution
# =============================================================================


def _run_e2e_scenario(
    scenario: E2EScenario,
    mock_api_server: MockAPIServer | None,
    user_testbed: str | None,
    tmp_path_factory: pytest.TempPathFactory,
    output_path_relative: bool = False,
    extra_cli_args: list[str] | None = None,
    extra_env_vars: dict[str, str] | None = None,
) -> E2EResults:
    """Execute an E2E scenario and return results.

    This is the core execution logic shared by all scenarios.

    Args:
        scenario: The scenario configuration to execute.
        mock_api_server: The mock API server instance (can be None, for example for dry-run scenarios).
        user_testbed: Path to the testbed YAML (None if not required).
        tmp_path_factory: Pytest temp path factory.
        output_path_relative: Whether to pass a cwd-relative output path to the CLI.
        extra_cli_args: Additional CLI arguments to pass (e.g., ["--dry-run", "--verbose"]).
        extra_env_vars: Additional environment variables to set (e.g., {"NAC_TEST_DEBUG": "true"}).

    Returns:
        E2EResults containing all execution results.

    Raises:
        ValueError: If scenario configuration is invalid (via scenario.validate()).
    """
    # Validate scenario configuration before execution
    scenario.validate()

    # Create scenario-specific temp directory
    output_dir = tmp_path_factory.mktemp(f"e2e_{scenario.name}")
    output_arg = str(output_dir)

    if output_path_relative:
        # pathlib-only alternatives are more convoluted here, while relpath
        # directly expresses the cross-directory relative path we need.
        output_arg = os.path.relpath(output_dir, Path.cwd())

    arch = scenario.architecture
    if arch not in CONTROLLER_ARCHITECTURES and arch not in D2D_ARCHITECTURES:
        raise ValueError(
            f"Scenario '{scenario.name}' has unknown architecture '{arch}'. "
            f"Must be one of {CONTROLLER_ARCHITECTURES | D2D_ARCHITECTURES}"
        )

    # Build environment: inherit current process env, then layer scenario-specific vars.
    # subprocess.run() receives this dict directly — no monkeypatching needed.
    env: dict[str, str] = {**os.environ}
    if arch in CONTROLLER_ARCHITECTURES:
        if mock_api_server:
            env[f"{arch}_URL"] = mock_api_server.url
        else:
            env[f"{arch}_URL"] = "http://dry-run.invalid"
        env[f"{arch}_USERNAME"] = "mock_user"
        env[f"{arch}_PASSWORD"] = TEST_CREDENTIAL_SENTINEL
    elif arch in D2D_ARCHITECTURES:
        env[f"{arch}_HOST"] = "127.0.0.1"
        env[f"{arch}_USERNAME"] = "mock_user"
        env[f"{arch}_PASSWORD"] = TEST_CREDENTIAL_SENTINEL

    # Secondary device credentials for D2D tests targeting devices with an OS distinct from the controller
    if scenario.expected_d2d_hostnames:
        for hostname in scenario.expected_d2d_hostnames:
            dev_os = MOCK_DEVICES.get(hostname, {}).get("os", "").upper()
            if dev_os and dev_os != arch:
                env[f"{dev_os}_USERNAME"] = "mock_user"
                env[f"{dev_os}_PASSWORD"] = TEST_CREDENTIAL_SENTINEL

    if extra_env_vars:
        env.update(extra_env_vars)

    cli_args = [
        "-d",
        scenario.data_path,
        "-t",
        scenario.templates_path,
        "-o",
        output_arg,
    ]

    if scenario.requires_testbed and user_testbed:
        cli_args.extend(["--testbed", user_testbed])

    # Add extra CLI arguments (e.g., --dry-run, --verbose)
    if extra_cli_args:
        cli_args.extend(extra_cli_args)

    # Execute via subprocess: each scenario gets a fresh interpreter with no shared state.
    result = subprocess.run(
        ["nac-test"] + cli_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    # Compute filtered stdout (strips logger output lines)
    filtered_stdout = "\n".join(
        line
        for line in result.stdout.split("\n")
        if not line.startswith(("INFO -", "DEBUG -", "WARNING -", "ERROR -"))
    )

    return E2EResults(
        scenario=scenario,
        output_dir=output_dir,
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        filtered_stdout=filtered_stdout,
        cli_result=result,
    )


# =============================================================================
# Individual scenario fixtures (class-scoped for caching)
# =============================================================================


@pytest.fixture(scope="class")
def e2e_success_results(
    mock_api_server: MockAPIServer,
    user_testbed: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the success scenario once and cache results for the class."""
    return _run_e2e_scenario(
        SUCCESS_SCENARIO,
        mock_api_server,
        user_testbed,
        tmp_path_factory,
    )


@pytest.fixture(scope="class")
def e2e_failure_results(
    mock_api_server: MockAPIServer,
    user_testbed: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the all-fail scenario once and cache results for the class."""
    return _run_e2e_scenario(
        ALL_FAIL_SCENARIO,
        mock_api_server,
        user_testbed,
        tmp_path_factory,
    )


@pytest.fixture(scope="class")
def e2e_mixed_results(
    mock_api_server: MockAPIServer,
    user_testbed: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the mixed scenario once and cache results for the class."""
    return _run_e2e_scenario(
        MIXED_SCENARIO,
        mock_api_server,
        user_testbed,
        tmp_path_factory,
    )


@pytest.fixture(scope="class")
def e2e_mixed_relative_output_results(
    mock_api_server: MockAPIServer,
    user_testbed: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the mixed scenario (same as above) with a relative output path."""
    return _run_e2e_scenario(
        MIXED_SCENARIO,
        mock_api_server,
        user_testbed,
        tmp_path_factory,
        output_path_relative=True,
    )


@pytest.fixture(scope="class")
def e2e_robot_only_results(
    mock_api_server: MockAPIServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the robot-only scenario once and cache results for the class.

    Note: This scenario does not require a testbed (no D2D tests).
    """
    return _run_e2e_scenario(
        ROBOT_ONLY_SCENARIO,
        mock_api_server,
        None,  # No testbed needed
        tmp_path_factory,
    )


@pytest.fixture(scope="class")
def e2e_pyats_api_only_results(
    mock_api_server: MockAPIServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the PyATS API-only scenario once and cache results for the class.

    Note: This scenario does not require a testbed (no D2D tests).
    """
    return _run_e2e_scenario(
        PYATS_API_ONLY_SCENARIO,
        mock_api_server,
        None,  # No testbed needed
        tmp_path_factory,
    )


@pytest.fixture(scope="class")
def e2e_pyats_d2d_only_results(
    mock_api_server: MockAPIServer,
    user_testbed: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the PyATS D2D-only scenario once and cache results for the class."""
    return _run_e2e_scenario(
        PYATS_D2D_ONLY_SCENARIO,
        mock_api_server,
        user_testbed,
        tmp_path_factory,
    )


@pytest.fixture(scope="class")
def e2e_pyats_cc_results(
    mock_api_server: MockAPIServer,
    user_testbed: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the PyATS Catalyst Center (API + D2D) scenario once and cache results."""
    return _run_e2e_scenario(
        PYATS_CC_SCENARIO,
        mock_api_server,
        user_testbed,
        tmp_path_factory,
    )


@pytest.fixture(scope="class")
def e2e_verbose_results(
    mock_api_server: MockAPIServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the verbose scenario with --verbose flag and cache results."""
    return _run_e2e_scenario(
        VERBOSE_SCENARIO,
        mock_api_server,
        None,
        tmp_path_factory,
        extra_cli_args=["--verbose"],
        extra_env_vars={"EXPECTED_ROBOT_LOG_LEVEL": "DEBUG"},
    )


@pytest.fixture(scope="class")
def e2e_verbose_with_info_results(
    mock_api_server: MockAPIServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the verbose scenario with --verbose --loglevel INFO flags."""
    return _run_e2e_scenario(
        VERBOSE_WITH_INFO_SCENARIO,
        mock_api_server,
        None,
        tmp_path_factory,
        extra_cli_args=["--verbose", "--loglevel", "INFO"],
        extra_env_vars={"EXPECTED_ROBOT_LOG_LEVEL": "INFO"},
    )


@pytest.fixture(scope="class")
def e2e_dry_run_results(
    user_testbed: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the dry-run scenario (mixed fixtures with --dry-run flag)."""
    return _run_e2e_scenario(
        DRY_RUN_SCENARIO,
        None,
        user_testbed,
        tmp_path_factory,
        extra_cli_args=["--dry-run"],
    )


@pytest.fixture(scope="class")
def e2e_dry_run_pyats_only_results(
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute dry-run with PyATS-only (no Robot tests)."""
    return _run_e2e_scenario(
        DRY_RUN_PYATS_ONLY_SCENARIO,
        None,
        None,
        tmp_path_factory,
        extra_cli_args=["--dry-run"],
    )


@pytest.fixture(scope="class")
def e2e_dry_run_robot_fail_results(
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute dry-run with Robot test that has non-existent keyword (fails dryrun)."""
    return _run_e2e_scenario(
        DRY_RUN_ROBOT_FAIL_SCENARIO,
        None,
        None,
        tmp_path_factory,
        extra_cli_args=["--dry-run"],
    )


@pytest.fixture(scope="class")
def e2e_windows_pyats_skip_results(
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the Windows PyATS skip scenario once and cache results."""
    return _run_e2e_scenario(
        WINDOWS_PYATS_SKIP_SCENARIO,
        None,
        None,
        tmp_path_factory,
    )


@pytest.fixture(scope="class")
def e2e_preflight_auth_failure_results(
    mock_api_server_preflight_401: MockAPIServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Pre-flight auth failure (401): Robot still runs, combined_summary shows failure report.

    Uses a dedicated mock server loaded from mock_api_config_preflight_401.yaml
    that returns 401 for all auth endpoints. This keeps the shared mock_api_server
    untouched and avoids any endpoint mutation.
    """
    return _run_e2e_scenario(
        PREFLIGHT_AUTH_FAILURE_SCENARIO,
        mock_api_server_preflight_401,
        None,
        tmp_path_factory,
    )


@pytest.fixture(scope="class")
def e2e_tag_filter_include_results(
    mock_api_server: MockAPIServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    from tests.e2e.config import TAG_FILTER_INCLUDE_SCENARIO

    return _run_e2e_scenario(
        TAG_FILTER_INCLUDE_SCENARIO,
        mock_api_server,
        None,
        tmp_path_factory,
        extra_cli_args=["--include", "bgp"],
    )


@pytest.fixture(scope="class")
def e2e_tag_filter_exclude_results(
    mock_api_server: MockAPIServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    from tests.e2e.config import TAG_FILTER_EXCLUDE_SCENARIO

    return _run_e2e_scenario(
        TAG_FILTER_EXCLUDE_SCENARIO,
        mock_api_server,
        None,
        tmp_path_factory,
        extra_cli_args=["--exclude", "osp*"],
    )


@pytest.fixture(scope="class")
def e2e_tag_filter_combined_results(
    mock_api_server: MockAPIServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    from tests.e2e.config import TAG_FILTER_COMBINED_SCENARIO

    return _run_e2e_scenario(
        TAG_FILTER_COMBINED_SCENARIO,
        mock_api_server,
        None,
        tmp_path_factory,
        extra_cli_args=["--include", "api-only"],
    )


@pytest.fixture(scope="class")
def e2e_tag_filter_no_match_results(
    mock_api_server: MockAPIServer,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    from tests.e2e.config import TAG_FILTER_NO_MATCH_SCENARIO

    return _run_e2e_scenario(
        TAG_FILTER_NO_MATCH_SCENARIO,
        mock_api_server,
        None,
        tmp_path_factory,
        extra_cli_args=["--exclude", "bgpORospf"],
    )


@pytest.fixture(scope="class")
def e2e_pyats_nxos_d2d_results(
    mock_api_server: MockAPIServer,
    user_testbed: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> E2EResults:
    """Execute the PyATS NX-OS D2D scenario once and cache results for the class."""
    return _run_e2e_scenario(
        PYATS_NXOS_D2D_SCENARIO,
        mock_api_server,
        user_testbed,
        tmp_path_factory,
    )
