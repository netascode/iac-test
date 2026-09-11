# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Shared fixtures for PyATS orchestrator tests.

NOTE: This module intentionally duplicates some patterns from tests/unit/conftest.py.
Issue #541 will merge tests/pyats_core/ into tests/unit/, at which point these
fixtures should be consolidated into a single conftest.py.
"""

import pytest
from _pytest.monkeypatch import MonkeyPatch


@pytest.fixture()
def aci_controller_env(monkeypatch: MonkeyPatch) -> None:
    """Set ACI controller environment variables."""
    monkeypatch.setenv("ACI_URL", "https://apic.test.com")
    monkeypatch.setenv("ACI_USERNAME", "admin")
    monkeypatch.setenv("ACI_PASSWORD", "password")


@pytest.fixture()
def sdwan_controller_env(monkeypatch: MonkeyPatch) -> None:
    """Set SD-WAN controller environment variables."""
    monkeypatch.setenv("SDWAN_URL", "https://vmanage.test.com")
    monkeypatch.setenv("SDWAN_USERNAME", "admin")
    monkeypatch.setenv("SDWAN_PASSWORD", "password")


@pytest.fixture()
def cc_controller_env(monkeypatch: MonkeyPatch) -> None:
    """Set Catalyst Center controller environment variables."""
    monkeypatch.setenv("CC_URL", "https://cc.test.com")
    monkeypatch.setenv("CC_USERNAME", "admin")
    monkeypatch.setenv("CC_PASSWORD", "password")
