# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Core constants shared across the nac-test framework."""

import os
import platform
import sys
import tempfile

from nac_test._env import get_bool_env, get_positive_numeric_env

# Retry configuration - Generic retry logic used by multiple components
RETRY_MAX_ATTEMPTS: int = 3
RETRY_INITIAL_DELAY: float = 1.0
RETRY_MAX_DELAY: float = 60.0
RETRY_EXPONENTIAL_BASE: float = 2.0

# General timeouts
DEFAULT_TEST_TIMEOUT: int = 21600  # 6 hours per test
CONNECTION_CLOSE_DELAY: float = 0.25  # seconds

# Concurrency limits - Can be used by both PyATS and Robot
# Can be overridden via environment variables
DEFAULT_API_CONCURRENCY: int = get_positive_numeric_env(
    "NAC_TEST_PYATS_API_CONCURRENCY", 55, int
)
DEFAULT_SSH_CONCURRENCY: int = get_positive_numeric_env(
    "NAC_TEST_PYATS_SSH_CONCURRENCY", 20, int
)
# Progress reporting
PROGRESS_UPDATE_INTERVAL: float = 0.5  # seconds

# Debug mode - enables progressive disclosure of error details
# Set NAC_TEST_DEBUG=true for developer-level error context
DEBUG_MODE: bool = get_bool_env("NAC_TEST_DEBUG")


# Test-level parallelization control for Robot Framework
# Set NAC_TEST_DISABLE_TESTLEVELSPLIT=true to disable test-level parallelization
DISABLE_TESTLEVELSPLIT: bool = get_bool_env("NAC_TEST_DISABLE_TESTLEVELSPLIT")

# Merged data model YAML dump for debugging
# Set NAC_TEST_DUMP_YAML_DATA_MODEL=true to write YAML alongside JSON
DUMP_YAML_DATA_MODEL: bool = get_bool_env("NAC_TEST_DUMP_YAML_DATA_MODEL")

# Report timestamp format - single source of truth for all report generators
REPORT_TIMESTAMP_FORMAT: str = "%Y-%m-%d %H:%M:%S"

# Progress/logging timestamp format with millisecond precision
PROGRESS_TIMESTAMP_FORMAT: str = "%Y-%m-%d %H:%M:%S.%f"

# Filesystem-safe timestamp formats for job IDs and filenames
FILE_TIMESTAMP_FORMAT: str = "%Y%m%d_%H%M%S"
FILE_TIMESTAMP_MS_FORMAT: str = "%Y%m%d_%H%M%S_%f"

# Simple time-only format for CLI progress display
CONSOLE_TIME_FORMAT: str = "%H:%M:%S"

# Robot Framework timestamp formats (e.g., "20250131 12:34:56.789")
ROBOT_TIMESTAMP_FORMAT: str = "%Y%m%d %H:%M:%S.%f"
ROBOT_TIMESTAMP_FORMAT_NO_MS: str = "%Y%m%d %H:%M:%S"

# Exit codes
# Note: EXIT_SUCCESS (0) is intentionally not defined here - zero is a universal
# POSIX convention that never changes, so a named constant adds no clarity.
EXIT_PREFLIGHT_FAILURE: int = 1  # Pre-flight failure (auth, unreachable, detection)
EXIT_INVALID_ARGS: int = (
    2  # Invalid nac-test arguments (aligns with POSIX/Typer convention)
)
EXIT_FAILURE_CAP: int = 250  # Maximum failure count reported (1-250)
EXIT_DATA_ERROR: int = 252  # Invalid Robot Framework arguments OR no tests found (matches Robot Framework naming)
EXIT_INTERRUPTED: int = 253  # Execution was interrupted (Ctrl+C, etc.)
EXIT_ERROR: int = 255  # Infrastructure/execution errors occurred

# Reason string used in TestResults.not_run() for dry-run
DRY_RUN_REASON = "dry-run mode"

# Output directory structure - single source of truth for directory layout
# These define the standardized paths for test results and reports
PYATS_RESULTS_DIRNAME: str = "pyats_results"
ROBOT_RESULTS_DIRNAME: str = "robot_results"
HTML_REPORTS_DIRNAME: str = "html_reports"
SUMMARY_REPORT_FILENAME: str = "summary_report.html"
COMBINED_SUMMARY_FILENAME: str = "combined_summary.html"
PRE_FLIGHT_FAILURE_FILENAME: str = "pre_flight_failure.html"
OUTPUT_XML: str = "output.xml"
LOG_HTML: str = "log.html"
REPORT_HTML: str = "report.html"
XUNIT_XML: str = "xunit.xml"
ORDERING_FILENAME: str = "ordering.txt"
MERGED_DATA_FILENAME: str = "merged_data_model_test_variables.json"
# Owner read/write only — prevents other users from reading sensitive merged data
MERGED_DATA_FILE_MODE: int = 0o600
SUMMARY_SEPARATOR_WIDTH: int = 70

# HTTP status code range boundaries
HTTP_STATUS_SUCCESS_MIN: int = 200
HTTP_STATUS_SUCCESS_MAX: int = 299
HTTP_STATUS_REDIRECT_MIN: int = 300
HTTP_STATUS_REDIRECT_MAX: int = 399
HTTP_STATUS_CLIENT_ERROR_MIN: int = 400
HTTP_STATUS_CLIENT_ERROR_MAX: int = 499
HTTP_STATUS_SERVER_ERROR_MIN: int = 500
HTTP_STATUS_SERVER_ERROR_MAX: int = 599

# Authentication failure status codes
HTTP_AUTH_FAILURE_CODES: tuple[int, ...] = (401, 403)

# Specific auth status codes for targeted detection
HTTP_FORBIDDEN_CODE: int = 403

# Service unavailable status codes (treat as unreachable)
HTTP_SERVICE_UNAVAILABLE_CODES: tuple[int, ...] = (408, 429, 503, 504)

# Controller context env var (orchestrator writes, subprocess reads)
ENV_CONTROLLER_CONTEXT: str = "NAC_TEST_CONTROLLER_CONTEXT"

# Auth cache directory (file-based token caching for parallel processes)
AUTH_CACHE_DIR: str = os.path.join(tempfile.gettempdir(), "nac-test-auth-cache")

# Platform detection
IS_MACOS: bool = platform.system() == "Darwin"
IS_WINDOWS: bool = platform.system() == "Windows"

# macOS requires Python 3.12+.
IS_UNSUPPORTED_MACOS_PYTHON: bool = IS_MACOS and sys.version_info < (3, 12)

# PyATS platform support (PyATS wheels are not available for Windows)
PYATS_SUPPORTED: bool = not IS_WINDOWS
