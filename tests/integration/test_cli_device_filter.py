# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2026 Cisco Systems, Inc.

"""Integration tests for --device-filter CLI option."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import nac_test.cli.main
from nac_test.core.constants import EXIT_INVALID_ARGS

pytestmark = [
    pytest.mark.integration,
]


def test_cli_invalid_device_filter_syntax_fails(tmp_path: Path) -> None:
    """Test that invalid --device-filter syntax produces an argument error (exit 2)."""
    runner = CliRunner()
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            "tests/integration/fixtures/data/data.yaml",
            "-t",
            "tests/integration/fixtures/templates/",
            "-o",
            str(tmp_path),
            "--device-filter",
            "invalid_filter_without_operator",
        ],
    )
    assert result.exit_code in (EXIT_INVALID_ARGS, 2)
    assert (
        "Invalid filter expression" in result.output
        or "Missing operator" in result.output
    )


def test_cli_device_filter_robot_only_warning(tmp_path: Path) -> None:
    """Test that --device-filter emits a warning when only Robot tests run."""
    runner = CliRunner()
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            "tests/integration/fixtures/data/data.yaml",
            "-d",
            "tests/integration/fixtures/data/defaults.yaml",
            "-t",
            "tests/integration/fixtures/templates/",
            "-o",
            str(tmp_path),
            "--device-filter",
            "hostname=leaf1",
        ],
    )
    assert result.exit_code == 0
    assert (
        "--device-filter was specified but no PyATS tests were executed"
        in result.output
    )
