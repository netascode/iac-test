# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Tests for flat-semaphore concurrency in _execute_device_tests_with_broker.

Validates that the orchestrator's device scheduling uses a flat semaphore
(no batched barriers), so a fast device finishing early frees a slot
immediately for the next queued device.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nac_test.pyats_core.orchestrator import PyATSOrchestrator

from .conftest import PyATSTestDirs


def _make_devices(n: int) -> list[dict[str, Any]]:
    return [{"hostname": f"device-{i}"} for i in range(n)]


def _make_orchestrator(
    pyats_test_dirs: PyATSTestDirs,
    max_workers: int = 4,
    max_parallel_devices: int | None = None,
) -> PyATSOrchestrator:
    orchestrator = PyATSOrchestrator(
        data_paths=[pyats_test_dirs.output_dir.parent / "data"],
        test_dir=pyats_test_dirs.test_dir,
        output_dir=pyats_test_dirs.output_dir,
    )
    orchestrator.max_workers = max_workers
    orchestrator.max_parallel_devices = max_parallel_devices
    orchestrator.base_output_dir = pyats_test_dirs.output_dir
    return orchestrator


def _patch_archive(orchestrator: PyATSOrchestrator) -> Any:
    return patch.object(
        type(orchestrator),
        "_populate_test_status_from_archive",
        new=lambda *_a, **_kw: None,
    )


class TestFlatSemaphoreConcurrency:
    """Tests for flat-semaphore device scheduling (PR #901)."""

    def test_all_devices_execute(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Every device executes — none silently skipped."""
        devices = _make_devices(7)
        executed: list[str] = []

        async def mock_run(
            device: dict[str, Any],
            test_files: list[Path],
            semaphore: asyncio.Semaphore,
        ) -> Path | None:
            async with semaphore:
                executed.append(device["hostname"])
            return None

        orchestrator = _make_orchestrator(pyats_test_dirs, max_workers=4)
        orchestrator.device_executor = MagicMock()
        orchestrator.device_executor.run_device_job_with_semaphore = AsyncMock(
            side_effect=mock_run
        )

        with _patch_archive(orchestrator):
            asyncio.run(
                orchestrator._execute_device_tests_with_broker(
                    test_files=[Path("test.py")], devices=devices
                )
            )

        assert sorted(executed) == sorted(d["hostname"] for d in devices)

    def test_concurrency_bounded_by_max_workers(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Semaphore limits concurrent execution to max_workers."""
        max_workers = 3
        devices = _make_devices(9)
        peak_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def mock_run(
            device: dict[str, Any],
            test_files: list[Path],
            semaphore: asyncio.Semaphore,
        ) -> Path | None:
            nonlocal peak_concurrent, current_concurrent
            async with semaphore:
                async with lock:
                    current_concurrent += 1
                    peak_concurrent = max(peak_concurrent, current_concurrent)
                await asyncio.sleep(0.02)
                async with lock:
                    current_concurrent -= 1
            return None

        orchestrator = _make_orchestrator(pyats_test_dirs, max_workers=max_workers)
        orchestrator.device_executor = MagicMock()
        orchestrator.device_executor.run_device_job_with_semaphore = AsyncMock(
            side_effect=mock_run
        )

        with _patch_archive(orchestrator):
            asyncio.run(
                orchestrator._execute_device_tests_with_broker(
                    test_files=[Path("test.py")], devices=devices
                )
            )

        assert peak_concurrent == min(max_workers, len(devices))

    def test_no_barrier_fast_devices_free_slots_immediately(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """Fast device frees a slot immediately — no barrier wait.

        With max_workers=2 and 4 devices (device-0 slow, device-1/2/3 fast),
        a batched approach would run [device-0, device-1] then [device-2, device-3].
        device-2 cannot start until device-0 finishes.

        A flat semaphore lets device-2 start as soon as device-1 finishes,
        overlapping with device-0.

        Proven structurally: device-0 blocks on an asyncio.Event that device-2
        sets on entry. Under a batched approach this deadlocks (device-2 can't
        start until device-0's batch finishes). Under a flat semaphore device-2
        starts while device-0 is blocked, sets the event, and both complete.
        """
        max_workers = 2
        devices = _make_devices(4)

        completed: list[str] = []

        async def _run() -> None:
            device_2_entered = asyncio.Event()

            async def mock_run(
                device: dict[str, Any],
                test_files: list[Path],
                semaphore: asyncio.Semaphore,
            ) -> Path | None:
                hostname = device["hostname"]
                async with semaphore:
                    if hostname == "device-0":
                        await asyncio.wait_for(device_2_entered.wait(), timeout=5.0)
                    elif hostname == "device-2":
                        device_2_entered.set()
                    completed.append(hostname)
                return None

            orchestrator = _make_orchestrator(pyats_test_dirs, max_workers=max_workers)
            orchestrator.device_executor = MagicMock()
            orchestrator.device_executor.run_device_job_with_semaphore = AsyncMock(
                side_effect=mock_run
            )

            with _patch_archive(orchestrator):
                await orchestrator._execute_device_tests_with_broker(
                    test_files=[Path("test.py")], devices=devices
                )

        asyncio.run(_run())

        assert sorted(completed) == sorted(d["hostname"] for d in devices)

    def test_max_parallel_devices_caps_concurrency(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """max_parallel_devices < max_workers becomes the effective limit."""
        devices = _make_devices(8)
        peak_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def mock_run(
            device: dict[str, Any],
            test_files: list[Path],
            semaphore: asyncio.Semaphore,
        ) -> Path | None:
            nonlocal peak_concurrent, current_concurrent
            async with semaphore:
                async with lock:
                    current_concurrent += 1
                    peak_concurrent = max(peak_concurrent, current_concurrent)
                await asyncio.sleep(0.02)
                async with lock:
                    current_concurrent -= 1
            return None

        orchestrator = _make_orchestrator(
            pyats_test_dirs, max_workers=6, max_parallel_devices=2
        )
        orchestrator.device_executor = MagicMock()
        orchestrator.device_executor.run_device_job_with_semaphore = AsyncMock(
            side_effect=mock_run
        )

        with _patch_archive(orchestrator):
            asyncio.run(
                orchestrator._execute_device_tests_with_broker(
                    test_files=[Path("test.py")], devices=devices
                )
            )

        assert peak_concurrent == 2

    def test_one_device_failure_does_not_block_others(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """A single device exception doesn't prevent other devices from completing."""
        devices = _make_devices(5)
        completed: list[str] = []

        async def mock_run(
            device: dict[str, Any],
            test_files: list[Path],
            semaphore: asyncio.Semaphore,
        ) -> Path | None:
            async with semaphore:
                if device["hostname"] == "device-2":
                    raise RuntimeError("simulated device failure")
                completed.append(device["hostname"])
            return None

        orchestrator = _make_orchestrator(pyats_test_dirs, max_workers=3)
        orchestrator.device_executor = MagicMock()
        orchestrator.device_executor.run_device_job_with_semaphore = AsyncMock(
            side_effect=mock_run
        )

        with _patch_archive(orchestrator):
            asyncio.run(
                orchestrator._execute_device_tests_with_broker(
                    test_files=[Path("test.py")], devices=devices
                )
            )

        assert sorted(completed) == ["device-0", "device-1", "device-3", "device-4"]

    @pytest.mark.parametrize(
        ("num_devices", "max_workers"),
        [
            (1, 4),
            (4, 4),
        ],
        ids=["single-device", "devices-equal-max-workers"],
    )
    def test_edge_cases(
        self,
        aci_controller_env: None,
        pyats_test_dirs: PyATSTestDirs,
        num_devices: int,
        max_workers: int,
    ) -> None:
        """Degenerate cases: single device and devices == max_workers."""
        devices = _make_devices(num_devices)
        executed: list[str] = []

        async def mock_run(
            device: dict[str, Any],
            test_files: list[Path],
            semaphore: asyncio.Semaphore,
        ) -> Path | None:
            async with semaphore:
                executed.append(device["hostname"])
            return None

        orchestrator = _make_orchestrator(pyats_test_dirs, max_workers=max_workers)
        orchestrator.device_executor = MagicMock()
        orchestrator.device_executor.run_device_job_with_semaphore = AsyncMock(
            side_effect=mock_run
        )

        with _patch_archive(orchestrator):
            asyncio.run(
                orchestrator._execute_device_tests_with_broker(
                    test_files=[Path("test.py")], devices=devices
                )
            )

        assert sorted(executed) == sorted(d["hostname"] for d in devices)

    def test_all_devices_share_single_semaphore_instance(
        self, aci_controller_env: None, pyats_test_dirs: PyATSTestDirs
    ) -> None:
        """All devices receive the same semaphore — the structural claim of this PR."""
        devices = _make_devices(6)
        seen_semaphores: list[asyncio.Semaphore] = []

        async def mock_run(
            device: dict[str, Any],
            test_files: list[Path],
            semaphore: asyncio.Semaphore,
        ) -> Path | None:
            seen_semaphores.append(semaphore)
            return None

        orchestrator = _make_orchestrator(pyats_test_dirs, max_workers=3)
        orchestrator.device_executor = MagicMock()
        orchestrator.device_executor.run_device_job_with_semaphore = AsyncMock(
            side_effect=mock_run
        )

        with _patch_archive(orchestrator):
            asyncio.run(
                orchestrator._execute_device_tests_with_broker(
                    test_files=[Path("test.py")], devices=devices
                )
            )

        assert len(seen_semaphores) == len(devices)
        assert len({id(s) for s in seen_semaphores}) == 1
