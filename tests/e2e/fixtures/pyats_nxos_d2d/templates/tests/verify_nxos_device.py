# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""[NRFU]: Verify NX-OS Port-Channel Operational State and Membership."""

import time
from typing import Any

from nac_test_pyats_common.nxos import NXOSTestBase
from pyats import aetest

from nac_test.pyats_core.reporting.types import ResultStatus

TITLE = "Verify NX-OS Port-Channel Operational State and Membership"

DESCRIPTION = "Validates the operational state of Port-Channels and member interfaces on NX-OS switches."

SETUP = "SSH access to the target NX-OS device."

PROCEDURE = (
    "1. Execute 'show port-channel summary'.\n"
    "2. Verify Port-Channel operational state and member interface bundling."
)

PASS_FAIL_CRITERIA = "Passes if all configured Port-Channels and member interfaces are operational ('up' and 'P')."


class VerifyNxosPortChannelSummary(NXOSTestBase):
    """[NX-OS] Verify Port-Channel Operational State and Member Ports."""

    TEST_CONFIG = {
        "resource_type": "Port-Channel",
        "api_endpoint": "show port-channel summary",
        "expected_values": {
            "oper_status": "up",
            "member_flag": "P",
        },
        "log_fields": [
            "hostname",
            "total_port_channels",
            "up_port_channels",
            "down_port_channels",
            "total_members",
            "up_members",
        ],
    }

    @aetest.test
    def test_port_channel_summary(self, steps: Any) -> None:
        """Entry point - delegates to base class async verification."""
        self.run_async_verification_test(steps)

    def get_items_to_verify(self) -> list[dict[str, Any]]:
        """Extract configured port-channels and member interfaces from the data model."""
        try:
            data_model = self.load_data_model()
        except Exception:
            data_model = {}

        devices = data_model.get("nxos", {}).get("devices", [])
        device_cfg: dict[str, Any] = {}
        for dev in devices:
            if dev.get("name") == self.hostname:
                device_cfg = dev.get("configuration", {})
                break

        interfaces = device_cfg.get("interfaces", {})
        configured_pcs = interfaces.get("port_channels", [])
        configured_ethernets = interfaces.get("ethernets", [])

        # Build map: po_id -> list of expected member interface names
        po_members: dict[int, list[str]] = {}
        for po in configured_pcs:
            po_id = po.get("id")
            if po_id is not None:
                po_members[int(po_id)] = []

        for eth in configured_ethernets:
            cg = eth.get("channel_group")
            eth_id = eth.get("id")
            if cg is not None and eth_id is not None:
                cg_int = int(cg)
                raw_id = str(eth_id)
                if not raw_id.lower().startswith("ethernet") and not raw_id.lower().startswith("eth"):
                    norm_eth = f"Ethernet{raw_id}"
                elif raw_id.lower().startswith("eth") and not raw_id.lower().startswith("ethernet"):
                    norm_eth = f"Ethernet{raw_id[3:]}"
                else:
                    norm_eth = raw_id

                if cg_int in po_members:
                    po_members[cg_int].append(norm_eth)
                else:
                    po_members[cg_int] = [norm_eth]

        return [
            {
                "hostname": self.hostname,
                "configured_port_channels": configured_pcs,
                "expected_members": po_members,
            }
        ]

    async def verify_item(
        self, semaphore: Any, client: Any, context: dict[str, Any]
    ) -> Any:
        """Verify Port-Channels and member interfaces on the NX-OS switch."""
        async with semaphore:
            command = self.TEST_CONFIG["api_endpoint"]
            api_context = self.build_api_context(
                self.TEST_CONFIG["resource_type"],
                f"Device {self.hostname}",
            )

            start_time = time.time()
            try:
                self.set_test_context(api_context)
                try:
                    assert self.execute_command is not None
                    output = await self.execute_command(command)
                finally:
                    self.clear_test_context()
                command_duration = time.time() - start_time

                parse_start = time.time()
                parsed_output = await self.parse_output(command, output=output)
                parse_duration = time.time() - parse_start
            except Exception as e:
                api_duration = time.time() - start_time
                error_msg = f"Failed to execute or parse command '{command}': {e}"
                self.logger.error(error_msg, exc_info=True)
                context["display_context"] = f"Port-Channel Summary -> {self.hostname}"
                return self.format_verification_result(
                    status=ResultStatus.FAILED,
                    context=context,
                    reason=f"PyATS Framework Exception: {error_msg}",
                    api_duration=api_duration,
                )

            api_duration = command_duration + parse_duration
            context["display_context"] = f"Port-Channel Summary -> {self.hostname}"

            if not parsed_output or "interfaces" not in parsed_output:
                return self.format_verification_result(
                    status=ResultStatus.FAILED,
                    context=context,
                    reason=f"No Port-Channel interfaces discovered in command output for '{command}'.",
                    api_duration=api_duration,
                )

            discovered_interfaces = parsed_output.get("interfaces", {})
            expected_members = context.get("expected_members", {})

            all_passed = True
            validation_lines: list[str] = []
            failures: list[str] = []
            total_members = 0
            up_members = 0
            up_pcs = 0
            down_pcs = 0

            # If specific port channels configured in data model, verify each
            target_po_ids = list(expected_members.keys()) if expected_members else []
            if not target_po_ids:
                # Fallback: check all discovered port-channels if none in data model
                for _po_name, po_data in discovered_interfaces.items():
                    bundle_id = po_data.get("bundle_id")
                    if bundle_id is not None:
                        target_po_ids.append(int(bundle_id))

            for po_id in target_po_ids:
                po_key = f"Port-channel{po_id}"
                po_data = discovered_interfaces.get(po_key)

                if not po_data:
                    all_passed = False
                    down_pcs += 1
                    msg = f"Port-channel{po_id}: Configured in data model but NOT found on device"
                    failures.append(f"  • {msg}")
                    validation_lines.append(f"[FAIL] {msg}")
                    continue

                oper_status = po_data.get("oper_status", "unknown")
                if oper_status.lower() == "up":
                    up_pcs += 1
                    po_status_str = f"[PASS] Port-channel{po_id} oper_status=up"
                else:
                    all_passed = False
                    down_pcs += 1
                    po_status_str = f"[FAIL] Port-channel{po_id} oper_status={oper_status} (Expected: up)"
                    failures.append(f"  • Port-channel{po_id} oper_status is '{oper_status}'")

                # Verify member ports
                members_dict = po_data.get("members", {})
                exp_members = expected_members.get(po_id, [])

                member_checks: list[str] = []
                for exp_m in exp_members:
                    total_members += 1
                    # Match member name (case-insensitive)
                    actual_member = None
                    for m_name in members_dict:
                        if m_name.lower() == exp_m.lower():
                            actual_member = members_dict[m_name]
                            break

                    if not actual_member:
                        all_passed = False
                        msg = f"{exp_m} missing from Port-channel{po_id} bundle"
                        failures.append(f"  • {msg}")
                        member_checks.append(f"{exp_m}=MISSING")
                    else:
                        flags = actual_member.get("flags", "")
                        if "P" in flags:
                            up_members += 1
                            member_checks.append(f"{exp_m}=Up(P)")
                        else:
                            all_passed = False
                            msg = f"{exp_m} in Port-channel{po_id} has flags '{flags}' (Expected 'P')"
                            failures.append(f"  • {msg}")
                            member_checks.append(f"{exp_m}=FLAG({flags})")

                if member_checks:
                    validation_lines.append(f"{po_status_str} [Members: {', '.join(member_checks)}]")
                else:
                    validation_lines.append(po_status_str)

            context["total_port_channels"] = len(target_po_ids)
            context["up_port_channels"] = up_pcs
            context["down_port_channels"] = down_pcs
            context["total_members"] = total_members
            context["up_members"] = up_members

            results_summary = "\n".join(validation_lines)

            if all_passed:
                reason = (
                    f"**NX-OS Port-Channel Summary Check PASSED**\n\n"
                    f"All {up_pcs} Port-Channels are operational (up) with all {up_members} member ports active (P).\n\n"
                    f"**Validation Results:**\n"
                    f"{results_summary}\n\n"
                    f"• Execution duration: {api_duration:.2f}s"
                )
                return self.format_verification_result(
                    status=ResultStatus.PASSED,
                    context=context,
                    reason=reason,
                    api_duration=api_duration,
                )
            else:
                failures_text = "\n".join(failures)
                reason = (
                    f"**NX-OS Port-Channel Summary Check FAILED**\n\n"
                    f"One or more Port-Channels or member ports are not operational:\n\n"
                    f"**Failures:**\n"
                    f"{failures_text}\n\n"
                    f"**Validation Results:**\n"
                    f"{results_summary}\n\n"
                    f"• Execution duration: {api_duration:.2f}s"
                )
                return self.format_verification_result(
                    status=ResultStatus.FAILED,
                    context=context,
                    reason=reason,
                    api_duration=api_duration,
                )
