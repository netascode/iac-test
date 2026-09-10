# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Integration tests for controller-to-defaults-prefix mapping.

This module verifies that all controller types in CONTROLLER_REGISTRY correctly
map to their defaults prefixes, and that the complete integration works end-to-end
from controller detection through defaults resolution.
"""

from typing import Any
from unittest.mock import patch

import pytest

from nac_test.core.controller import CONTROLLER_REGISTRY, get_defaults_prefix


class TestControllerDefaultsPrefixMapping:
    """Tests that verify all controller types correctly map to defaults prefixes."""

    @pytest.mark.parametrize(
        "controller_type,expected_prefix",
        [
            ("ACI", "defaults.apic"),
            ("SDWAN", "defaults.sdwan"),
            ("CC", "defaults.catc"),
            ("MERAKI", "defaults.meraki"),
            ("FMC", "defaults.fmc"),
            ("ISE", "defaults.ise"),
            ("IOSXE", "defaults.iosxe"),
            ("NXOS", "defaults.nxos"),
        ],
    )
    def test_controller_type_maps_to_correct_prefix(
        self, controller_type: str, expected_prefix: str
    ) -> None:
        """Each controller type should map to its correct defaults prefix."""
        # Verify the mapping is in CONTROLLER_REGISTRY
        assert controller_type in CONTROLLER_REGISTRY

        # Verify the defaults_prefix is correctly configured
        config = CONTROLLER_REGISTRY[controller_type]
        assert config.defaults_prefix == expected_prefix

        # Verify get_defaults_prefix() returns the correct value
        assert get_defaults_prefix(controller_type) == expected_prefix

    def test_all_controller_types_have_defaults_prefix(self) -> None:
        """Every controller in CONTROLLER_REGISTRY must have a defaults_prefix."""
        for controller_type, config in CONTROLLER_REGISTRY.items():
            assert config.defaults_prefix is not None, (
                f"Controller {controller_type} missing defaults_prefix"
            )
            assert config.defaults_prefix.startswith("defaults."), (
                f"Controller {controller_type} defaults_prefix must start with 'defaults.'"
            )

    def test_defaults_prefixes_are_unique(self) -> None:
        """Each controller should have a unique defaults prefix."""
        prefixes = [config.defaults_prefix for config in CONTROLLER_REGISTRY.values()]
        assert len(prefixes) == len(set(prefixes)), "Duplicate defaults_prefix found"

    def test_unknown_controller_type_fallback(self) -> None:
        """get_defaults_prefix should gracefully handle unknown controller types."""
        result = get_defaults_prefix("UNKNOWN_CONTROLLER")
        assert result == "defaults.unknown_controller"


class TestGetDefaultValueIntegration:
    """Integration tests for resolve_default_value() in real test class instances.

    These tests verify the complete integration flow:
    1. Controller type detection from environment
    2. Mapping to defaults prefix
    3. Error message generation with display_name
    4. Delegation to defaults_resolver with correct parameters
    """

    def test_aci_integration_with_real_instance(
        self,
        nac_test_base_class: Any,
        aci_controller_env: None,
        apic_data_model: dict[str, Any],
    ) -> None:
        """Integration test: ACI controller detection → defaults.apic prefix → value resolution."""
        instance = nac_test_base_class.__new__(nac_test_base_class)
        instance.data_model = apic_data_model
        instance.controller_type = "ACI"

        # Mock only the _resolve function to verify correct delegation
        with patch(
            "nac_test.pyats_core.common.base_test.resolve_default_value"
        ) as mock_resolve:
            mock_resolve.return_value = "test-fabric"

            result = instance.get_default_value("fabric.name")

            # Verify correct delegation
            args, kwargs = mock_resolve.call_args
            assert args[0] is apic_data_model
            assert args[1] == "fabric.name"
            assert kwargs["defaults_prefix"] == "defaults.apic"
            assert kwargs["required"] is True
            assert result == "test-fabric"

    def test_sdwan_integration_with_real_instance(
        self,
        nac_test_base_class: Any,
        sdwan_controller_env: None,
        sdwan_data_model: dict[str, Any],
    ) -> None:
        """Integration test: SDWAN controller detection → defaults.sdwan prefix → value resolution."""
        instance = nac_test_base_class.__new__(nac_test_base_class)
        instance.data_model = sdwan_data_model
        instance.controller_type = "SDWAN"

        with patch(
            "nac_test.pyats_core.common.base_test.resolve_default_value"
        ) as mock_resolve:
            mock_resolve.return_value = 30

            result = instance.get_default_value("global.timeout")

            args, kwargs = mock_resolve.call_args
            assert kwargs["defaults_prefix"] == "defaults.sdwan"
            assert result == 30

    def test_cc_integration_with_real_instance(
        self,
        nac_test_base_class: Any,
        cc_controller_env: None,
    ) -> None:
        """Integration test: CC controller detection → defaults.catc prefix → value resolution."""
        instance = nac_test_base_class.__new__(nac_test_base_class)
        instance.data_model = {"defaults": {"catc": {"timeout": 60}}}
        instance.controller_type = "CC"

        with patch(
            "nac_test.pyats_core.common.base_test.resolve_default_value"
        ) as mock_resolve:
            mock_resolve.return_value = 60

            result = instance.get_default_value("timeout")

            args, kwargs = mock_resolve.call_args
            assert kwargs["defaults_prefix"] == "defaults.catc"
            assert result == 60

    def test_iosxe_integration_with_real_instance(
        self,
        nac_test_base_class: Any,
        iosxe_controller_env: None,
    ) -> None:
        """Integration test: IOSXE controller detection → defaults.iosxe prefix → value resolution."""
        instance = nac_test_base_class.__new__(nac_test_base_class)
        instance.data_model = {"defaults": {"iosxe": {"timeout": 45}}}
        instance.controller_type = "IOSXE"

        with patch(
            "nac_test.pyats_core.common.base_test.resolve_default_value"
        ) as mock_resolve:
            mock_resolve.return_value = 45

            result = instance.get_default_value("timeout")

            args, kwargs = mock_resolve.call_args
            assert kwargs["defaults_prefix"] == "defaults.iosxe"
            assert result == 45


class TestErrorPropagation:
    """Tests that verify errors propagate correctly from defaults_resolver to test classes."""

    def test_missing_defaults_block_error_propagates(
        self,
        nac_test_base_class: Any,
        aci_controller_env: None,
    ) -> None:
        """ValueError from defaults_resolver should propagate when defaults block missing.

        Note: get_default_value() does NOT call ensure_defaults_block_exists() because
        CLI validators perform pre-flight validation. When the defaults block is missing,
        the error is "Required default value not found" rather than the custom missing_error.
        This is acceptable behavior - the CLI validator should have caught this earlier.
        """
        instance = nac_test_base_class.__new__(nac_test_base_class)
        instance.data_model = {}  # No defaults block
        instance.controller_type = "ACI"

        with pytest.raises(ValueError) as exc_info:
            instance.get_default_value("fabric.name")

        # Should get "Required default value not found" error
        error_msg = str(exc_info.value)
        assert "Required default value not found" in error_msg
        assert "defaults.apic.fabric.name" in error_msg

    def test_missing_required_value_error_propagates(
        self,
        nac_test_base_class: Any,
        aci_controller_env: None,
    ) -> None:
        """ValueError for missing required value should propagate correctly."""
        instance = nac_test_base_class.__new__(nac_test_base_class)
        instance.data_model = {"defaults": {"apic": {}}}  # Empty defaults
        instance.controller_type = "ACI"

        with pytest.raises(ValueError) as exc_info:
            instance.get_default_value("nonexistent.path", required=True)

        error_msg = str(exc_info.value)
        assert "Required default value not found" in error_msg
        assert "nonexistent.path" in error_msg

    def test_type_error_for_no_paths_propagates(
        self,
        nac_test_base_class: Any,
        aci_controller_env: None,
        apic_data_model: dict[str, Any],
    ) -> None:
        """TypeError for no paths should propagate correctly."""
        instance = nac_test_base_class.__new__(nac_test_base_class)
        instance.data_model = apic_data_model
        instance.controller_type = "ACI"

        with pytest.raises(TypeError) as exc_info:
            instance.get_default_value()  # type: ignore[call-arg]

        error_msg = str(exc_info.value)
        assert "at least one default_path" in error_msg

    def test_cascade_fallback_error_propagates_all_paths(
        self,
        nac_test_base_class: Any,
        sdwan_controller_env: None,
        sdwan_data_model: dict[str, Any],
    ) -> None:
        """Error for cascade fallback should include all attempted paths."""
        instance = nac_test_base_class.__new__(nac_test_base_class)
        instance.data_model = sdwan_data_model
        instance.controller_type = "SDWAN"

        with pytest.raises(ValueError) as exc_info:
            instance.get_default_value(
                "missing.path1",
                "missing.path2",
                "missing.path3",
                required=True,
            )

        error_msg = str(exc_info.value)
        assert "Required default value not found" in error_msg
        # Should list all attempted paths
        assert "missing.path1" in error_msg
        assert "missing.path2" in error_msg
        assert "missing.path3" in error_msg
