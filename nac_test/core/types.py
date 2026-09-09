# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Core types for nac-test orchestration."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, get_args

from nac_test.core.constants import (
    EXIT_DATA_ERROR,
    EXIT_ERROR,
    EXIT_FAILURE_CAP,
    EXIT_INTERRUPTED,
    EXIT_PREFLIGHT_FAILURE,
)

# Type alias for supported controller type keys.
# Matches the keys of CONTROLLER_REGISTRY in nac_test.core.controller.
ControllerTypeKey = Literal[
    "ACI", "SDWAN", "CC", "MERAKI", "FMC", "ISE", "IOSXE", "NXOS"
]

# Type alias for the semantic kind of a credential value.
# Matches the keys used in CredentialSet.fields in nac_test.core.controller.
CredentialKind = Literal["url", "username", "password", "token"]


class AuthMethod(str, Enum):
    """Authentication mechanism for a controller credential set.

    Consumed by auth adapters in nac-test-pyats-common to branch on
    token vs. session authentication.
    """

    SESSION = "session"
    TOKEN = "token"  # nosec B105 — not a password, enum value for auth method selection


@dataclass(frozen=True)
class ControllerContext:
    """Resolved controller selection identity passed from orchestrator to subprocess.

    Represents controller *identity* (type + auth method), **not** connection
    state.  Credentials are resolved separately from environment variables via
    ``get_connection_params()`` in ``nac_test.core.controller``.

    Produced once by ``resolve_controller()`` and threaded through the
    orchestration chain.  Serialized to ``NAC_TEST_CONTROLLER_CONTEXT``
    for subprocess transport (follows the existing ``DEVICE_INFO`` pattern).

    Attributes:
        controller_type: Detected controller key (e.g. ``"ACI"``, ``"SDWAN"``).
        auth_method: Authentication mechanism selected during resolution
            (e.g. ``"token"``, ``"session"``).  Consumed by auth adapters in
            *nac-test-pyats-common* to branch on token vs. session auth.
    """

    controller_type: ControllerTypeKey
    auth_method: AuthMethod

    def to_json(self) -> str:
        """Serialize for ``NAC_TEST_CONTROLLER_CONTEXT`` env var."""
        return json.dumps(
            {"controller_type": self.controller_type, "auth_method": self.auth_method}
        )

    @classmethod
    def from_json(cls, raw: str) -> "ControllerContext":
        """Deserialize from ``NAC_TEST_CONTROLLER_CONTEXT`` env var.

        Unknown keys are silently ignored so new fields can be added
        without breaking older consumers.

        Raises:
            ValueError: If the raw string is not valid JSON.
            KeyError: If required fields are missing.
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Invalid JSON in controller context: {e}") from e
        # Validate controller_type against known values
        ct = data["controller_type"]
        if ct not in get_args(ControllerTypeKey):
            raise ValueError(
                f"Unknown controller_type {ct!r}; "
                f"expected one of {get_args(ControllerTypeKey)}"
            )
        try:
            auth = AuthMethod(data["auth_method"])
        except ValueError as e:
            raise ValueError(f"Invalid auth_method {data['auth_method']!r}: {e}") from e
        return cls(
            controller_type=ct,
            auth_method=auth,
        )


@dataclass(frozen=True)
class ValidatedRobotArgs:
    """Pre-parsed, validated Robot Framework extra arguments.

    Produced once at the CLI boundary by validate_extra_args() and threaded
    through the orchestration chain. Consumers use .args to extend pabot's
    command line and .robot_opts to inspect parsed option values without
    re-parsing the raw string list.

    Attributes:
        args: Validated raw argument strings, ready to append to pabot's argv.
        robot_opts: Robot Framework options dict from pabot's parser (result[0]
            of pabot.arguments.parse_args). Keys are option names (e.g.
            "loglevel", "variable"); values are parsed values.
    """

    args: list[str]
    robot_opts: dict[str, Any]

    def __len__(self) -> int:
        """Number of validated argument strings.

        Drives bool() via Python's standard protocol: truthy when args are
        present, falsy when empty — robot_opts does not influence truthiness.
        """
        return len(self.args)


class PreFlightFailureType(str, Enum):
    """Type of pre-flight failure that prevented PyATS execution.

    Attributes:
        AUTH: Authentication failed (invalid credentials, 401/403).
        UNREACHABLE: Controller unreachable (network error, timeout, 5xx).
        DETECTION: Controller type could not be detected (no credentials set).
    """

    AUTH = "auth"
    UNREACHABLE = "unreachable"
    DETECTION = "detection"

    @property
    def display_name(self) -> str:
        """User-friendly display name for CLI output and reports."""
        return _PREFLIGHT_DISPLAY_NAMES[self]

    @property
    def is_auth(self) -> bool:
        return self == PreFlightFailureType.AUTH

    @property
    def is_unreachable(self) -> bool:
        return self == PreFlightFailureType.UNREACHABLE

    @property
    def is_detection(self) -> bool:
        return self == PreFlightFailureType.DETECTION


_PREFLIGHT_DISPLAY_NAMES: dict[PreFlightFailureType, str] = {
    PreFlightFailureType.AUTH: "Controller Authentication Failed",
    PreFlightFailureType.UNREACHABLE: "Controller Unreachable",
    PreFlightFailureType.DETECTION: "Controller Detection Failed",
}


class ExecutionState(str, Enum):
    """Execution state for test results.

    Distinguishes between different outcomes:
        SUCCESS: Tests ran (may have test failures, but execution succeeded)
        EMPTY: No tests found/executed (expected outcome, not an error)
        SKIPPED: Tests intentionally skipped (e.g., render-only mode)
        ERROR: Execution failed with an error (e.g., framework crash)
    """

    SUCCESS = "success"
    EMPTY = "empty"
    SKIPPED = "skipped"
    ERROR = "error"


class ErrorType(Enum):
    """Categorized error types for exit code determination.

    Used by TestResults.from_error() to indicate specific error conditions
    that map to different exit codes in CombinedResults.exit_code.
    """

    GENERIC = "generic"
    INTERRUPTED = "interrupted"


@dataclass
class TestResults:
    """Test execution results from a single test framework or test type.

    Represents discrete test outcomes from one source (e.g., PyATS API tests,
    PyATS D2D tests, or Robot Framework tests). Does not aggregate across
    frameworks - use CombinedResults for that.

    Attributes:
        passed: Number of tests that passed
        failed: Number of tests that failed (includes errored tests)
        skipped: Number of tests that were skipped
        other: Number of tests with other statuses (blocked, aborted, passx, info)
        reason: Context for non-SUCCESS states (error message or skip reason)
        state: Execution state indicating the outcome type

    Properties:
        total: Total number of tests (computed as passed + failed + skipped + other)

    Note: Robot Framework only has three test statuses (PASS/FAIL/SKIP), so
    `other` will always be 0 for Robot results. PyATS has additional statuses
    (blocked, passx, aborted, info) which are tracked in `other`.

    The total is always computed correctly:
        total = passed + failed + skipped + other

    For success rate calculation, skipped tests are excluded from the denominator
    since they weren't executed. Tests in `other` (blocked, aborted, etc.) ARE
    included in the denominator as they represent tests that were attempted but
    did not pass.
    """

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    other: int = 0
    reason: str | None = None
    state: ExecutionState = ExecutionState.SUCCESS
    error_type: ErrorType | None = None

    @property
    def total(self) -> int:
        """Total number of tests (always computed from counts)."""
        return self.passed + self.failed + self.skipped + self.other

    @classmethod
    def empty(cls) -> "TestResults":
        """Create empty results (no tests found/executed).

        Use when no tests were discovered or matched filters.
        This is an expected outcome, not an error.
        """
        return cls(state=ExecutionState.EMPTY)

    @classmethod
    def not_run(cls, reason: str | None = None) -> "TestResults":
        """Create results for intentionally skipped execution.

        Use when tests were intentionally not run (e.g., render-only mode).

        Args:
            reason: Optional explanation for why tests were skipped
        """
        return cls(state=ExecutionState.SKIPPED, reason=reason)

    @classmethod
    def from_error(
        cls, reason: str, error_type: ErrorType = ErrorType.GENERIC
    ) -> "TestResults":
        """Create TestResults representing an execution error.

        Use when test execution failed due to a framework or infrastructure error
        (not test failures).

        Args:
            reason: Error message describing what went wrong
            error_type: Category of error for exit code determination

        Returns:
            TestResults with zero counts, reason recorded, and ERROR state
        """
        return cls(reason=reason, state=ExecutionState.ERROR, error_type=error_type)

    @property
    def success_rate(self) -> float:
        """Success rate excluding skipped tests (0.0-100.0)."""
        tests_with_results = self.total - self.skipped
        if tests_with_results > 0:
            return (self.passed / tests_with_results) * 100
        return 0.0

    @property
    def has_failures(self) -> bool:
        """Check if any tests failed."""
        return self.failed > 0

    @property
    def has_error(self) -> bool:
        """Check if an execution error occurred (not test failures)."""
        return self.state == ExecutionState.ERROR

    @property
    def is_empty(self) -> bool:
        """Check if no tests were executed."""
        return self.total == 0

    @property
    def is_error(self) -> bool:
        """Check if execution failed with an error."""
        return self.state == ExecutionState.ERROR

    @property
    def was_not_run(self) -> bool:
        """Check if tests were intentionally not run."""
        return self.state == ExecutionState.SKIPPED

    def __str__(self) -> str:
        """Concise string representation: total/passed/failed/skipped[/other]."""
        base = f"{self.total}/{self.passed}/{self.failed}/{self.skipped}"
        if self.other > 0:
            return f"{base}/{self.other}"
        return base


@dataclass
class PyATSResults:
    """Results from PyATS test execution.

    Groups API and D2D test results from a single PyATS run.
    Robot Framework doesn't need this as it returns a single TestResults
    (Robot doesn't distinguish between API and D2D test types).

    Attributes:
        api: Results from PyATS API tests (controller API validation)
        d2d: Results from PyATS D2D tests (device-to-device validation)
    """

    api: TestResults | None = None
    d2d: TestResults | None = None

    def __str__(self) -> str:
        """Concise string: PyATSResults(API: t/p/f/s, D2D: t/p/f/s)."""
        parts = []
        if self.api is not None:
            parts.append(f"API: {self.api}")
        if self.d2d is not None:
            parts.append(f"D2D: {self.d2d}")
        return f"PyATSResults({', '.join(parts) if parts else 'empty'})"


@dataclass(frozen=True)
class PreFlightFailure:
    """Pre-execution failure that prevented all test execution.

    Represents conditions detected before any tests run (auth failure,
    controller unreachable, etc.). When present on CombinedResults,
    all test counts will be zero.

    Attributes:
        failure_type: Category of failure.
        controller_type: Controller identifier, or None for detection failures.
        controller_url: URL that was tested, or None for detection failures.
        detail: Human-readable error description.
        status_code: HTTP status code from the failed request, or None for
            non-HTTP failures (e.g., connection timeout, DNS failure).
    """

    failure_type: PreFlightFailureType
    controller_type: ControllerTypeKey | None
    controller_url: str | None
    detail: str
    status_code: int | None = None


@dataclass
class CombinedResults:
    """Combined test results from all frameworks.

    Container for test results across PyATS (API/D2D) and Robot Framework.
    Uses explicit attributes for type safety and clear ownership.

    Attributes:
        api: Results from PyATS API tests (controller API validation)
        d2d: Results from PyATS D2D tests (device-to-device validation)
        robot: Results from Robot Framework tests
        pre_flight_failure: Pre-execution failure that prevented testing
    """

    api: TestResults | None = None
    d2d: TestResults | None = None
    robot: TestResults | None = None
    pre_flight_failure: PreFlightFailure | None = None

    def __str__(self) -> str:
        """Concise string: CombinedResults(API: t/p/f/s, D2D: t/p/f/s, Robot: t/p/f/s)."""
        if self.pre_flight_failure is not None:
            pf = self.pre_flight_failure
            return f"CombinedResults(pre_flight_failure={pf.failure_type}: {pf.detail})"
        parts = []
        if self.api is not None:
            parts.append(f"API: {self.api}")
        if self.d2d is not None:
            parts.append(f"D2D: {self.d2d}")
        if self.robot is not None:
            parts.append(f"Robot: {self.robot}")
        return f"CombinedResults({', '.join(parts) if parts else 'empty'})"

    @property
    def _results(self) -> list["TestResults"]:
        """List of non-None results for aggregation."""
        return [r for r in (self.api, self.d2d, self.robot) if r is not None]

    @property
    def total(self) -> int:
        """Total tests across all frameworks."""
        return sum(r.total for r in self._results)

    @property
    def passed(self) -> int:
        """Total passed tests across all frameworks."""
        return sum(r.passed for r in self._results)

    @property
    def failed(self) -> int:
        """Total failed tests across all frameworks."""
        return sum(r.failed for r in self._results)

    @property
    def skipped(self) -> int:
        """Total skipped tests across all frameworks."""
        return sum(r.skipped for r in self._results)

    @property
    def other(self) -> int:
        """Total tests with other statuses across all frameworks."""
        return sum(r.other for r in self._results)

    @property
    def errors(self) -> list[str]:
        """All execution errors/reasons across all frameworks."""
        return [r.reason for r in self._results if r.reason is not None]

    @property
    def was_not_run(self) -> bool:
        """Check if all frameworks were intentionally not run (e.g., render-only mode)."""
        return bool(self._results) and all(r.was_not_run for r in self._results)

    @property
    def has_any_results(self) -> bool:
        """Check if any test framework produced results (tests actually ran)."""
        return bool(self._results)

    @property
    def success_rate(self) -> float:
        """Combined success rate excluding skipped tests (0.0-100.0)."""
        tests_with_results = self.total - self.skipped
        if tests_with_results > 0:
            return (self.passed / tests_with_results) * 100
        return 0.0

    @property
    def has_failures(self) -> bool:
        """Check if any tests failed across all frameworks."""
        return self.failed > 0

    @property
    def has_errors(self) -> bool:
        """Check if any execution errors occurred across all frameworks."""
        return any(r.state == ExecutionState.ERROR for r in self._results)

    @property
    def is_empty(self) -> bool:
        """Check if no tests were executed across all frameworks."""
        return self.total == 0

    @property
    def exit_code(self) -> int:
        """Calculate appropriate exit code per Robot Framework convention.

        Exit codes:
            0: All tests passed, no errors OR all frameworks intentionally skipped
            1: Pre-flight failure (auth, unreachable, or controller detection failed)
            1-250: Number of test failures (capped at 250)
            252: No tests found across any framework
                 Note: invalid robot/pabot extra arguments are now caught in main() and
                 no longer need to be inferred from test results
            253: Execution was interrupted (Ctrl+C, etc.)
            255: Execution errors occurred (has_errors is True)

        Note: Exit code 1 from pre-flight failure takes precedence over test failures.
        If pre-flight fails, exit code is 1 regardless of any subsequent test results.

        Priority (highest to lowest): pre-flight > 253 (interrupted) > 255 (generic) > failures > empty

        Why this priority? Pre-flight failures (1) indicate that testing could not even begin
        due to auth/connection issues — this is the most actionable signal. Interrupted (253)
        is next because the user explicitly stopped execution. Generic errors (255) indicate
        infrastructure problems (framework crash, output.xml parse failure, etc.) that make
        test results unreliable — any reported failures may be artifacts of the crash rather
        than genuine test failures, so we surface the infrastructure error first. Test failures
        (1-250) and empty results (252) are lowest priority as they represent normal execution
        outcomes.
        """
        if self.pre_flight_failure is not None:
            return EXIT_PREFLIGHT_FAILURE
        if self.has_errors:
            error_types = [
                r.error_type for r in self._results if r.error_type is not None
            ]
            if ErrorType.INTERRUPTED in error_types:
                return EXIT_INTERRUPTED
            return EXIT_ERROR
        if self.has_failures:
            return min(self.failed, EXIT_FAILURE_CAP)
        # was_not_run must precede is_empty: both evaluate to total==0,
        # but SKIPPED (intentional) should return 0, not 252. See #645.
        if self.was_not_run:
            return 0
        if self.is_empty:
            return EXIT_DATA_ERROR
        return 0
