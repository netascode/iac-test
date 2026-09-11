# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""E2E test scenario configurations.

This module defines the configuration dataclass for E2E test scenarios
and provides pre-configured scenarios for different test outcomes.
"""

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path


@dataclass(frozen=True)
class E2EScenario:
    """Configuration for an E2E test scenario.

    Attributes:
        name: Unique identifier for the scenario (used in test IDs).
        description: Human-readable description of what this scenario tests.
        data_path: Path to the data.yaml fixture file.
        templates_path: Path to the templates fixture directory.
        requires_testbed: Whether this scenario requires a --testbed argument (D2D tests).
        architecture: The network architecture (SDWAN, ACI, CC) - determines env vars.

        expected_exit_code: Expected CLI exit code (0=success, 1=failure).
        expected_robot_passed: Expected number of passed Robot tests.
        expected_robot_failed: Expected number of failed Robot tests.
        expected_robot_skipped: Expected number of skipped Robot tests.

        PyATS has two test types with separate reports:
        - API tests: REST API calls to controllers (SDWANManagerTestBase, APICTestBase, etc.)
        - D2D tests: Device-to-device SSH tests (IOSXETestBase)

        expected_pyats_api_passed: Expected number of passed PyATS API tests.
        expected_pyats_api_failed: Expected number of failed PyATS API tests.
        expected_pyats_api_skipped: Expected number of skipped PyATS API tests.
        expected_pyats_d2d_passed: Expected number of passed PyATS D2D tests.
        expected_pyats_d2d_failed: Expected number of failed PyATS D2D tests.
        expected_pyats_d2d_skipped: Expected number of skipped PyATS D2D tests.

        expected_preflight_failure: True when a pre-flight failure is expected. The
            pyats_results/ directory is created for pre_flight_failure.html even when
            no PyATS tests ran, so the root-level whitelist test must account for it.
    """

    name: str
    description: str
    data_path: str
    templates_path: str
    requires_testbed: bool = True  # D2D tests require testbed, Robot/API-only don't
    architecture: str = (
        ""  # Controller type: "SDWAN", "ACI", "CC" - determines env var prefix
    )
    is_dry_run: bool = False  # Dry-run mode: validates structure without execution

    # Expected CLI behavior
    expected_exit_code: int = 0

    # Expected Robot Framework results
    expected_robot_passed: int = 0
    expected_robot_failed: int = 0
    expected_robot_skipped: int = 0

    # Expected PyATS API results (REST API to SD-WAN Manager)
    expected_pyats_api_passed: int = 0
    expected_pyats_api_failed: int = 0
    expected_pyats_api_skipped: int = 0

    # Expected PyATS D2D results (SSH to devices)
    expected_pyats_d2d_passed: int = 0
    expected_pyats_d2d_failed: int = 0
    expected_pyats_d2d_skipped: int = 0

    # Expected device hostnames for D2D tests (for hostname display validation)
    expected_d2d_hostnames: list[str] | None = None

    # True when a pre-flight failure is expected (pyats_results/ created for the report)
    expected_preflight_failure: bool = False

    @property
    def expected_robot_total(self) -> int:
        """Total number of Robot tests."""
        return (
            self.expected_robot_passed
            + self.expected_robot_failed
            + self.expected_robot_skipped
        )

    @property
    def expected_pyats_api_total(self) -> int:
        """Total number of PyATS API tests."""
        return (
            self.expected_pyats_api_passed
            + self.expected_pyats_api_failed
            + self.expected_pyats_api_skipped
        )

    @property
    def expected_pyats_d2d_total(self) -> int:
        """Total number of PyATS D2D tests."""
        return (
            self.expected_pyats_d2d_passed
            + self.expected_pyats_d2d_failed
            + self.expected_pyats_d2d_skipped
        )

    @property
    def expected_pyats_total(self) -> int:
        """Total number of all PyATS tests (API + D2D)."""
        return self.expected_pyats_api_total + self.expected_pyats_d2d_total

    @property
    def expected_pyats_passed(self) -> int:
        """Total number of passed PyATS tests (API + D2D)."""
        return self.expected_pyats_api_passed + self.expected_pyats_d2d_passed

    @property
    def expected_pyats_failed(self) -> int:
        """Total number of failed PyATS tests (API + D2D)."""
        return self.expected_pyats_api_failed + self.expected_pyats_d2d_failed

    @property
    def expected_pyats_skipped(self) -> int:
        """Total number of skipped PyATS tests (API + D2D)."""
        return self.expected_pyats_api_skipped + self.expected_pyats_d2d_skipped

    @property
    def expected_total_tests(self) -> int:
        """Total number of all tests."""
        return self.expected_robot_total + self.expected_pyats_total

    @property
    def expected_total_passed(self) -> int:
        """Total number of passed tests (Robot + PyATS)."""
        return self.expected_robot_passed + self.expected_pyats_passed

    @property
    def expected_total_failed(self) -> int:
        """Total number of failed tests (Robot + PyATS)."""
        return self.expected_robot_failed + self.expected_pyats_failed

    @property
    def expected_total_skipped(self) -> int:
        """Total number of skipped tests (Robot + PyATS)."""
        return self.expected_robot_skipped + self.expected_pyats_skipped

    @property
    def expects_success(self) -> bool:
        """True if this scenario expects all tests to pass."""
        return self.expected_exit_code == 0

    @property
    def expects_any_failures(self) -> bool:
        """True if this scenario expects any test failures."""
        return self.expected_total_failed > 0

    @property
    def has_robot_tests(self) -> bool:
        """True if this scenario includes Robot Framework tests."""
        return self.expected_robot_total > 0

    @cached_property
    def robot_invoked(self) -> bool:
        """True if pabot was invoked, regardless of tag filter results.

        Pabot always creates robot_results/ when the fixture contains .robot
        files, even when tag filters produce zero matching tests. This differs
        from has_robot_tests, which reflects whether tests actually ran.
        """
        return any(Path(self.templates_path).rglob("*.robot"))

    @property
    def has_pyats_api_tests(self) -> bool:
        """True if this scenario includes PyATS API tests."""
        return self.expected_pyats_api_total > 0

    @property
    def has_pyats_d2d_tests(self) -> bool:
        """True if this scenario includes PyATS D2D tests."""
        return self.expected_pyats_d2d_total > 0

    @property
    def has_pyats_tests(self) -> bool:
        """True if this scenario includes any PyATS tests."""
        return self.expected_pyats_total > 0

    def validate(self) -> None:
        """Validate scenario configuration consistency.
        Only check for basic consistency which is relevant to test execution,
        not the expected results (which are scenario-specific).

        Raises:
            ValueError: If configuration is inconsistent.
        """
        # D2D tests require expected_d2d_hostnames to be defined
        if self.has_pyats_d2d_tests and not self.expected_d2d_hostnames:
            raise ValueError(
                f"Scenario '{self.name}' has D2D tests ({self.expected_pyats_d2d_total} total) "
                "but expected_d2d_hostnames is not defined"
            )

        # Paths should exist (relative to project root)
        data_file = Path(self.data_path)
        templates_dir = Path(self.templates_path)
        if not data_file.exists():
            raise ValueError(
                f"Scenario '{self.name}' data_path does not exist: {self.data_path}"
            )
        if not templates_dir.exists():
            raise ValueError(
                f"Scenario '{self.name}' templates_path does not exist: {self.templates_path}"
            )


# =============================================================================
# Pre-configured E2E Scenarios
# =============================================================================

# Fixture base path (relative to project root)
_FIXTURE_BASE = "tests/e2e/fixtures"


SUCCESS_SCENARIO = E2EScenario(
    name="success",
    description="All tests pass - Robot (1 pass) + PyATS API (1 pass) + PyATS D2D (1 pass)",
    data_path=f"{_FIXTURE_BASE}/success/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/success/templates",
    architecture="SDWAN",
    expected_exit_code=0,
    expected_robot_passed=1,
    expected_robot_failed=0,
    # PyATS API: verify_sdwan_sync.py (SDWANManagerTestBase) - 1 pass
    expected_pyats_api_passed=1,
    expected_pyats_api_failed=0,
    # PyATS D2D: verify_iosxe_control.py (IOSXETestBase) - 1 pass
    expected_pyats_d2d_passed=1,
    expected_pyats_d2d_failed=0,
    expected_d2d_hostnames=["sd-dc-c8kv-01"],
)

ALL_FAIL_SCENARIO = E2EScenario(
    name="all_fail",
    description="All tests fail - Robot (1 fail) + PyATS API (1 fail) + PyATS D2D (1 fail)",
    data_path=f"{_FIXTURE_BASE}/failure/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/failure/templates",
    architecture="SDWAN",
    expected_exit_code=3,  # 3 total failures (graduated exit code)
    expected_robot_passed=0,
    expected_robot_failed=1,
    # PyATS API: verify_sdwan_sync_fail.py (SDWANManagerTestBase) - 1 fail
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=1,
    # PyATS D2D: verify_iosxe_control_fail.py (IOSXETestBase) - 1 fail
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=1,
    expected_d2d_hostnames=["sd-dc-c8kv-01"],
)

MIXED_SCENARIO = E2EScenario(
    name="mixed",
    description="Mixed results - Robot (1 pass, 1 fail) + PyATS API (0 pass, 1 fail) + PyATS D2D (1 pass, 0 fail)",
    data_path=f"{_FIXTURE_BASE}/mixed/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/mixed/templates",
    architecture="SDWAN",
    expected_exit_code=2,  # 2 total failures (graduated exit code)
    expected_robot_passed=1,
    expected_robot_failed=1,
    # PyATS API: verify_sdwan_sync_fail.py (SDWANManagerTestBase) - 1 fail
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=1,
    # PyATS D2D: verify_iosxe_control.py (IOSXETestBase) - 1 pass
    expected_pyats_d2d_passed=1,
    expected_pyats_d2d_failed=0,
    expected_d2d_hostnames=["sd-dc-c8kv-01"],
)

ROBOT_ONLY_SCENARIO = E2EScenario(
    name="robot_only",
    description="Robot Framework only - 1 passing test, no PyATS tests",
    data_path=f"{_FIXTURE_BASE}/robot_only/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/robot_only/templates",
    requires_testbed=False,  # No D2D tests, no testbed needed
    architecture="SDWAN",
    expected_exit_code=0,
    expected_robot_passed=1,
    expected_robot_failed=0,
    # No PyATS tests
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
)

PYATS_API_ONLY_SCENARIO = E2EScenario(
    name="pyats_api_only",
    description="PyATS API only (ACI) - 1 passing test, no Robot or D2D tests",
    data_path=f"{_FIXTURE_BASE}/pyats_api_only/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/pyats_api_only/templates",
    requires_testbed=False,  # No D2D tests, no testbed needed
    architecture="ACI",  # Uses APIC API
    expected_exit_code=0,
    # No Robot tests
    expected_robot_passed=0,
    expected_robot_failed=0,
    # PyATS API: 1 test — the file is a symlink pointing outside templates/ to
    # verify_aci_apic_appliance_operational_status.py in the fixture root.
    # The symlink exists specifically to verify that test name computation uses
    # absolute() not resolve() — see issue #656.
    expected_pyats_api_passed=1,
    expected_pyats_api_failed=0,
    # No D2D tests
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
)

PYATS_D2D_ONLY_SCENARIO = E2EScenario(
    name="pyats_d2d_only",
    description="PyATS D2D only - 2 passing tests, no Robot or API tests",
    data_path=f"{_FIXTURE_BASE}/pyats_d2d_only/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/pyats_d2d_only/templates",
    requires_testbed=True,  # D2D tests require testbed
    architecture="SDWAN",
    expected_exit_code=0,
    # No Robot tests
    expected_robot_passed=0,
    expected_robot_failed=0,
    # No API tests
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=0,
    # PyATS D2D: verify_iosxe_control.py (1 pass) + verify_iosxe_ospf_te_links.py (1 pass)
    # The OSPF TE test requires Genie's supplementary device.execute() calls
    # to be routed through the broker (fix for issue #663).
    expected_pyats_d2d_passed=2,
    expected_pyats_d2d_failed=0,
    expected_d2d_hostnames=["sd-dc-c8kv-01"],
)

PYATS_CC_SCENARIO = E2EScenario(
    name="pyats_cc",
    description="PyATS Catalyst Center - API (1 pass) + D2D (2 pass), no Robot tests",
    data_path=f"{_FIXTURE_BASE}/pyats_cc/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/pyats_cc/templates",
    requires_testbed=True,  # D2D tests require testbed
    architecture="CC",  # Uses Catalyst Center API
    expected_exit_code=0,
    # No Robot tests
    expected_robot_passed=0,
    expected_robot_failed=0,
    # PyATS API: verify_dnac_catalyst_center_system_health_no_critical_events.py - 1 pass
    expected_pyats_api_passed=1,
    expected_pyats_api_failed=0,
    # PyATS D2D: verify_iosxe_no_critical_errors_in_system_logs.py - 2 pass (2 devices)
    expected_pyats_d2d_passed=2,
    expected_pyats_d2d_failed=0,
    expected_d2d_hostnames=["sd-dc-c8kv-01", "sd-dc-c8kv-02"],
)

VERBOSE_SCENARIO = E2EScenario(
    name="verbose",
    description="Verbose flag test - Robot + PyATS API, verifies --verbose enables DEBUG log level in nac-test and PyATS/Robot",
    data_path=f"{_FIXTURE_BASE}/verbose/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/verbose/templates",
    requires_testbed=False,
    architecture="ACI",
    expected_exit_code=0,
    expected_robot_passed=1,
    expected_robot_failed=0,
    expected_pyats_api_passed=1,
    expected_pyats_api_failed=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
)

VERBOSE_WITH_INFO_SCENARIO = E2EScenario(
    name="verbose_with_info",
    description="Verbose with INFO loglevel - verifies --verbose --loglevel INFO filters PyATS DEBUG output",
    data_path=f"{_FIXTURE_BASE}/verbose/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/verbose/templates",
    requires_testbed=False,
    architecture="ACI",
    expected_exit_code=0,
    expected_robot_passed=1,
    expected_robot_failed=0,
    expected_pyats_api_passed=1,
    expected_pyats_api_failed=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
)

DRY_RUN_SCENARIO = E2EScenario(
    name="dry_run_robot_pyats",
    description="Dry-run mode - Robot (2 validated) + PyATS (discovered, not executed)",
    data_path=f"{_FIXTURE_BASE}/mixed/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/mixed/templates",
    requires_testbed=True,
    architecture="SDWAN",
    is_dry_run=True,
    expected_exit_code=0,
    expected_robot_passed=2,
    expected_robot_failed=0,
    expected_robot_skipped=0,
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=0,
    expected_pyats_api_skipped=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
    expected_pyats_d2d_skipped=0,
)

DRY_RUN_PYATS_ONLY_SCENARIO = E2EScenario(
    name="dry_run_pyats_only",
    description="Dry-run mode - PyATS API only (no Robot tests)",
    data_path=f"{_FIXTURE_BASE}/pyats_api_only/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/pyats_api_only/templates",
    requires_testbed=False,
    architecture="ACI",
    is_dry_run=True,
    expected_exit_code=0,
    expected_robot_passed=0,
    expected_robot_failed=0,
    expected_robot_skipped=0,
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=0,
    expected_pyats_api_skipped=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
    expected_pyats_d2d_skipped=0,
)

DRY_RUN_ROBOT_FAIL_SCENARIO = E2EScenario(
    name="dry_run_robot_fail",
    description="Dry-run mode - Robot only with non-existent keyword (1 fail)",
    data_path=f"{_FIXTURE_BASE}/dry_run_robot_fail/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/dry_run_robot_fail/templates",
    requires_testbed=False,
    architecture="SDWAN",
    is_dry_run=True,
    expected_exit_code=1,
    expected_robot_passed=0,
    expected_robot_failed=1,
    expected_robot_skipped=0,
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=0,
    expected_pyats_api_skipped=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
    expected_pyats_d2d_skipped=0,
)

WINDOWS_PYATS_SKIP_SCENARIO = E2EScenario(
    name="windows_pyats_skip",
    description="Windows PyATS skip - Robot (1 pass), PyATS discovered but skipped on Windows",
    data_path=f"{_FIXTURE_BASE}/windows_pyats_skip/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/windows_pyats_skip/templates",
    requires_testbed=False,  # No D2D tests actually run
    architecture="SDWAN",
    expected_exit_code=0,
    # Robot tests run normally
    expected_robot_passed=1,
    expected_robot_failed=0,
    # All PyATS expectations are 0 (skipped on Windows)
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
)

PREFLIGHT_AUTH_FAILURE_SCENARIO = E2EScenario(
    name="preflight_auth_failure",
    description=(
        "Auth failure (401): ACI pre-flight check fails but Robot still runs, "
        "combined_summary shows normal dashboard with pre-flight banner"
    ),
    data_path=f"{_FIXTURE_BASE}/preflight_failure/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/preflight_failure/templates",
    requires_testbed=False,
    architecture="ACI",
    expected_exit_code=1,  # CombinedResults.exit_code returns 1 on pre-flight failure
    expected_preflight_failure=True,
    expected_robot_passed=1,
    expected_robot_failed=0,
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
)

TAG_FILTER_INCLUDE_SCENARIO = E2EScenario(
    name="tag_filter_include",
    description="--include bgp: filters to 1 Robot + 1 PyATS (both pass)",
    data_path=f"{_FIXTURE_BASE}/tag_filtering/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/tag_filtering/templates",
    requires_testbed=False,
    architecture="ACI",
    expected_exit_code=0,
    expected_robot_passed=1,
    expected_robot_failed=0,
    expected_robot_skipped=0,
    # PyATS API: verify_bgp_api.py (groups=["bgp"]) - 1 pass
    expected_pyats_api_passed=1,
    expected_pyats_api_failed=0,
    expected_pyats_api_skipped=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
    expected_pyats_d2d_skipped=0,
)

TAG_FILTER_EXCLUDE_SCENARIO = E2EScenario(
    name="tag_filter_exclude",
    description="--exclude ospf: filters to 1 Robot + 1 PyATS (both pass)",
    data_path=f"{_FIXTURE_BASE}/tag_filtering/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/tag_filtering/templates",
    requires_testbed=False,
    architecture="ACI",
    expected_exit_code=0,
    expected_robot_passed=1,
    expected_robot_failed=0,
    expected_robot_skipped=0,
    # PyATS API: verify_bgp_api.py (groups=["bgp"]) - 1 pass (ospf excluded)
    expected_pyats_api_passed=1,
    expected_pyats_api_failed=0,
    expected_pyats_api_skipped=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
    expected_pyats_d2d_skipped=0,
)

TAG_FILTER_COMBINED_SCENARIO = E2EScenario(
    name="tag_filter_combined",
    description="--include api-only: 0 Robot (no match) + 1 PyATS (verify_ospf_api.py) → exit 0",
    data_path=f"{_FIXTURE_BASE}/tag_filtering/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/tag_filtering/templates",
    requires_testbed=False,
    architecture="ACI",
    expected_exit_code=0,
    expected_robot_passed=0,
    expected_robot_failed=0,
    expected_robot_skipped=0,
    # PyATS API: verify_ospf_api.py (groups=["ospf", "api-only"]) - 1 pass
    expected_pyats_api_passed=1,
    expected_pyats_api_failed=0,
    expected_pyats_api_skipped=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
    expected_pyats_d2d_skipped=0,
)

TAG_FILTER_NO_MATCH_SCENARIO = E2EScenario(
    name="tag_filter_no_match",
    description="--exclude bgpORospf filters out all tests: 0 Robot + 0 PyATS → exit 252",
    data_path=f"{_FIXTURE_BASE}/tag_filtering/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/tag_filtering/templates",
    requires_testbed=False,
    architecture="ACI",
    expected_exit_code=252,
    expected_robot_passed=0,
    expected_robot_failed=0,
    expected_robot_skipped=0,
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=0,
    expected_pyats_api_skipped=0,
    expected_pyats_d2d_passed=0,
    expected_pyats_d2d_failed=0,
    expected_pyats_d2d_skipped=0,
)

PYATS_NXOS_D2D_SCENARIO = E2EScenario(
    name="pyats_nxos_d2d",
    description="PyATS NX-OS D2D - SSH to NX-OS device, no Robot or API tests",
    data_path=f"{_FIXTURE_BASE}/pyats_nxos_d2d/data.yaml",
    templates_path=f"{_FIXTURE_BASE}/pyats_nxos_d2d/templates",
    requires_testbed=True,
    architecture="NXOS",
    expected_exit_code=0,
    expected_robot_passed=0,
    expected_robot_failed=0,
    expected_pyats_api_passed=0,
    expected_pyats_api_failed=0,
    expected_pyats_d2d_passed=1,
    expected_pyats_d2d_failed=0,
    expected_d2d_hostnames=["nxos-switch-01"],
)
