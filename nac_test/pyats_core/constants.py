# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""PyATS-specific constants and configuration."""

import os

from nac_test._env import get_bool_env, get_positive_numeric_env
from nac_test.core.constants import (
    CONNECTION_CLOSE_DELAY,
    # Concurrency
    DEFAULT_API_CONCURRENCY,
    DEFAULT_SSH_CONCURRENCY,
    # Timeouts
    DEFAULT_TEST_TIMEOUT,
    # Platform detection
    IS_MACOS,
    IS_UNSUPPORTED_MACOS_PYTHON,
    # Progress
    PROGRESS_UPDATE_INTERVAL,
    RETRY_EXPONENTIAL_BASE,
    RETRY_INITIAL_DELAY,
    # Retry configuration
    RETRY_MAX_ATTEMPTS,
    RETRY_MAX_DELAY,
)

# PyATS-specific worker calculation constants
MIN_WORKERS: int = 2
MAX_WORKERS: int = 32
MAX_WORKERS_HARD_LIMIT: int = 50
MEMORY_PER_WORKER_GB: float = 0.35
DEFAULT_CPU_MULTIPLIER: int = 2
LOAD_AVERAGE_THRESHOLD: float = 0.8

# PyATS-specific timeouts
# Timeout for broker-routed device commands (e.g. Genie supplementary execute calls).
# Override via NAC_TEST_DEVICE_EXECUTE_TIMEOUT env var for slow WAN links or large outputs.
DEVICE_EXECUTE_TIMEOUT: int = get_positive_numeric_env(
    "NAC_TEST_DEVICE_EXECUTE_TIMEOUT", 120, int
)

# Timeout for each per-device disconnect during broker shutdown.  Chosen to be
# shorter than DEVICE_EXECUTE_TIMEOUT: if the lock holder is blocked on
# something cancellable this lets shutdown proceed, but note that an
# uncancellable run_in_executor call will still block in asyncio.run's
# shutdown_default_executor after the coroutine returns.
BROKER_SHUTDOWN_DEVICE_TIMEOUT: float = 30.0

# PyATS config files written to output directory during test execution
PYATS_PLUGIN_CONFIG_FILENAME: str = ".pyats_plugin.yaml"
PYATS_CONFIG_FILENAME: str = ".pyats.conf"

# pushed to pyats device connection settings to speed up disconnects (default is 10s/1s)
PYATS_POST_DISCONNECT_WAIT_SECONDS: int = 0
PYATS_GRACEFUL_DISCONNECT_WAIT_SECONDS: int = 0

# Connection broker protocol limits
# A 4-byte unsigned length prefix can represent ~4 GB; without this guard a buggy
# client could exhaust broker memory via a single oversized frame.
MAX_BROKER_MESSAGE_BYTES: int = 10 * 1024 * 1024  # 10 MB

# Multi-job execution configuration (to avoid reporter crashes)
TESTS_PER_JOB: int = 15  # Reduced from 20 for safety margin - each test ~1500 steps
MAX_PARALLEL_JOBS: int = 2  # Conservative parallelism to avoid resource exhaustion
JOB_RETRY_ATTEMPTS: int = 1  # Retry failed jobs once


# NOTE: The following environment variables remain as undocumented internal tuning
# knobs, not exposed as CLI flags or documented in README. Consider converting to
# proper constants with CLI flags in a future release if user demand warrants it:
# - NAC_TEST_PYATS_OUTPUT_BUFFER_LIMIT
# - NAC_TEST_PYATS_PIPE_DRAIN_DELAY
# - NAC_TEST_PYATS_PIPE_DRAIN_TIMEOUT
# - NAC_TEST_PYATS_BATCH_SIZE
# - NAC_TEST_PYATS_BATCH_TIMEOUT
# - NAC_TEST_PYATS_QUEUE_SIZE
# - NAC_TEST_PYATS_MEMORY_LIMIT_MB

# PyATS subprocess output buffer limit
# PyATS tests can generate extremely large output lines (100KB+ JSON responses from API calls).
# asyncio's default 64KB buffer would trigger `LimitOverrunError` and cause nac-test to hang.
# Default: 10MB - configurable via NAC_TEST_PYATS_OUTPUT_BUFFER_LIMIT environment variable
PYATS_OUTPUT_BUFFER_LIMIT: int = get_positive_numeric_env(
    "NAC_TEST_PYATS_OUTPUT_BUFFER_LIMIT", 10 * 1024 * 1024, int
)

# macOS subprocess pipe drain configuration (secondary fallback for backward compatibility)
# Used as fallback when sentinel-based synchronization is unavailable (e.g., old plugins
# that don't emit sentinels). Prefer sentinel-based sync when possible.
# macOS has different pipe buffering behavior that requires extra time for kernel flush
# Default: 100ms on macOS (balances reliability vs performance), 1ms on Linux
# These values can be overridden via environment variables for CI tuning
_pipe_drain_default = 0.1 if IS_MACOS else 0.001
PIPE_DRAIN_DELAY_SECONDS: float = get_positive_numeric_env(
    "NAC_TEST_PYATS_PIPE_DRAIN_DELAY", _pipe_drain_default, float
)
PIPE_DRAIN_TIMEOUT_SECONDS: float = get_positive_numeric_env(
    "NAC_TEST_PYATS_PIPE_DRAIN_TIMEOUT", 2.0, float
)

# Batching reporter configuration
# Controls how PyATS reporter messages are batched for efficient transmission

# Enable batching reporter: buffers step messages instead of sending immediately
# Set NAC_TEST_BATCHING_REPORTER=true to enable (experimental)
BATCHING_REPORTER_ENABLED: bool = get_bool_env("NAC_TEST_BATCHING_REPORTER")

# Batch size: number of messages accumulated before flush (default: 200)
BATCH_SIZE: int = get_positive_numeric_env("NAC_TEST_PYATS_BATCH_SIZE", 200, int)

# Batch timeout: seconds before auto-flush even if batch incomplete (default: 0.5s)
BATCH_TIMEOUT_SECONDS: float = get_positive_numeric_env(
    "NAC_TEST_PYATS_BATCH_TIMEOUT", 0.5, float
)

# Overflow queue size: maximum overflow queue size for burst handling (default: 5000)
OVERFLOW_QUEUE_SIZE: int = get_positive_numeric_env(
    "NAC_TEST_PYATS_QUEUE_SIZE", 5000, int
)

# Overflow memory limit: maximum memory for overflow queue in MB (default: 500MB)
OVERFLOW_MEMORY_LIMIT_MB: int = get_positive_numeric_env(
    "NAC_TEST_PYATS_MEMORY_LIMIT_MB", 500, int
)

# Overflow directory override: user-specified directory for overflow files
# Default: system temp directory (tempfile.gettempdir()/nac_test_overflow)
OVERFLOW_DIR_OVERRIDE: str | None = os.environ.get("NAC_TEST_PYATS_OVERFLOW_DIR")

# Environment variable name used to pass the test directory to PyATS subprocesses.
# Set by the orchestrator and device_executor; read by the progress plugin to compute
# relative (dot-notation) test names.
ENV_TEST_DIR: str = "NAC_TEST_TEST_DIR"

# Valid test types for PyATS test classification
# Used by discovery and cleanup modules
VALID_TEST_TYPES: frozenset[str] = frozenset({"api", "d2d"})

# Re-export all constants for backward compatibility
__all__ = [
    # From core
    "RETRY_MAX_ATTEMPTS",
    "RETRY_INITIAL_DELAY",
    "RETRY_MAX_DELAY",
    "RETRY_EXPONENTIAL_BASE",
    "DEFAULT_TEST_TIMEOUT",
    "CONNECTION_CLOSE_DELAY",
    "DEFAULT_API_CONCURRENCY",
    "DEFAULT_SSH_CONCURRENCY",
    "PROGRESS_UPDATE_INTERVAL",
    # PyATS-specific
    "MIN_WORKERS",
    "MAX_WORKERS",
    "MAX_WORKERS_HARD_LIMIT",
    "MEMORY_PER_WORKER_GB",
    "DEFAULT_CPU_MULTIPLIER",
    "LOAD_AVERAGE_THRESHOLD",
    "PYATS_PLUGIN_CONFIG_FILENAME",
    "PYATS_CONFIG_FILENAME",
    "PYATS_POST_DISCONNECT_WAIT_SECONDS",
    "PYATS_GRACEFUL_DISCONNECT_WAIT_SECONDS",
    # Connection broker protocol limits
    "MAX_BROKER_MESSAGE_BYTES",
    # Device execution
    "DEVICE_EXECUTE_TIMEOUT",
    "BROKER_SHUTDOWN_DEVICE_TIMEOUT",
    # Multi-job execution
    "TESTS_PER_JOB",
    "MAX_PARALLEL_JOBS",
    "JOB_RETRY_ATTEMPTS",
    # Subprocess handling
    "PYATS_OUTPUT_BUFFER_LIMIT",
    # Platform detection and pipe drain configuration
    "IS_MACOS",
    "IS_UNSUPPORTED_MACOS_PYTHON",
    "PIPE_DRAIN_DELAY_SECONDS",
    "PIPE_DRAIN_TIMEOUT_SECONDS",
    # Batching reporter
    "BATCHING_REPORTER_ENABLED",
    "BATCH_SIZE",
    "BATCH_TIMEOUT_SECONDS",
    "OVERFLOW_QUEUE_SIZE",
    "OVERFLOW_MEMORY_LIMIT_MB",
    "OVERFLOW_DIR_OVERRIDE",
    # Environment variable name
    "ENV_TEST_DIR",
    # Test type classification
    "VALID_TEST_TYPES",
]
