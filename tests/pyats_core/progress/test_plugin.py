# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for ProgressReporterPlugin._get_test_name()."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nac_test.pyats_core.constants import ENV_TEST_DIR
from nac_test.pyats_core.progress.plugin import ProgressReporterPlugin
from tests.conftest import PyATSTestDirs


def _make_plugin(
    monkeypatch: pytest.MonkeyPatch, test_dir: Path | None
) -> ProgressReporterPlugin:
    """Instantiate ProgressReporterPlugin with BasePlugin.__init__ patched out.

    Sets ENV_TEST_DIR to *test_dir* if provided, otherwise removes it from the
    environment. BasePlugin.__init__ is patched to avoid PyATS runtime dependencies.
    """
    if test_dir is not None:
        monkeypatch.setenv(ENV_TEST_DIR, str(test_dir))
    else:
        monkeypatch.delenv(ENV_TEST_DIR, raising=False)

    with patch("nac_test.pyats_core.progress.plugin.BasePlugin.__init__"):
        plugin = ProgressReporterPlugin.__new__(ProgressReporterPlugin)
        plugin.worker_id = "test-worker"
        plugin.task_start_times = {}
        plugin.event_count = 0
        plugin.test_dir_path = None
        raw = os.environ.get(ENV_TEST_DIR)
        if raw:
            candidate = Path(raw).absolute()
            if candidate.exists() and candidate.is_dir():
                plugin.test_dir_path = candidate
        return plugin


class TestGetTestName:
    """Tests for ProgressReporterPlugin._get_test_name()."""

    @pytest.mark.parametrize(
        "rel_script, expected",
        [
            ("nrfu/verify.py", "nrfu.verify"),
            ("api/tenants/verify_tenant.py", "api.tenants.verify_tenant"),
            ("verify_device.py", "verify_device"),
        ],
        ids=["one-level", "nested", "root-level"],
    )
    def test_returns_dot_notation_when_env_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pyats_test_dirs: PyATSTestDirs,
        rel_script: str,
        expected: str,
    ) -> None:
        """Path under test_dir produces correct dot-notation name."""
        script = pyats_test_dirs.test_dir / rel_script
        script.parent.mkdir(parents=True, exist_ok=True)
        script.touch()

        plugin = _make_plugin(monkeypatch, pyats_test_dirs.test_dir)
        assert plugin._get_test_name(str(script)) == expected

    def test_returns_stem_when_env_not_set(
        self, monkeypatch: pytest.MonkeyPatch, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """When ENV_TEST_DIR is unset, falls back to filename stem even for nested paths."""
        script = pyats_test_dirs.test_dir / "nrfu" / "verify_device.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.touch()

        plugin = _make_plugin(monkeypatch, None)
        # With ENV_TEST_DIR set this would return "nrfu.verify_device"; unset → stem only
        assert plugin._get_test_name(str(script)) == "verify_device"

    def test_returns_stem_when_path_not_under_test_dir(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pyats_test_dirs: PyATSTestDirs,
        tmp_path: Path,
    ) -> None:
        """Script outside test_dir falls back to filename stem."""
        outside = tmp_path / "other" / "verify_bgp.py"
        outside.parent.mkdir()
        outside.touch()

        plugin = _make_plugin(monkeypatch, pyats_test_dirs.test_dir)
        assert plugin._get_test_name(str(outside)) == "verify_bgp"

    def test_test_dir_path_resolved_once(
        self, monkeypatch: pytest.MonkeyPatch, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """ENV_TEST_DIR is read in __init__, not on every _get_test_name() call."""
        script = pyats_test_dirs.test_dir / "nrfu" / "verify.py"
        script.parent.mkdir()
        script.touch()

        plugin = _make_plugin(monkeypatch, pyats_test_dirs.test_dir)

        # Change the env var after construction — should have no effect
        monkeypatch.setenv(ENV_TEST_DIR, "/some/other/path")
        assert plugin._get_test_name(str(script)) == "nrfu.verify"
