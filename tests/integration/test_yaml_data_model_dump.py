# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Integration test for the optional YAML merged-data-model dump.

When ``NAC_TEST_DUMP_YAML_DATA_MODEL`` is set, nac-test writes a companion
YAML file next to the JSON merged data model. Unlike the JSON file, the YAML
file is intentionally *not* registered with the CleanupManager, so it survives
after the run for post-run debugging.

This test runs the real ``nac-test`` CLI in a subprocess. A subprocess is
required because ``DUMP_YAML_DATA_MODEL`` is evaluated from the environment at
import time — an in-process runner (e.g. CliRunner) cannot toggle it after the
module is already imported. The default (env var unset) case is covered by
``test_cli_rendering.py::test_merged_data_model_creates_default_filename``.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from nac_test.core.constants import IS_WINDOWS, MERGED_DATA_FILENAME
from nac_test.utils.yaml import safe_load

pytestmark = [pytest.mark.integration, pytest.mark.windows]


def test_yaml_dump_created_when_env_set(tmp_path: Path) -> None:
    """With the env var set, the YAML dump is written and persists after exit.

    We assert on the YAML file (which is *not* cleanup-registered and therefore
    survives) rather than the JSON file. Note that we do not assert here that the
    JSON was removed: JSON cleanup-on-exit is already covered end-to-end by
    ``tests/e2e/test_e2e_scenarios.py::test_merged_data_file_removed_after_run``.
    """
    templates_path = "tests/integration/fixtures/templates/"
    data_dir = Path("tests/integration/fixtures/data_merge")
    yaml_output_path = (tmp_path / MERGED_DATA_FILENAME).with_suffix(".yaml")

    env = {**os.environ, "NAC_TEST_DUMP_YAML_DATA_MODEL": "true"}
    result = subprocess.run(
        [
            "nac-test",
            "-d",
            str(data_dir / "file1.yaml"),
            "-d",
            str(data_dir / "file2.yaml"),
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
            "--render-only",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert result.returncode == 0, (
        f"nac-test should succeed, got exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    assert yaml_output_path.exists(), (
        f"YAML dump should persist at {yaml_output_path} when "
        "NAC_TEST_DUMP_YAML_DATA_MODEL=true (it is not cleanup-registered)"
    )

    # Sanity check: the YAML deserializes to the expected merged content.
    yaml_data = safe_load(yaml_output_path.read_text(encoding="utf-8"))
    assert isinstance(yaml_data, dict) and yaml_data, "YAML dump should be non-empty"

    # YAML dump must carry the same restrictive permissions as the JSON file.
    if not IS_WINDOWS:
        yaml_mode = stat.S_IMODE(yaml_output_path.stat().st_mode)
        assert yaml_mode == 0o600, (
            f"YAML dump should have 0o600 permissions, got {oct(yaml_mode)}"
        )
