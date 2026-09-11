# PyATS Test Case Developer Guide

This guide is for engineers/developers writing pyATS test cases for the nac-test framework. It assumes you understand networking concepts but are new to this test framework. We'll cover pyATS basics as needed.

A pyATS test in this framework is typically 50–100 lines of Python. You write the verification logic — what to query, what to compare. The framework handles everything else:

- **Automatic test discovery** — inherit from the right base class and your file is found; no registration needed
- **Authentication and connection management** — login tokens are cached across all parallel tests; you never call an auth API yourself
- **Parallel execution** — API tests verify multiple items concurrently; D2D tests run across devices in parallel, with a shared SSH connection pool so each device gets one connection regardless of how many tests run against it
- **Command caching** — `show version` runs once per device per hour even if 10 tests need it
- **Structured HTML reports** — pass/fail per item, expandable command output, timing data; generated from streaming JSONL so partial results are preserved if a test crashes mid-run
- **CI/CD exit codes** — exit code reflects the number of failed tests; zero means everything passed

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
   - [1.1 How pyATS Tests Differ from Robot Framework](#11-how-pyats-tests-differ-from-robot-framework)
   - [1.2 Test Discovery](#12-test-discovery)
   - [1.3 Execution Model](#13-execution-model)
   - [1.4 Concurrency](#14-concurrency)
2. [Class Hierarchy](#2-class-hierarchy)
   - [2.1 Choosing the Right SSH Base Class](#21-choosing-the-right-ssh-base-class)
   - [2.2 Import Table](#22-import-table)
3. [The Three-Method Contract](#3-the-three-method-contract)
   - [3.1 The Entry Point](#31-the-entry-point-aetest-test-method)
   - [3.2 get_items_to_verify()](#32-get_items_to_verify)
   - [3.3 async verify_item()](#33-async-verify_itemsemaphore-client-context)
4. [Two Test Patterns](#4-two-test-patterns)
   - [4.1 NRFU Pattern](#41-nrfu-pattern-static-expected-values)
   - [4.2 Data-Model-Driven Pattern](#42-data-model-driven-pattern-expected-values-from-yaml)
   - [4.3 D2D/SSH Pattern](#43-d2dssh-pattern-device-directed-tests)
5. [The verify_group() Pattern](#5-the-verify_group-pattern)
6. [Data Model Access](#6-data-model-access)
   - [6.1 How --data Works](#61-how---data-works)
   - [6.2 Accessing the Data Model](#62-accessing-the-data-model)
   - [6.3 Defaults Resolution](#63-defaults-resolution)
   - [6.4 SSH/D2D Device Context](#64-sshd2d-device-context)
7. [Reporting Pass/Fail and Logging](#7-reporting-passfail-and-logging)
   - [7.1 ResultStatus Enum](#71-resultstatus-enum)
   - [7.2 Returning Results](#72-returning-results)
   - [7.3 display_context, api_context, and test_context](#73-display_context-api_context-and-test_context)
   - [7.4 display_context](#74-display_context)
   - [7.5 api_context](#75-api_context)
   - [7.6 Logging](#76-logging)
8. [TEST_CONFIG and Module Constants](#8-test_config-and-module-constants)
   - [8.1 TEST_CONFIG](#81-test_config)
   - [8.2 Optional TEST_CONFIG Keys](#82-optional-test_config-keys)
   - [8.3 Module-Level Constants](#83-module-level-constants-strongly-recommended)
9. [API Caching](#9-api-caching)
   - [9.1 Authentication Token Caching](#91-authentication-token-caching)
   - [9.2 HTTP Connection Pooling](#92-http-connection-pooling)
10. [SSH/CLI via the Connection Broker](#10-sshcli-via-the-connection-broker)
    - [10.1 execute_command()](#101-execute_command)
    - [10.2 parse_output()](#102-parse_output)
    - [10.3 test_context()](#103-test_context)
    - [10.4 Command Caching](#104-command-caching)
    - [10.5 Broker Architecture](#105-broker-architecture-for-understanding-not-interaction)
11. [Concurrency](#11-concurrency)
    - [11.1 API Tests](#111-api-tests)
    - [11.2 D2D Tests](#112-d2d-tests)
    - [11.3 What You Must Do](#113-what-you-must-do)
12. [Tag Filtering](#12-tag-filtering)
13. [Static Analysis and Type Checking](#13-static-analysis-and-type-checking)

## 1. Architecture Overview

### 1.1 How pyATS Tests Differ from Robot Framework

The nac-test framework supports two test execution approaches:

**Robot Framework:**
- Test cases are `.robot` files rendered from Jinja2 templates
- Data model values are injected at template render time (before execution)
- Tests execute via Pabot for parallel execution
- Template-driven approach separates test logic from test data

**pyATS:**
- Test cases are pure Python classes
- No template rendering — tests are discovered and executed directly
- Data model is loaded at runtime from merged YAML files
- Executed via `pyats run job` subprocesses
- Code-driven approach with full Python expressiveness

In production runs, pyATS tests execute first, followed by Robot Framework tests sequentially. During development, use `--pyats` or `--robot` flags to run only one type for faster iteration cycles.

### 1.2 Test Discovery

The nac-test framework discovers pyATS tests using a two-phase process: **discovery** and **classification**.

**Discovery:** A file is only considered a pyATS test file if it contains at least one class with a base class name that maps to a known test type. The framework uses Python's `ast` module to inspect the source without executing it — files that don't contain classes inheriting from a recognized base (e.g., `APICTestBase`, `IOSXETestBase`) are silently ignored, even if they reside in an `api/` or `d2d/` directory.

**Classification** (for discovered files) uses a three-tier strategy:

1. **AST-based (primary):** Base class names are checked against an internal mapping (e.g., `APICTestBase` → "api", `IOSXETestBase` → "d2d").
2. **Directory fallback:** If AST detection yields no match, the framework checks if the file path contains `/d2d/` or `/api/`.
3. **Default:** Falls back to `"api"` with a warning logged.

Practical implication: inherit from the correct base class and your test file is automatically discovered and classified correctly. No manual registration needed.


### 1.3 Execution Model

**API tests** (ACI, SD-WAN Manager, Catalyst Center):
- All collected tests run in a single PyATS job file
- Execute in one subprocess with a shared HTTP client session
- Concurrent verification controlled by semaphores
- Efficient for controller-based architectures

**D2D/SSH tests** (IOS-XE, SD-WAN edge devices):
- One PyATS job per device
- Device inventory discovered from the data model via the architecture's device resolver
- Each device gets its own subprocess with environment variables (`DEVICE_INFO`, `HOSTNAME`)
- Parallel device execution with concurrency control

### 1.4 Concurrency

**API tests:**
- `verify_item()` receives an `asyncio.Semaphore` for concurrent verification within a single job process
- Multiple items can be verified concurrently, limited by the semaphore

**D2D tests:**
- Devices run in parallel via `asyncio.gather()` with semaphore control
- Default concurrency: `min(20, device_count)` or override with `--max-parallel-devices`
- Within each device, verification is sequential (one SSH session per device)

**Always use `async with semaphore:` at the top of `verify_item()`** — the framework controls concurrency through this mechanism.

## 2. Class Hierarchy

The nac-test framework uses a three-tier class hierarchy:

```
Layer 1: nac-test (core framework)
├── NACTestBase(aetest.Testcase)     — base for ALL tests
└── SSHTestBase(NACTestBase)         — base for SSH/D2D tests

Layer 2: nac-test-pyats-common (architecture adapters)
├── APICTestBase(NACTestBase)              — ACI API tests
├── SDWANManagerTestBase(NACTestBase)      — SD-WAN Manager API tests
├── CatalystCenterTestBase(NACTestBase)    — Catalyst Center API tests
├── IOSXETestBase(SSHTestBase)             — SSH tests, controller-agnostic (auto-detects architecture)
├── SDWANTestBase(SSHTestBase)             — SD-WAN edge device SSH tests (SD-WAN-specific inventory)
└── CatalystCenterSSHTestBase(SSHTestBase) — CC-managed device SSH tests (CC-specific inventory)

Layer 3: Your test files (architecture repos)
└── verify_*.py files inheriting from Layer 2 bases
```

**Key principle:** You always inherit from a Layer 2 class. The Layer 2 class handles authentication, client creation, and device resolution. You focus on verification logic.

**Note:** D2D/SSH tests currently require a controller to discover the device inventory — the controller's data model is the source of truth for which devices to test and how to reach them. Standalone IOS-XE (no SD-WAN, no Catalyst Center) is not yet supported as a D2D target because there is no controller to resolve the device list from. Only SD-WAN (`SDWANTestBase`) and Catalyst Center (`CatalystCenterSSHTestBase`) managed IOS-XE devices are currently supported for D2D testing.

### 2.1 Choosing the Right SSH Base Class

For D2D/SSH tests there are three options — the right choice depends on how you want device inventory resolved:

| Base Class | Device Inventory Source | When to Use |
|---|---|---|
| `IOSXETestBase` | Auto-detected via plugin registry based on `controller_type` env var | **Preferred** — works across SD-WAN and Catalyst Center managed devices. The same test file runs regardless of which controller is managing the devices. |
| `SDWANTestBase` | Hard-wired to SD-WAN data model (`sdwan.sites[].routers[]`) | When your test is explicitly SD-WAN-specific and you want direct control over SD-WAN schema navigation |
| `CatalystCenterSSHTestBase` | Hard-wired to CC data model (`catalyst_center.inventory.devices[]`) | When your test is explicitly CC-specific |

`IOSXETestBase` delegates to the same `SDWANDeviceResolver` or `CatalystCenterDeviceResolver` at runtime — it is not a simpler class, just a more portable entry point.

### 2.2 Import Table

| Platform | Import Statement |
|----------|------------------|
| ACI | `from nac_test_pyats_common.aci import APICTestBase` |
| SD-WAN Manager | `from nac_test_pyats_common.sdwan import SDWANManagerTestBase` |
| Catalyst Center | `from nac_test_pyats_common.catc import CatalystCenterTestBase` |
| IOS-XE SSH (generic) | `from nac_test_pyats_common.iosxe import IOSXETestBase` |
| SD-WAN SSH (explicit) | `from nac_test_pyats_common.sdwan import SDWANTestBase` |
| Catalyst Center SSH (explicit) | `from nac_test_pyats_common.catc import CatalystCenterSSHTestBase` |

## 3. The Three-Method Contract

Every test class implements exactly three things. In exchange, the framework provides parallel execution, caching, authentication, retry logic, and a structured HTML report — without any additional code from you.

### 3.1 The Entry Point (`@aetest.test` method)

This is the method decorated with `@aetest.test` that PyATS discovers and executes. The body is always the same:

```python
@aetest.test
def test_bgp_neighbors(self, steps):
    """Verify BGP neighbor states."""
    self.run_async_verification_test(steps)
```

- **Decorated with `@aetest.test`** — required for PyATS discovery
- **`steps`** — a pyATS `Steps` object injected automatically by the framework. It is used internally by `run_async_verification_test()` to create one named sub-step per verified item, each with its own pass/fail status in the pyATS report. You pass it through and do not call it directly.
- **Body is always just:** `self.run_async_verification_test(steps)`
- **Name should be descriptive:** `test_bgp_neighbors`, `test_control_connections`, `test_ospf_neighbors`
- The docstring appears in PyATS logs and reports

### 3.2 `get_items_to_verify()`

This method returns a list of items to verify. Each item is a dictionary (called a "context") that describes one verification task.

```python
def get_items_to_verify(self) -> list[dict[str, Any]]:
    """Return list of items to verify."""
    devices = jmespath.search("sdwan.devices[?role=='edge']", self.data_model) or []
    return [
        {
            "hostname": device["hostname"],
            "system_ip": device["system_ip"],
        }
        for device in devices
    ]
```

**Key points:**
- Returns `list[dict[str, Any]]` — each dict is a context describing one item to verify
- Called once before verification begins
- **For SSH/D2D tests:** MUST NOT execute device commands here (SSH connection not yet established)
- Common patterns:
  - **Data-model iteration:** Extract from `self.data_model` via JMESPath
  - **Device enumeration:** Use `self.get_devices_from_data_model()` for SD-WAN
  - **Single trigger:** Return `[{"trigger": True}]` for tests that discover items from the live system

### 3.3 `async verify_item(semaphore, client, context)`

This is where the actual verification happens. It's called once for each item returned by `get_items_to_verify()`.

```python
async def verify_item(
    self,
    semaphore: asyncio.Semaphore,
    client: Any,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Verify one item."""
    async with semaphore:
        # Set display context for HTML report
        context["display_context"] = f"BGP -> {context['hostname']} ({context['system_ip']})"
        
        # Build API context for tracking
        api_context = self.build_api_context(
            "BGP Neighbors",
            context["hostname"],
            system_ip=context["system_ip"],
        )
        
        # Execute API call
        start_time = time.time()
        response = await client.get(
            f"{self.TEST_CONFIG['api_endpoint']}?deviceId={context['system_ip']}",
            test_context=api_context,
        )
        api_duration = time.time() - start_time
        
         if response.status_code != 200:
             context["api_duration"] = api_duration
             return self.format_api_error(response.status_code, response.url, context)
        
        neighbors = response.json().get("data", [])
        
        # Verify all neighbors are established
        failed_neighbors = [n for n in neighbors if n.get("state") != "established"]
        
        if failed_neighbors:
            reason = f"Found {len(failed_neighbors)} BGP neighbors not in established state"
            return self.format_verification_result(
                ResultStatus.FAILED,
                context,
                reason,
                api_duration,
            )
        
        return self.format_verification_result(
            ResultStatus.PASSED,
            context,
            f"All {len(neighbors)} BGP neighbors are established",
            api_duration,
        )
```

**Parameters:**
- `semaphore`: `asyncio.Semaphore` — always wrap body in `async with semaphore:`
- `client`: HTTP client (API tests) or `None` (SSH tests use `self.execute_command()`)
- `context`: one dict from `get_items_to_verify()`

**Must return:** A result dict from `self.format_verification_result()`

**Must set:** `context["display_context"]` for HTML report display

## 4. Two Test Patterns

### 4.1 NRFU Pattern (Static Expected Values)

Network Ready For Use (NRFU) tests verify universal healthy states. Expected values are hardcoded because they represent operational health, not configuration intent.

**Characteristics:**
- Tests verify universal healthy states: "up", "established", "green"
- Expected values are hardcoded in the test or in `TEST_CONFIG["expected_values"]`
- `get_items_to_verify()` might or might not use the data model to discover which items to check but NOT what values to expect
- Example: verify all BGP neighbors are "established", all control connections are "up"

**Example:**

```python
class VerifyControlConnections(SDWANManagerTestBase):
    TEST_CONFIG = {
        "resource_type": "SD-WAN Control Connections",
        "api_endpoint": "/dataservice/device/control/connections",
        "expected_values": {
            "state": "up",
        },
    }
    
    def get_items_to_verify(self) -> list[dict[str, Any]]:
        """Get all edge devices from data model."""
        devices = self.get_devices_from_data_model()
        return [{"hostname": d["hostname"], "system_ip": d["system_ip"]} for d in devices]
    
    async def verify_item(self, semaphore, client, context):
        async with semaphore:
            context["display_context"] = f"Control -> {context['hostname']}"
            
            # Query live system
            response = await client.get(
                f"{self.TEST_CONFIG['api_endpoint']}?deviceId={context['system_ip']}"
            )
            connections = response.json().get("data", [])
            
            # Compare against hardcoded expected state
            down_connections = [c for c in connections if c.get("state") != "up"]
            
            if down_connections:
                return self.format_verification_result(
                    ResultStatus.FAILED,
                    context,
                    f"Found {len(down_connections)} control connections not up",
                    0.0,
                )
            
            return self.format_verification_result(
                ResultStatus.PASSED,
                context,
                f"All {len(connections)} control connections are up",
                0.0,
            )
```

### 4.2 Data-Model-Driven Pattern (Expected Values from YAML)

These tests extract both items AND expected attribute values from the data model. The data model defines what the network should look like; the test verifies reality matches intent.

**Characteristics:**
- Tests extract BOTH items AND expected attribute values from `self.data_model`
- The data model defines what the network should look like
- `get_items_to_verify()` iterates the data model and builds context dicts with expected values
- `verify_item()` queries the live system and compares against those expected values
- Example: verify tenant BD subnet configuration matches data model, verify L3Out attributes match intent

**Example:**

```python
class VerifyBridgeDomainSubnets(APICTestBase):
    TEST_CONFIG = {
        "resource_type": "ACI Bridge Domain Subnets",
        "api_endpoint": "/api/node/class/fvSubnet.json",
        "attribute_names": {
            "ip": "Subnet IP",
            "scope": "Subnet Scope",
            "preferred": "Preferred",
        },
    }
    
    def get_items_to_verify(self) -> list[dict[str, Any]]:
        """Extract expected subnet configuration from data model."""
        items = []
        tenants = jmespath.search("apic.tenants[?name]", self.data_model) or []
        
        for tenant in tenants:
            bds = tenant.get("bridge_domains", [])
            for bd in bds:
                subnets = bd.get("subnets", [])
                for subnet in subnets:
                    items.append({
                        "tenant": tenant["name"],
                        "bd": bd["name"],
                        "subnet_ip": subnet["ip"],
                        # Expected values from data model
                        "expected_scope": subnet.get("scope", "private"),
                        "expected_preferred": subnet.get("preferred", False),
                    })
        
        return items
    
    async def verify_item(self, semaphore, client, context):
        async with semaphore:
            context["display_context"] = (
                f"{context['tenant']} -> {context['bd']} -> {context['subnet_ip']}"
            )
            
            # Query live system
            response = await client.get(
                f"{self.TEST_CONFIG['api_endpoint']}?"
                f"query-target-filter=eq(fvSubnet.ip,\"{context['subnet_ip']}\")"
            )
            
            subnets = response.json().get("imdata", [])
            if not subnets:
                return self.format_not_found("Subnet", context["subnet_ip"], context)
            
            actual = subnets[0]["fvSubnet"]["attributes"]
            
            # Compare actual against expected from data model
            if actual.get("scope") != context["expected_scope"]:
                return self.format_mismatch(
                    "scope",
                    context["expected_scope"],
                    actual.get("scope"),
                    context,
                )
            
            if actual.get("preferred") != str(context["expected_preferred"]).lower():
                return self.format_mismatch(
                    "preferred",
                    context["expected_preferred"],
                    actual.get("preferred"),
                    context,
                )
            
            return self.format_verification_result(
                ResultStatus.PASSED,
                context,
                "Subnet configuration matches data model",
                0.0,
            )
```

### 4.3 D2D/SSH Pattern (Device-Directed Tests)

For tests that execute CLI commands directly on devices via SSH, the flow is similar but uses `execute_command()` and `parse_output()` instead of an HTTP client. The SD-WAN edge device example below uses `SDWANTestBase`, which provides device inventory from the data model and routes SSH through the Connection Broker.

```python
import asyncio
import logging
import time
from typing import Any

import jmespath
from pyats import aetest
from nac_test_pyats_common.sdwan.ssh_test_base import SDWANTestBase
from nac_test.pyats_core.reporting.types import ResultStatus

logger = logging.getLogger(__name__)

TITLE = "Verify SD-WAN Control Connections (D2D)"
DESCRIPTION = """Verifies SD-WAN control connections are up by executing
'show sdwan control connections' directly on each edge device via SSH."""
SETUP = "* SSH access to SD-WAN edge devices.\n* Valid IOS-XE credentials configured.\n"
PROCEDURE = "* Execute 'show sdwan control connections' on each device.\n* Parse output and verify all connections are 'up'.\n"
PASS_FAIL_CRITERIA = "* PASS: All connections up.\n* FAIL: Any connection not up.\n* SKIP: No connections returned.\n"


class VerifySDWANControlConnectionsD2D(SDWANTestBase):
    """Verify SD-WAN control connections directly on edge devices."""

    groups = ["sdwan", "d2d", "control-plane"]

    TEST_CONFIG = {
        "resource_type": "SD-WAN Control Connections",
        "api_endpoint": "show sdwan control connections",
        "identifier_format": "Device {hostname}",
        "log_fields": ["hostname"],
    }

    def get_items_to_verify(self) -> list[dict[str, Any]]:
        """One item per test run — verify_item() runs on the current device only."""
        # self.hostname is set automatically by the framework to the current device
        return [{"hostname": self.hostname}]

    async def verify_item(
        self,
        semaphore: asyncio.Semaphore,
        client: Any,  # None for SSH tests
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify control connections on the current device."""
        async with semaphore:
            command = self.TEST_CONFIG["api_endpoint"]
            context["display_context"] = f"Control Connections -> {self.hostname}"

            api_context = self.build_api_context(
                self.TEST_CONFIG["resource_type"], self.hostname
            )
            with self.test_context(api_context):
                output = await self.execute_command(command)

            # Store api_context so the command output is linked to this result
            # in the HTML report — same pattern as API tests
            context["api_context"] = api_context

            if not output:
                return self.format_verification_result(
                    status=ResultStatus.ERRORED,
                    context=context,
                    reason=f"No output returned from '{command}' on {self.hostname}",
                    api_duration=0.0,
                )

            # nac-test 2.0: synchronous parse
            parsed = self.parse_output(command, output=output)
            # nac-test 2.1+: await self.parse_output(command, output=output)

            if parsed is None:
                # No Genie parser — fall back to string check
                if "up" not in output.lower():
                    return self.format_verification_result(
                        status=ResultStatus.FAILED,
                        context=context,
                        reason=f"No 'up' connections found in output on {self.hostname}",
                        api_duration=0.0,
                    )
                return self.format_verification_result(
                    status=ResultStatus.PASSED,
                    context=context,
                    reason=f"Control connections appear up on {self.hostname}",
                    api_duration=0.0,
                )

            # With parsed output, check each connection state
            connections = parsed.get("local_color", {}) or {}
            if not connections:
                return self.format_verification_result(
                    status=ResultStatus.SKIPPED,
                    context=context,
                    reason=f"No control connections found on {self.hostname}",
                    api_duration=0.0,
                )

            down = [
                f"{color}/{state['state']}"
                for color, state in connections.items()
                if state.get("state") != "up"
            ]
            if down:
                return self.format_verification_result(
                    status=ResultStatus.FAILED,
                    context=context,
                    reason=f"Down connections on {self.hostname}: {', '.join(down)}",
                    api_duration=0.0,
                )

            return self.format_verification_result(
                status=ResultStatus.PASSED,
                context=context,
                reason=f"All {len(connections)} control connections up on {self.hostname}",
                api_duration=0.0,
            )
```

**Key differences from API tests:**
- `client` parameter is `None` — use `self.execute_command()` and `self.parse_output()` instead
- `self.hostname` is auto-set by the framework — `get_items_to_verify()` typically returns a single-element list
- Each device runs in its own subprocess — you don't iterate over devices; the framework does that
- Wrap `execute_command()` in `self.test_context()` to link output to the HTML report

**Spectrum of approaches:**

Tests range from "data model for device discovery only" (pure NRFU) to "full data-model-driven attribute comparison" (configuration validation). There's no rigid boundary — choose the approach that fits your verification goals.

## 5. The verify_group() Pattern

For 1:N parent-child relationships (e.g., one Bridge Domain with many subnets), the `verify_group()` pattern allows you to make one API call per parent and verify multiple children from the same response.

**How it works:**
- `get_items_to_verify()` returns `dict[str, list[dict]]` instead of `list[dict]`
- When `run_async_verification_test()` sees a dict return, it dispatches `verify_group()` instead of `verify_item()` for each group key
- Your test class implements `verify_group()` instead of `verify_item()` — you choose which by the return type of `get_items_to_verify()`
- If `get_items_to_verify()` returns a list → `verify_item()` is called per entry; if it returns a dict → `verify_group()` is called per key


**Signature:**

```python
async def verify_group(
    self,
    semaphore: asyncio.Semaphore,
    client: Any,
    group_key: str,
    contexts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Verify a group of related items with one API call."""
    pass
```

**Example:**

```python
class VerifyBridgeDomainSubnets(APICTestBase):
    TEST_CONFIG = {
        "resource_type": "ACI Bridge Domain Subnets",
        "api_endpoint": "/api/node/mo/uni/tn-{tenant}/BD-{bd}.json",
    }
    
    def get_items_to_verify(self) -> dict[str, list[dict[str, Any]]]:
        """Group subnets by bridge domain."""
        groups = {}
        tenants = jmespath.search("apic.tenants[?name]", self.data_model) or []
        
        for tenant in tenants:
            bds = tenant.get("bridge_domains", [])
            for bd in bds:
                group_key = f"{tenant['name']}:{bd['name']}"
                groups[group_key] = []
                
                for subnet in bd.get("subnets", []):
                    groups[group_key].append({
                        "tenant": tenant["name"],
                        "bd": bd["name"],
                        "subnet_ip": subnet["ip"],
                        "expected_scope": subnet.get("scope", "private"),
                    })
        
        return groups
    
    async def verify_group(
        self,
        semaphore: asyncio.Semaphore,
        client: Any,
        group_key: str,
        contexts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Verify all subnets in a bridge domain with one API call."""
        async with semaphore:
            results = []
            
            # All contexts in the group share tenant and BD
            tenant = contexts[0]["tenant"]
            bd = contexts[0]["bd"]
            
            # One API call for the entire bridge domain
            url = self.TEST_CONFIG["api_endpoint"].format(tenant=tenant, bd=bd)
            response = await client.get(f"{url}?query-target=children")
            
            bd_data = response.json().get("imdata", [])
            actual_subnets = {
                s["fvSubnet"]["attributes"]["ip"]: s["fvSubnet"]["attributes"]
                for item in bd_data
                for s in [item] if "fvSubnet" in item
            }
            
            # Verify each subnet context against the single response
            for context in contexts:
                context["display_context"] = f"{tenant} -> {bd} -> {context['subnet_ip']}"
                
                actual = actual_subnets.get(context["subnet_ip"])
                if not actual:
                    results.append(
                        self.format_not_found("Subnet", context["subnet_ip"], context)
                    )
                    continue
                
                if actual.get("scope") != context["expected_scope"]:
                    results.append(
                        self.format_mismatch(
                            "scope",
                            context["expected_scope"],
                            actual.get("scope"),
                            context,
                        )
                    )
                else:
                    results.append(
                        self.format_verification_result(
                            ResultStatus.PASSED,
                            context,
                            "Subnet configuration matches data model",
                            0.0,
                        )
                    )
            
            return results
```

**Benefit:** One API call per bridge domain instead of one per subnet. For a BD with 10 subnets, this reduces API calls from 10 to 1.

## 6. Data Model Access

### 6.1 How --data Works

1. All `--data` YAML files are deep-merged by `DataMerger` (supports Jinja2 templating and `${VAR}` env var substitution), same as for robot test cases.
2. Merged result is written to `output/merged_data_model_test_variables.yaml` (the file will be removed post execution as it could contain sensitive values)
3. Passed to test subprocesses via env var `MERGED_DATA_MODEL_TEST_VARIABLES_FILEPATH`
4. `NACTestBase.setup()` loads it into `self.data_model`

### 6.2 Accessing the Data Model

The merged data model is available as `self.data_model` — a Python dict containing the entire merged YAML structure.

**Use JMESPath for extraction:**

```python
import jmespath

# Extract all tenants
tenants = jmespath.search("apic.tenants[?name]", self.data_model) or []

# Extract edge devices
devices = jmespath.search("sdwan.devices[?role=='edge']", self.data_model) or []

# Extract a single value
site_id = jmespath.search("sdwan.site_id", self.data_model)
```

**Always append `or []` for list queries:**

```python
# Good - handles None gracefully
tenants = jmespath.search("apic.tenants[?name]", self.data_model) or []

# Bad - will fail if path doesn't exist
tenants = jmespath.search("apic.tenants[?name]", self.data_model)
for tenant in tenants:  # TypeError if tenants is None
    ...
```

**Check `None` explicitly for single values:**

```python
site_id = jmespath.search("sdwan.site_id", self.data_model)
if site_id is None:
    self.logger.warning("Site ID not found in data model")
    return []
```

### 6.3 Defaults Resolution

The framework provides a helper for reading default values from the `defaults` block in the data model:

```python
self.get_default_value(*paths, required=True)
```

**Key points:**
- The framework auto-prepends the architecture prefix (`defaults.apic.`, `defaults.sdwan.`, etc.)
- Pass RELATIVE paths only
- Set `required=False` for optional defaults

**Examples:**

```python
# Good - relative path
arp_flooding = self.get_default_value("tenants.bridge_domains.arp_flooding")

# Bad - absolute path (will fail)
arp_flooding = self.get_default_value("defaults.apic.tenants.bridge_domains.arp_flooding")

# Optional default with fallback
unicast_routing = self.get_default_value(
    "tenants.bridge_domains.unicast_routing",
    required=False,
) or True
```

### 6.4 SSH/D2D Device Context

For SSH/D2D tests, the framework automatically sets device-specific attributes:

- `self.hostname` — current device hostname (set automatically)
- `self.device_data` — device metadata dict from the data model (includes `device_id`, `system_ip`, etc.)

**Example:**

```python
class VerifyOSPFNeighbors(IOSXETestBase):
    async def verify_item(self, semaphore, client, context):
        async with semaphore:
            # self.hostname is automatically set to the current device
            context["display_context"] = f"OSPF Neighbors -> {self.hostname}"
            
            # self.device_data contains device metadata from data model
            self.logger.info(f"Verifying device {self.device_data.get('device_id')}")
            
            output = await self.execute_command("show ip ospf neighbor")
            # ... verification logic
```

## 7. Reporting Pass/Fail and Logging

### 7.1 ResultStatus Enum

All verification results use the `ResultStatus` enum:

```python
from nac_test.pyats_core.reporting.types import ResultStatus

ResultStatus.PASSED   # Verification succeeded
ResultStatus.FAILED   # Verification found a mismatch
ResultStatus.SKIPPED  # Item not applicable (e.g., feature not configured)
ResultStatus.ERRORED  # Infrastructure error (API unreachable, etc.)
ResultStatus.INFO     # Informational result (no pass/fail judgment)
```

### 7.2 Returning Results

Every `verify_item()` must return a result dict. Use these helper methods:

#### `format_verification_result()`

Primary result builder for all verification outcomes:

```python
self.format_verification_result(
    status: ResultStatus,
    context: dict[str, Any],
    reason: str,
    api_duration: float,
) -> dict[str, Any]
```

- `status`: One of the `ResultStatus` enum values
- `context`: The context dict (must have `display_context` set)
- `reason`: Markdown-formatted string explaining what happened
- `api_duration`: API call duration in seconds (float)

**Example:**

```python
return self.format_verification_result(
    ResultStatus.PASSED,
    context,
    f"All {len(neighbors)} BGP neighbors are established",
    api_duration,
)
```

#### `format_mismatch()`

Convenience method for attribute mismatches. Returns a FAILED result with a standardized message using `TEST_CONFIG["attribute_names"]` for human-readable names.

```python
self.format_mismatch(
    attribute: str,   # API attribute key (e.g., "scope")
    expected: Any,    # Expected value from data model
    actual: Any,      # Actual value from API response
    context: dict[str, Any],
) -> BaseVerificationResultOptional
```

**Example:**

```python
if actual_scope != expected_scope:
    context["api_duration"] = api_duration  # optional — improves report detail
    return self.format_mismatch("scope", expected_scope, actual_scope, context)
```

#### `format_not_found()`

Resource not found in API response. Returns a FAILED result.

```python
self.format_not_found(
    resource_type: str,   # Human-readable resource name (e.g., "Subnet")
    identifier: str,      # Identifier that was searched for
    context: dict[str, Any],
) -> BaseVerificationResultOptional
```

**Example:**

```python
if not subnets:
    return self.format_not_found("Subnet", context["subnet_ip"], context)
```

#### `format_api_error()`

HTTP error response. Returns a FAILED result.

```python
self.format_api_error(
    status_code: int,
    url: str,
    context: dict[str, Any],
) -> BaseVerificationResultOptional
```

**Example:**

```python
if response.status_code != 200:
    context["api_duration"] = api_duration  # optional
    return self.format_api_error(response.status_code, str(response.url), context)
```

### 7.3 display_context, api_context, and test_context

The HTML report needs three pieces of information from each `verify_item()` call. They have similar names but serve distinct purposes:

| What the report needs | How you provide it | Effect if omitted |
|---|---|---|
| What item this result is about | `context["display_context"] = "BGP -> edge-01"` | Item label appears blank |
| Which HTTP call / CLI command produced the evidence | `context["api_context"] = api_context` | Call output appears in a separate "Commands Without Matching Results" section instead of nested under the result |
| What to name the output block | `test_context=api_context` (to `client.get()`) or `with self.test_context(api_context):` (D2D — see [§10.3](#103-test_context)) | Output block has no label |

In practice, `api_context` is the same string used for both the last two — you build it once with `build_api_context()` and use it in both places.

### 7.4 display_context

Set `context["display_context"]` before returning any result. This string appears in the HTML report as the item label.

**Examples:**

```python
# Simple hostname
context["display_context"] = f"BGP -> {hostname}"

# Hierarchical path
context["display_context"] = f"{tenant} -> {bd} -> {subnet_ip}"

# With additional identifiers
context["display_context"] = f"BGP -> {hostname} ({system_ip})"
```

### 7.5 api_context

Use `self.build_api_context()` to create context strings that link HTTP responses to result entries in the HTML report. The context string must be stored in `context["api_context"]` **and** passed to `client.get()` — both are required for the link to appear:

```python
api_context = self.build_api_context(
    "BGP Neighbors",
    f"Device {hostname} ({system_ip})",
    hostname=hostname,
    system_ip=system_ip,
)
context["api_context"] = api_context          # ← links result to HTTP call in report
response = await client.get(
    f"{self.TEST_CONFIG['api_endpoint']}?deviceId={system_ip}",
    test_context=api_context,                  # ← tags the HTTP call
)
```

When both are set, the HTTP response appears nested and expandable directly under the result entry in the report. Without `context["api_context"]`, the HTTP call still appears in the report but in a separate "Commands Without Matching Results" section.

**Multiple API calls per verify_item:** only one call can be the primary (linked to the result). Set `context["api_context"]` to the context of the most important call. Other calls will appear in "Commands Without Matching Results" — they are still visible and labelled.

**D2D tests:** the same `context["api_context"]` pattern works for SSH tests too. Set it after the command executes (after the `with self.test_context()` block exits) to link the command output to the result entry:

```python
api_context = self.build_api_context(self.TEST_CONFIG["resource_type"], self.hostname)
with self.test_context(api_context):
    output = await self.execute_command("show ip ospf neighbor")

context["api_context"] = api_context  # links command to result — works for D2D too
```

For multiple commands per `verify_item`, set `context["api_context"]` to the primary command's context. Secondary commands will still appear in "Commands Without Matching Results".

### 7.6 Logging

Use `self.logger` for progress and diagnostic logging:

```python
# Info - normal progress
self.logger.info(f"Found {len(items)} items to verify")

# Warning - unexpected but non-fatal
self.logger.warning(f"No subnets configured for BD {bd_name}")

# Error - infrastructure errors only (not test failures)
self.logger.error(f"Failed to parse command output: {e}")
```

**Important:** Use `self.logger.error()` for infrastructure errors only, not for test failures. Test failures go through `format_verification_result()` with `ResultStatus.FAILED`.

## 8. TEST_CONFIG and Module Constants

### 8.1 TEST_CONFIG

Class-level dict that configures framework behavior. Minimum required:

```python
TEST_CONFIG = {
    "resource_type": "SD-WAN BGP Neighbors",  # Required: HTML report grouping
    "api_endpoint": "/dataservice/device/bgp/neighbors",  # Required: REST path or CLI command
}
```

### 8.2 Optional TEST_CONFIG Keys

| Key | Type | Purpose | Example |
|-----|------|---------|---------|
| `identifier_format` | str | Python format string for item identification in reports. Uses context dict keys. | `"{tenant}:{bd}:{subnet_ip}"` |
| `step_name_format` | str | Format string for PyATS step names | `"Verify {hostname} BGP neighbors"` |
| `expected_values` | dict | Static expected values (NRFU pattern) | `{"state": "up", "admin_state": "enabled"}` |
| `log_fields` | list[str] | Context keys to include in log output | `["hostname", "system_ip", "peer_ip"]` |
| `attribute_names` | dict | Maps API attribute keys to human-readable names for `format_mismatch()` | `{"ip": "Subnet IP", "scope": "Subnet Scope"}` |
| `schema_paths` | dict | Documents data model paths (for reporting) | `{"items": "apic.tenants[].bridge_domains[].subnets[]"}` |

**Complete example:**

```python
TEST_CONFIG = {
    "resource_type": "ACI Bridge Domain Subnets",
    "api_endpoint": "/api/node/class/fvSubnet.json",
    "identifier_format": "{tenant}:{bd}:{subnet_ip}",
    "step_name_format": "Verify subnet {subnet_ip} in {bd}",
    "log_fields": ["tenant", "bd", "subnet_ip"],
    "attribute_names": {
        "ip": "Subnet IP",
        "scope": "Subnet Scope",
        "preferred": "Preferred",
        "ctrl": "Control",
    },
    "schema_paths": {
        "items": "apic.tenants[].bridge_domains[].subnets[]",
        "defaults": "defaults.apic.tenants.bridge_domains.subnets",
    },
}
```

### 8.3 Module-Level Constants (Strongly Recommended)

These constants feed into HTML report generation. Without them, reports work but lack descriptive metadata.

```python
TITLE = "Verify SD-WAN BGP Neighbor State"

DESCRIPTION = """
Verifies that all BGP neighbors on SD-WAN edge devices are in the established state.
This test queries the SD-WAN Manager API for BGP neighbor status on each device and
confirms operational readiness of BGP routing.
"""

SETUP = """
* SD-WAN Manager API must be reachable
* Edge devices must be online and reachable by the manager
* BGP must be configured on the devices
"""

PROCEDURE = """
* Query SD-WAN Manager API for BGP neighbors on each device
* Parse the response to extract neighbor state
* Verify all neighbors are in "established" state
* Report any neighbors in other states as failures
"""

PASS_FAIL_CRITERIA = """
* PASS: All BGP neighbors are in "established" state
* FAIL: One or more BGP neighbors are not in "established" state
* SKIP: No BGP neighbors configured on the device
* ERROR: API unreachable or returned an error response
"""
```

**Why these matter:**
- `TITLE`: Appears in HTML report headers and navigation
- `DESCRIPTION`: Explains the test purpose to readers
- `SETUP`: Documents prerequisites for the test
- `PROCEDURE`: Step-by-step verification logic
- `PASS_FAIL_CRITERIA`: Clear success/failure definitions

## 9. API Caching

The framework provides two caching layers automatically:

### 9.1 Authentication Token Caching

- File-based, cross-process safe (file locking)
- Stored in temp directory, keyed by SHA256 of controller URL
- First test authenticates; subsequent tests reuse the cached token until expiry
- Automatic — you don't interact with this directly

**How it works:**
1. First test process authenticates and writes token to cache file
2. Subsequent test processes read from cache file (with file locking)
3. If token is expired, the process re-authenticates and updates the cache
4. All processes share the same cache, reducing authentication overhead

### 9.2 HTTP Connection Pooling

- `self.pool` provides shared `httpx.AsyncClient` instances
- Architecture base classes create the client in `run_async_verification_test()` (deferred from setup for macOS fork safety)
- You receive the client as the second argument to `verify_item()` — just use it

**Example:**

```python
async def verify_item(self, semaphore, client, context):
    async with semaphore:
        # client is a shared httpx.AsyncClient with connection pooling
        response = await client.get(f"{self.TEST_CONFIG['api_endpoint']}")
        # ... verification logic
```

**Benefits:**
- Connection reuse across multiple API calls
- Automatic retry logic for transient failures
- Request/response logging for debugging

## 10. SSH/CLI via the Connection Broker

For SSH/D2D tests (IOSXETestBase and subclasses), the framework provides CLI execution through a Connection Broker — a daemon process that manages shared SSH connections so all tests executed against a given device can share the same SSH session, commands are being multiplexed through it.

### 10.1 execute_command()

Execute a CLI command on the current device:

```python
output = await self.execute_command("show ip ospf neighbor")
```

**Key points:**
- Async function (always `await` it)
- Checks the command cache first (1-hour TTL per device)
- If not cached, executes via the broker's shared connection pool
- Returns raw CLI output as a string

**Example:**

```python
async def verify_item(self, semaphore, client, context):
    async with semaphore:
        context["display_context"] = f"OSPF Neighbors -> {self.hostname}"
        
        # Execute command
        output = await self.execute_command("show ip ospf neighbor")
        
        if not output:
            return self.format_verification_result(
                ResultStatus.ERRORED,
                context,
                "Failed to execute command",
                0.0,
            )
        
        # Parse and verify output
        # ...
```

### 10.2 parse_output()

Convert CLI output to structured data using Genie parsers:

```python
# nac-test 2.0 (current): synchronous
parsed = self.parse_output("show ip ospf neighbor", output=output)

# nac-test 2.1+ (upcoming): asynchronous
parsed = await self.parse_output("show ip ospf neighbor", output=output)
```

**Key points:**
- Uses Genie parsers to convert CLI output into structured data (dict)
- Returns `None` if no parser is available for the command
- **Important:** This is synchronous in nac-test 2.0 but will become async in nac-test 2.1 to support certain genie parsers which execute additional commands to go via nac-test's SSH Connection Broker.

**Example:**

```python
output = await self.execute_command("show ip ospf neighbor")

# Current (nac-test 2.0)
parsed = self.parse_output("show ip ospf neighbor", output=output)

# Future (nac-test 2.1+)
# parsed = await self.parse_output("show ip ospf neighbor", output=output)

if parsed is None:
    # No parser available - fall back to regex or string parsing
    self.logger.warning("No Genie parser available for command")
    # ... manual parsing
else:
    # Use structured data
    neighbors = parsed.get("interfaces", {})
    # ... verification logic
```

### 10.3 test_context()

Labels a command output block in the HTML report, and — together with `context["api_context"]` (explained in [§7](#7-reporting-passfail-and-logging)) — links it to the result entry. Use the same string for both:

```python
api_context = self.build_api_context("OSPF Neighbors", self.hostname)
with self.test_context(api_context):           # ← labels the output block
    output = await self.execute_command("show ip ospf neighbor")

context["api_context"] = api_context           # ← links block to result entry
```

Multiple sequential `test_context` blocks per `verify_item()` are supported — each labels its own command output independently:

```python
ctx_a = self.build_api_context("Control Connections", self.hostname)
with self.test_context(ctx_a):
    output_a = await self.execute_command("show sdwan control connections")

ctx_b = self.build_api_context("SD-WAN Version", self.hostname)
with self.test_context(ctx_b):
    output_b = await self.execute_command("show sdwan version")

# Link the primary command to the result; ctx_b will appear unlinked
context["api_context"] = ctx_a
```

**Note:** `test_context()` labels the command output block. `context["api_context"]` links it to the result entry. Both are needed. For a single command, use the same string for both. For multiple commands, only one can be linked to the result — set `context["api_context"]` to whichever command is the primary evidence for the pass/fail decision.

### 10.4 Command Caching

The Connection Broker provides automatic command caching:

- **Per-device:** Each device has its own cache
- **Keyed by command string:** Exact command match required
- **1-hour TTL:** Cache entries expire after 1 hour
- **Shared across ALL test subprocesses:** Not per-process

**Benefits:**
- 10 tests each running `show version` on 50 devices = 50 SSH commands instead of 500
- Faster test execution
- Reduced load on network devices

**Cache behavior:**
- First test to execute a command on a device populates the cache
- Subsequent tests (even in different subprocesses) read from cache
- Cache is transparent — you don't need to manage it

### 10.5 Broker Architecture (for understanding, not interaction)

The Connection Broker is a daemon process that:
- Listens on a Unix domain socket
- Manages a shared SSH connection pool with per-device locks
- Monitors connection health (auto-reconnects unhealthy connections)
- Provides command caching across all test processes

**You interact via `execute_command()` only** — no direct broker interaction needed.

## 11. Concurrency

Section 1 introduced concurrency at a high level. This section details what you must do in your test code.

### 11.1 API Tests

- All run in a single process with a shared event loop
- `verify_item()` calls run concurrently, controlled by the semaphore
- Always start `verify_item()` with `async with semaphore:`
- The semaphore limit is set by the framework (typically 10-20 concurrent verifications)

**Example:**

```python
async def verify_item(self, semaphore, client, context):
    async with semaphore:  # Acquire semaphore slot
        # This code runs concurrently with other verify_item() calls
        # up to the semaphore limit
        response = await client.get(...)
        # ... verification logic
    # Semaphore released automatically when exiting the block
```

### 11.2 D2D Tests

- Each device runs in its own subprocess
- Multiple devices run in parallel (up to `--max-parallel-devices` or 20)
- Within a device, `verify_item()` calls are sequential (one SSH session per device)

**Example:**

```python
# Device 1 subprocess
async def verify_item(self, semaphore, client, context):
    async with semaphore:
        # This runs sequentially within this device
        # but in parallel with other devices
        output = await self.execute_command("show version")
        # ... verification logic
```

### 11.3 What You Must Do

Always wrap the body of `verify_item()` in `async with semaphore:`:

```python
async def verify_item(self, semaphore, client, context):
    async with semaphore:  # REQUIRED
        # Your verification logic here
        ...
```

**Why this matters:**
- The semaphore controls concurrency across all tests
- Without it, you'll overwhelm the controller/devices with concurrent requests
- The framework sets appropriate limits based on the architecture

## 12. Tag Filtering

Add a `groups` class attribute to enable `--include`/`--exclude` tag filtering:

```python
class VerifyBGPRoutes(APICTestBase):
    groups = ["bgp", "routing"]
    
    TEST_CONFIG = {
        "resource_type": "BGP Routes",
        "api_endpoint": "/api/node/class/bgpRoute.json",
    }
    
    # ... rest of test implementation
```

**Usage:**

```bash
# Run only tests with "bgp" in groups
nac-test --pyats --include bgp ...

# Skip tests with "routing" in groups
nac-test --pyats --exclude routing ...

# Combine multiple tags
nac-test --pyats --include bgp,ospf --exclude slow ...
```

**Key points:**
- Tests without `groups` always run (not affected by filtering)
- Multiple tags can be specified (comma-separated)
- Tags are case-sensitive
- Use tags to organize tests by feature, protocol, or execution time
- The tag semantic is identical to robot's tag filtering

## Next Steps

Now that you understand the framework architecture and patterns, you're ready to write your first test:

1. **Choose your base class** from the Layer 2 architecture adapters (Section 2)
2. **Implement the three-method contract** (Section 3)
3. **Choose your test pattern** — NRFU or data-model-driven (Section 4)
4. **Add TEST_CONFIG and module constants** (Section 8)
5. **Test locally** with `nac-test --pyats --include your-tag`

**Additional resources:**
- See `api-reference.md` for complete method signatures
- See `QUICKSTART.md` for a copy-paste minimal example
- See `workspace/doc-examples/` for fully runnable examples against mock infrastructure (`./run_examples.sh`)

**Common development workflow:**

```bash
# 1. Create your test file
vim verify_my_feature.py

# 2. Add tag filtering to your test class so you can run just this test:
#    class VerifyMyFeature(SDWANManagerTestBase):
#        groups = ["my-feature"]
#        ...

# 3. Run just your test during development
nac-test --pyats --include my-feature \
  --data data/my-test-data.yaml \
  --templates path/to/tests/ \
  --output /tmp/nac-test-output

# 4. Check the HTML report
open /tmp/nac-test-output/report.html

# 5. Iterate until passing
# ... edit test ...
nac-test --pyats --include my-feature \
  --data data/my-test-data.yaml \
  --templates path/to/tests/ \
  --output /tmp/nac-test-output

# 6. Run full test suite
nac-test --pyats \
  --data data/full-data.yaml \
  --templates path/to/tests/ \
  --output /tmp/nac-test-output
```

**Debugging tips:**
- Use `self.logger.info()` liberally during development
- Use `--verbose` flag to see detailed PyATS execution logs
- For SSH tests, check Connection Broker logs in `output/connection_broker.log`
- HTML report shows API call timing and response codes for performance analysis

## 13. Static Analysis and Type Checking

Test files live outside the nac-test core repo, so they don't benefit from the project's own pre-commit hooks. Setting up static analysis in your own test repo catches common mistakes — including async/await errors — before you even run nac-test.

### 13.1 What Static Analysis Catches

The most common mistakes for test writers:

| Mistake | Example | Detected by |
|---------|---------|-------------|
| Missing `await` on async call | `output = self.execute_command(cmd)` | mypy — `Coroutine[...] assigned to str` |
| Awaiting a sync function | `result = await self.parse_output(...)` in nac-test 2.0 | mypy — `Incompatible types in await` |
| Wrong return type from `verify_item` | Returning `None` instead of a result dict | mypy |
| Unused imports, undefined variables | — | ruff (flake8/pyflakes rules) |
| Style inconsistencies | Line length, import order | ruff (format) |

mypy detection requires type annotations on local variables. Without them, mypy infers `Any` and silently passes. The key annotation is on the output of async calls:

```python
# mypy catches this because output is annotated str but execute_command returns Coroutine
output: str = self.execute_command("show version")   # Error: Maybe you forgot await?

# mypy catches this too — parse_output returns dict|None, not Awaitable
parsed: dict = await self.parse_output("show version", output=output)  # Error in 2.0
```

### 13.2 Recommended Pre-commit Setup

Install pre-commit in your test repo:

```bash
pip install pre-commit ruff mypy
```

Create `.pre-commit-config.yaml` in your repo root:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0
    hooks:
      - id: mypy
        additional_dependencies:
          - nac-test
          - nac-test-pyats-common
          - pyats
```

```bash
pre-commit install   # runs on every git commit
pre-commit run --all-files  # run manually against all files
```

### 13.3 Recommended mypy Configuration

Create `pyproject.toml` (or `mypy.ini`) in your test repo matching the nac-test project's own settings:

```toml
[tool.mypy]
python_version = "3.10"
check_untyped_defs = true
disallow_incomplete_defs = true
disallow_untyped_defs = true
ignore_missing_imports = true
strict_optional = true
warn_return_any = true
warn_unreachable = true
show_error_context = true
```

### 13.4 Recommended ruff Configuration

```toml
[tool.ruff]
target-version = "py310"
line-length = 88

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes (undefined names, unused imports)
    "I",   # isort
    "B",   # flake8-bugbear (likely bugs)
    "UP",  # pyupgrade (modern Python syntax)
]
```

### 13.5 The parse_output() Migration (nac-test 2.0 → 2.1)

`parse_output()` changes from synchronous to asynchronous in nac-test 2.1. mypy will catch code written for the wrong version:

```python
# nac-test 2.0 — sync. Accidentally adding await is caught by mypy:
parsed = self.parse_output(command, output=output)        # correct
parsed = await self.parse_output(command, output=output)  # mypy error: not Awaitable

# nac-test 2.1+ — async. Forgetting await is caught by mypy:
parsed = await self.parse_output(command, output=output)  # correct
parsed = self.parse_output(command, output=output)        # mypy error: Coroutine assigned
```

Run `mypy` against your test files after upgrading nac-test to catch any `parse_output` calls that need updating:

```bash
mypy verify_my_test.py
```
