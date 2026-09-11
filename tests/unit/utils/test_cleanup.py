# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for CleanupManager."""

from __future__ import annotations

import signal
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from nac_test.core.constants import IS_WINDOWS
from nac_test.utils.cleanup import CleanupManager, get_cleanup_manager


@pytest.fixture
def fresh_cleanup_manager() -> Generator[CleanupManager, None, None]:
    """Create a fresh CleanupManager instance isolated from the global singleton."""
    with (
        patch.object(CleanupManager, "_instance", None),
        patch.object(CleanupManager, "_initialized", False),
        patch("atexit.register"),
    ):
        cm = CleanupManager()
        cm._files.clear()
        yield cm


class TestCleanupManagerSingleton:
    """Tests for CleanupManager singleton behavior."""

    def test_multiple_calls_return_same_instance(
        self, fresh_cleanup_manager: CleanupManager
    ) -> None:
        cm2 = CleanupManager()
        assert fresh_cleanup_manager is cm2

    def test_get_cleanup_manager_returns_singleton(
        self, fresh_cleanup_manager: CleanupManager
    ) -> None:
        assert get_cleanup_manager() is fresh_cleanup_manager


class TestCleanupManagerRegistration:
    """Tests for file registration and unregistration."""

    def test_register_adds_path_with_default_flag_false(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        """register() without keep_if_debug adds the path and stores False (always delete)."""
        test_file = tmp_path / "test.txt"
        test_file.touch()

        fresh_cleanup_manager.register(test_file)

        assert test_file.resolve() in fresh_cleanup_manager._files
        assert fresh_cleanup_manager._files[test_file.resolve()] is False

    def test_register_keep_if_debug_stores_true(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        """register(keep_if_debug=True) stores True for the path."""
        test_file = tmp_path / "test.txt"
        test_file.touch()

        fresh_cleanup_manager.register(test_file, keep_if_debug=True)

        assert fresh_cleanup_manager._files[test_file.resolve()] is True

    def test_unregister_prevents_deletion(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        """A file removed from the registry is not deleted on cleanup."""
        test_file = tmp_path / "test.txt"
        test_file.touch()

        fresh_cleanup_manager.register(test_file)
        fresh_cleanup_manager.unregister(test_file)
        fresh_cleanup_manager.run_cleanup()

        assert test_file.exists()

    @pytest.mark.parametrize("keep_if_debug", [False, True])
    def test_unregister_removes_path(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path, keep_if_debug: bool
    ) -> None:
        """unregister() removes the path regardless of keep_if_debug flag."""
        test_file = tmp_path / "test.txt"
        test_file.touch()

        fresh_cleanup_manager.register(test_file, keep_if_debug=keep_if_debug)
        fresh_cleanup_manager.unregister(test_file)

        assert test_file.resolve() not in fresh_cleanup_manager._files

    def test_unregister_nonexistent_path_is_safe(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        nonexistent = tmp_path / "nonexistent.txt"
        fresh_cleanup_manager.unregister(nonexistent)  # Should not raise


class TestCleanupManagerCleanup:
    """Tests for cleanup behavior."""

    def test_cleanup_removes_registered_files(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        test_file = tmp_path / "sensitive_data.yaml"
        test_file.write_text("secret: password123")

        fresh_cleanup_manager.register(test_file)
        fresh_cleanup_manager.run_cleanup()

        assert not test_file.exists()

    def test_cleanup_is_idempotent(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        test_file = tmp_path / "test.txt"
        test_file.touch()

        fresh_cleanup_manager.register(test_file)
        fresh_cleanup_manager.run_cleanup()

        assert not test_file.exists()  # deleted on first call

        fresh_cleanup_manager.run_cleanup()  # second call must not raise

    def test_files_registered_between_cleanups_are_deleted(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        """Files registered after a cleanup run are deleted by the next cleanup."""
        file1 = tmp_path / "first.txt"
        file2 = tmp_path / "second.txt"
        file1.touch()
        file2.touch()

        # First cycle: register file1, clean up
        fresh_cleanup_manager.register(file1)
        assert file1.exists()
        fresh_cleanup_manager.run_cleanup()
        assert not file1.exists()

        # Second cycle: register file2 after cleanup, clean up again
        fresh_cleanup_manager.register(file2)
        assert file2.exists()
        fresh_cleanup_manager.run_cleanup()
        assert not file2.exists()

    def test_cleanup_handles_already_deleted_file(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        test_file = tmp_path / "test.txt"
        test_file.touch()

        fresh_cleanup_manager.register(test_file)
        test_file.unlink()  # Delete before cleanup

        fresh_cleanup_manager.run_cleanup()  # Should not raise

    def test_cleanup_continues_after_single_file_error(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.touch()
        file2.touch()

        fresh_cleanup_manager.register(file1)
        fresh_cleanup_manager.register(file2)

        # Make file1 a directory so unlink fails
        file1.unlink()
        file1.mkdir()

        fresh_cleanup_manager.run_cleanup()

        # file2 should still be cleaned up despite file1 error
        assert not file2.exists()


class TestCleanupManagerSkipIfDebug:
    """Tests for keep_if_debug behaviour during cleanup."""

    @pytest.mark.parametrize(
        ("debug_mode", "expected_exists"),
        [
            (False, False),  # debug off → file is deleted
            (True, True),  # debug on  → file is kept
        ],
        ids=["debug_off_deletes", "debug_on_keeps"],
    )
    def test_keep_if_debug_respects_debug_mode(
        self,
        fresh_cleanup_manager: CleanupManager,
        tmp_path: Path,
        debug_mode: bool,
        expected_exists: bool,
    ) -> None:
        """Files registered with keep_if_debug=True are kept iff NAC_TEST_DEBUG is set."""
        test_file = tmp_path / "job.py"
        test_file.touch()

        fresh_cleanup_manager.register(test_file, keep_if_debug=True)

        with patch("nac_test.utils.cleanup.DEBUG_MODE", debug_mode):
            fresh_cleanup_manager.run_cleanup()

        assert test_file.exists() is expected_exists

    def test_normal_files_always_deleted_regardless_of_debug(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        """Files registered without keep_if_debug are always deleted, even in debug mode."""
        test_file = tmp_path / "sensitive.yaml"
        test_file.touch()

        fresh_cleanup_manager.register(test_file)

        with patch("nac_test.utils.cleanup.DEBUG_MODE", True):
            fresh_cleanup_manager.run_cleanup()

        assert not test_file.exists()

    def test_mixed_registration_in_debug_mode(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        """In debug mode, sensitive files are deleted but debug-skipped files are kept."""
        sensitive = tmp_path / "merged_data.json"
        debug_file = tmp_path / "job.py"
        sensitive.touch()
        debug_file.touch()

        fresh_cleanup_manager.register(sensitive)
        fresh_cleanup_manager.register(debug_file, keep_if_debug=True)

        with patch("nac_test.utils.cleanup.DEBUG_MODE", True):
            fresh_cleanup_manager.run_cleanup()

        assert not sensitive.exists()
        assert debug_file.exists()


class TestCleanupManagerThreadSafety:
    """Tests for thread-safe behavior."""

    def test_concurrent_register_and_cleanup(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        """Concurrent register() and run_cleanup() calls must not lose files silently.

        The real risk is a file registered while cleanup is iterating _files,
        causing either a RuntimeError (dict changed size) or a silently
        skipped deletion. This test creates files in parallel with cleanup
        and asserts that every file ends up either deleted (cleanup won the
        race) or still tracked and then cleaned up explicitly — nothing is
        silently lost.
        """
        num_threads = 10
        files_per_thread = 10
        all_files: list[Path] = []
        errors: list[Exception] = []

        def register_files(thread_id: int) -> None:
            try:
                for i in range(files_per_thread):
                    f = tmp_path / f"thread{thread_id}_file{i}.txt"
                    f.touch()
                    all_files.append(f)
                    fresh_cleanup_manager.register(f)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=register_files, args=(i,))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()

        # Trigger cleanup mid-registration to exercise lock contention
        fresh_cleanup_manager.run_cleanup()

        for t in threads:
            t.join()

        assert not errors, f"Exceptions in registration threads: {errors}"

        # Any file not yet deleted must still be tracked; clean up remaining
        for f in all_files:
            if f.exists():
                assert f.resolve() in fresh_cleanup_manager._files, (
                    f"{f.name} still exists but is no longer tracked — silently lost"
                )

        fresh_cleanup_manager.run_cleanup()

        assert all(not f.exists() for f in all_files), (
            "Some files were neither deleted by cleanup nor cleaned up on retry"
        )


@pytest.mark.skipif(IS_WINDOWS, reason="Signal tests not supported on Windows")
class TestCleanupManagerSignalHandlers:
    """Tests for signal handler behavior (Unix only)."""

    def test_sigint_cleans_up_and_raises_keyboard_interrupt(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        test_file = tmp_path / "test.txt"
        test_file.touch()
        fresh_cleanup_manager.register(test_file)

        with patch("signal.signal"), pytest.raises(KeyboardInterrupt):
            fresh_cleanup_manager._signal_handler(signal.SIGINT, None)

        assert not test_file.exists()

    def test_sigterm_cleans_up_and_reraises_signal(
        self, fresh_cleanup_manager: CleanupManager, tmp_path: Path
    ) -> None:
        test_file = tmp_path / "test.txt"
        test_file.touch()
        fresh_cleanup_manager.register(test_file)

        with patch("signal.signal"), patch("signal.raise_signal") as mock_raise:
            fresh_cleanup_manager._signal_handler(signal.SIGTERM, None)
            mock_raise.assert_called_once_with(signal.SIGTERM)

        assert not test_file.exists()

    @pytest.mark.parametrize(
        ("signum", "original_handler", "expected_restored"),
        [
            # SIG_DFL (value 0) is falsy — must use `is not None`, not truthiness check
            (signal.SIGTERM, signal.SIG_DFL, signal.SIG_DFL),
            # SIG_IGN (value 1) is truthy but should still be restored correctly
            (signal.SIGTERM, signal.SIG_IGN, signal.SIG_IGN),
            # None means no prior handler was recorded — fall back to SIG_DFL
            (signal.SIGTERM, None, signal.SIG_DFL),
        ],
        ids=["SIG_DFL_restored", "SIG_IGN_restored", "None_falls_back_to_SIG_DFL"],
    )
    def test_signal_handler_restores_original_handler(
        self,
        fresh_cleanup_manager: CleanupManager,
        signum: int,
        original_handler: Any,
        expected_restored: Any,
    ) -> None:
        """Handler restores the original signal disposition, including falsy SIG_DFL."""
        if signum == signal.SIGTERM:
            fresh_cleanup_manager._original_sigterm = original_handler
        else:
            fresh_cleanup_manager._original_sigint = original_handler

        restored: list[Any] = []

        def capture_signal(sig: int, handler: Any) -> None:
            restored.append((sig, handler))

        with (
            patch("signal.signal", side_effect=capture_signal),
            patch("signal.raise_signal"),
        ):
            fresh_cleanup_manager._signal_handler(signum, None)

        assert restored == [(signum, expected_restored)]


class TestCleanupManagerIntegration:
    """Integration tests using the real singleton."""

    def test_multiple_registrations_share_state(self, tmp_path: Path) -> None:
        cm = get_cleanup_manager()
        initial_count = len(cm._files)

        file1 = tmp_path / "module_a.txt"
        file2 = tmp_path / "module_b.txt"
        file1.touch()
        file2.touch()

        cm.register(file1)
        cm.register(file2)

        assert len(cm._files) == initial_count + 2

        # Clean up test files from singleton
        cm.unregister(file1)
        cm.unregister(file2)
