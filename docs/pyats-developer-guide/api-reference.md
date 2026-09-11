# nac-test's PyATS Test Framework API Reference

This reference documents the methods, properties, and types that test case writers interact with. It covers the developer-facing surface — methods you call or override — not internal framework methods.

## NACTestBase

Base class for all pyATS test cases. Inherits from `pyats.aetest.Testcase`.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `self.data_model` | `dict[str, Any]` | Merged YAML data model loaded from all `--data` files. Available after `setup()`. |
| `self.logger` | `logging.Logger` | Logger scoped to the test module (`logging.getLogger(cls.__module__)`). |
| `self.controller_type` | `str` | Auto-detected controller type: `"ACI"`, `"SDWAN"`, `"CC"`, `"IOSXE"`, etc. |
| `self.controller_url` | `str` | Controller URL from env var (e.g., `ACI_URL`, `SDWAN_URL`, `CC_URL`). |
| `self.result_collector` | `TestResultCollector` | Collects results for HTML reporting. Typically used via helper methods. |

### Methods to Override

#### `get_items_to_verify() -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]`

Returns items to verify. Called once before verification begins.

- **List return** (standard): Each dict is a context for one `verify_item()` call.
- **Dict return** (grouped): Keys are group identifiers, values are lists of contexts. Triggers `verify_group()` instead.
- Must NOT execute device commands (SSH not established at this point).

**Example:**

```python
def get_items_to_verify(self) -> list[dict[str, Any]]:
    devices = self.data_model.get("devices", [])
    return [{"hostname": d["hostname"], "site_id": d["site_id"]} for d in devices]
```

#### `async verify_item(semaphore: asyncio.Semaphore, client: Any, context: dict[str, Any]) -> dict[str, Any]`

Verify a single item. Called once per context dict from `get_items_to_verify()`.

- `semaphore`: Wrap body in `async with semaphore:` for concurrency control.
- `client`: HTTP client for API tests, or `None` for SSH tests.
- `context`: One dict from `get_items_to_verify()`. Set `context["display_context"]` before returning.
- Returns: Result dict from `format_verification_result()` or similar helper.

**Example:**

```python
async def verify_item(self, semaphore, client, context):
    async with semaphore:
        hostname = context["hostname"]
        api_context = self.build_api_context("Device Status", hostname)
        
        response = await client.get(f"/api/devices/{hostname}", test_context=api_context)
        
        if response.status_code != 200:
            return self.format_api_error(response.status_code, response.url, context)
        
        data = response.json()
        context["display_context"] = hostname
        
        if data["status"] != "reachable":
            return self.format_mismatch("status", "reachable", data["status"], context)
        
        return self.format_verification_result(ResultStatus.PASSED, context, "Device is reachable")
```

#### `async verify_group(semaphore: asyncio.Semaphore, client: Any, group_key: str, contexts: list[dict[str, Any]]) -> list[dict[str, Any]]`

Verify a group of related items (1:N pattern). Called when `get_items_to_verify()` returns a dict.

- `group_key`: The dict key identifying this group.
- `contexts`: All context dicts for this group.
- Returns: List of result dicts.

**Example:**

```python
def get_items_to_verify(self) -> dict[str, list[dict[str, Any]]]:
    # Group by site_id
    sites = {}
    for device in self.data_model.get("devices", []):
        site_id = device["site_id"]
        sites.setdefault(site_id, []).append({"hostname": device["hostname"]})
    return sites

async def verify_group(self, semaphore, client, group_key, contexts):
    async with semaphore:
        # Fetch all devices for this site in one API call
        response = await client.get(f"/api/sites/{group_key}/devices")
        devices = response.json()
        
        results = []
        for context in contexts:
            # Verify each device from the batch response
            device = next((d for d in devices if d["hostname"] == context["hostname"]), None)
            if device:
                results.append(self.format_verification_result(
                    ResultStatus.PASSED, context, "Device found in site"
                ))
            else:
                results.append(self.format_not_found("Device", context["hostname"], context))
        return results
```

### Methods to Call

#### `run_async_verification_test(steps: aetest.Steps) -> None`

Entry point for async verification. Call from your `@aetest.test` method:

```python
@aetest.test
def test_something(self, steps):
    self.run_async_verification_test(steps)
```

Creates an event loop, creates the HTTP client (API tests), calls `get_items_to_verify()`, dispatches `verify_item()` or `verify_group()`, and processes results.

#### `format_verification_result(status: ResultStatus, context: dict, reason: str, api_duration: float = 0) -> BaseVerificationResultOptional`

Build a standardized result dict.

- `status`: `ResultStatus.PASSED`, `.FAILED`, `.SKIPPED`, `.ERRORED`, or `.INFO`.
- `context`: The context dict (should have `display_context` set).
- `reason`: Markdown-formatted string explaining the result.
- `api_duration`: API call duration in seconds (float).

**Example:**

```python
return self.format_verification_result(
    ResultStatus.PASSED,
    context,
    "BGP neighbor is established",
    api_duration=0.234
)
```

#### `format_mismatch(attribute: str, expected: Any, actual: Any, context: dict) -> BaseVerificationResultOptional`

Convenience for attribute mismatches. Returns a FAILED result. Uses `TEST_CONFIG["attribute_names"]` for human-readable attribute names if available. Reads `api_duration` from `context["api_duration"]` if set.

**Example:**

```python
if actual_scope != expected_scope:
    context["api_duration"] = api_duration  # optional, improves report detail
    return self.format_mismatch("scope", expected_scope, actual_scope, context)
```

#### `format_not_found(resource_type: str, identifier: str, context: dict) -> BaseVerificationResultOptional`

Convenience for resources not found in API response. Returns a FAILED result.

**Example:**

```python
if not subnets:
    return self.format_not_found("Subnet", context["subnet_ip"], context)
```

#### `format_api_error(status_code: int, url: str, context: dict) -> BaseVerificationResultOptional`

Convenience for HTTP error responses. Returns a FAILED result. Reads `api_duration` from `context["api_duration"]` if set.

**Example:**

```python
if response.status_code != 200:
    context["api_duration"] = api_duration  # optional
    return self.format_api_error(response.status_code, str(response.url), context)
```

#### `build_api_context(resource_type: str, identifier: str, **kwargs) -> str`

Build a context string for linking HTTP responses or SSH command output to result entries in the HTML report. Requires two steps — both are necessary for the link to appear:

1. Store the string in `context["api_context"]` — tells `process_results_smart` which context to attach to the result.
2. **API tests:** pass it to `client.get(..., test_context=api_context)` — tags the HTTP call.  
   **D2D tests:** pass it to `self.test_context(api_context)` — labels the command output block.

```python
api_context = self.build_api_context("BGP Neighbors", f"Device {hostname}")
context["api_context"] = api_context          # required for report linking
response = await client.get(url, test_context=api_context)
```

When both are set, the HTTP response appears nested and expandable under the result entry. Without `context["api_context"]`, the call lands in "Commands Without Matching Results".

For multiple API calls per `verify_item`, set `context["api_context"]` to the primary call's context only. Secondary calls remain in "Commands Without Matching Results".

#### `get_default_value(*paths: str, required: bool = True) -> Any`

Read a value from the `defaults` block in the data model.

- Paths are RELATIVE — the framework auto-prepends the architecture prefix (`defaults.apic.`, `defaults.sdwan.`, etc.).
- `required=True` (default): raises if path not found.
- `required=False`: returns `None` if path not found.

**Example:**

```python
# Resolves to: self.data_model["defaults"]["apic"]["tenants"]["bridge_domains"]["arp_flooding"]
arp_flooding = self.get_default_value("tenants.bridge_domains.arp_flooding")

# Optional value
mtu = self.get_default_value("interfaces.mtu", required=False)
if mtu is None:
    mtu = 1500  # fallback
```

#### `build_identifier(context: dict) -> str`

Format an identifier string using `TEST_CONFIG["identifier_format"]` and the context dict.

**Example:**

```python
# With TEST_CONFIG["identifier_format"] = "Device {hostname} ({system_ip})"
identifier = self.build_identifier({"hostname": "edge01", "system_ip": "10.1.1.1"})
# Returns: "Device edge01 (10.1.1.1)"
```

#### `categorize_results(results: list[dict]) -> tuple[list, list, list]`

Split results into `(failed, skipped, passed)` lists.

**Example:**

```python
failed, skipped, passed = self.categorize_results(all_results)
self.logger.info(f"Passed: {len(passed)}, Failed: {len(failed)}, Skipped: {len(skipped)}")
```

#### `test_context(api_context: str) -> ContextManager`

Labels a command output block in the HTML report. Use together with `context["api_context"]` to link the command to the result entry.

```python
api_context = self.build_api_context("OSPF Neighbors", self.hostname)
with self.test_context(api_context):
    output = await self.execute_command("show ip ospf neighbor")

# Set after the with block — links command output to this result in the report
context["api_context"] = api_context
```

Works identically for both API and D2D tests. For multiple commands per `verify_item`, only one can be the primary:

```python
with self.test_context(self.build_api_context("Control Connections", self.hostname)):
    output_a = await self.execute_command("show sdwan control connections")

with self.test_context(self.build_api_context("SD-WAN Version", self.hostname)):
    output_b = await self.execute_command("show sdwan version")

context["api_context"] = self.build_api_context("Control Connections", self.hostname)
# output_b appears in "Commands Without Matching Results" — expected
```

#### `wrap_client_for_tracking(client: Any, device_name: str) -> Any`

Wrap an HTTP client for API call tracking in reports. Called by architecture base classes — you typically don't call this directly.

#### `api_call_with_retry(func: Callable, *args, **kwargs) -> Any`

Retry wrapper using `SmartRetry`. Handles transient API failures.

**Example:**

```python
response = await self.api_call_with_retry(
    client.get,
    endpoint,
    test_context=api_context
)
```

## SSHTestBase

Extends `NACTestBase` for SSH/D2D (device-to-device) tests.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `self.hostname` | `str` | Current device hostname (extracted from env var `DEVICE_INFO` JSON blob). |
| `self.device_info` | `dict` | Device metadata from the data model (from env var `DEVICE_INFO`). |
| `self.device_data` | `dict` | Alias for `self.device_info`. |

### Methods

#### `await self.execute_command(command: str) -> str`

Execute a CLI command on the current device.

- Checks the command cache first (1-hour TTL per device).
- If not cached, executes via the Connection Broker's shared SSH pool.
- Returns raw CLI output as a string.
- Always `await` this call.

**Example:**

```python
output = await self.execute_command("show ip ospf neighbor")
if "FULL" in output:
    self.logger.info("OSPF neighbor is up")
```

#### `self.parse_output(command: str, output: str = None) -> dict | None` (nac-test 2.0)
#### `await self.parse_output(command: str, output: str = None) -> dict | None` (nac-test 2.1+)

Parse CLI output using Genie parsers.

- Returns structured data as a dict, or `None` if no parser available.
- **nac-test 2.0:** Synchronous — call without `await`.
- **nac-test 2.1+:** Asynchronous — must `await`.

**Example (nac-test 2.0):**

```python
output = await self.execute_command("show ip ospf neighbor")
parsed = self.parse_output("show ip ospf neighbor", output=output)
if parsed:
    neighbors = parsed.get("interfaces", {})
```

**Example (nac-test 2.1+):**

```python
output = await self.execute_command("show ip ospf neighbor")
parsed = await self.parse_output("show ip ospf neighbor", output=output)
if parsed:
    neighbors = parsed.get("interfaces", {})
```

## Architecture-Specific Base Classes

### APICTestBase

Extends `NACTestBase` for ACI/APIC API tests.

- **Auth:** Cookie-based authentication (600s TTL), auto-cached.
- **Client:** `httpx.AsyncClient` with APIC base URL and auth cookies.
- **Import:** `from nac_test_pyats_common.aci import APICTestBase`

No additional methods beyond NACTestBase. The base class handles APIC authentication and client creation.

**Example:**

```python
from nac_test_pyats_common.aci.test_base import APICTestBase
from pyats import aetest

class VerifyTenants(APICTestBase):
    @aetest.test
    def test_tenants(self, steps):
        self.run_async_verification_test(steps)
    
    def get_items_to_verify(self):
        import jmespath
        tenants = jmespath.search("apic.tenants[?name]", self.data_model) or []
        return [{"tenant": t["name"]} for t in tenants]
    
    async def verify_item(self, semaphore, client, context):
        async with semaphore:
            tenant = context["tenant"]
            response = await client.get(f"/api/node/mo/uni/tn-{tenant}.json")
            # ... verification logic
```

### SDWANManagerTestBase

Extends `NACTestBase` for SD-WAN Manager (vManage) API tests.

- **Auth:** Dual-mode (JWT token + session), auto-cached.
- **Client:** `httpx.AsyncClient` with vManage base URL and auth headers.
- **Import:** `from nac_test_pyats_common.sdwan import SDWANManagerTestBase`

#### Additional Methods

##### `get_devices_from_data_model() -> list[dict]`

Convenience method to extract devices from the SD-WAN data model. Returns list of dicts with `system_ip`, `site_id`, `hostname` keys.

> **Note:** This method is from nac-test-pyats-common PR #37, which is still in review. It will become available once that PR is merged.

**Example:**

```python
def get_items_to_verify(self):
    devices = self.get_devices_from_data_model()
    return [{"system_ip": d["system_ip"], "hostname": d["hostname"]} for d in devices]
```

### CatalystCenterTestBase

Extends `NACTestBase` for Catalyst Center API tests.

- **Auth:** Basic Auth → X-Auth-Token exchange (3600s TTL), auto-cached.
- **Client:** `httpx.AsyncClient` with CC base URL and auth token.
- **Import:** `from nac_test_pyats_common.catc import CatalystCenterTestBase`

No additional methods beyond NACTestBase.

**Example:**

```python
from nac_test_pyats_common.catc.api_test_base import CatalystCenterTestBase
from pyats import aetest

class VerifyDeviceHealth(CatalystCenterTestBase):
    @aetest.test
    def test_device_health(self, steps):
        self.run_async_verification_test(steps)
```

### IOSXETestBase

Extends `SSHTestBase`. **Controller-agnostic** SSH base — the preferred base class for D2D tests targeting devices managed by SD-WAN or Catalyst Center. Uses a plugin registry to auto-detect the managing architecture at runtime (based on `controller_type` env var) and delegate device inventory resolution to the appropriate resolver.

- **Import:** `from nac_test_pyats_common.iosxe import IOSXETestBase`
- **Device resolution:** Automatic — detects SD-WAN or Catalyst Center via plugin registry.
- **Inherits:** `execute_command()`, `parse_output()`, `self.hostname`, `self.device_data` from `SSHTestBase`.
- **Note:** Standalone IOS-XE (no controller) is not yet supported — device inventory must come from a controller data model.

### SDWANTestBase

Extends `SSHTestBase`. Hard-wired to SD-WAN device inventory (`sdwan.sites[].routers[]`). Use when writing explicitly SD-WAN-specific SSH tests.

- **Import:** `from nac_test_pyats_common.sdwan import SDWANTestBase`

### CatalystCenterSSHTestBase

Extends `SSHTestBase`. Hard-wired to Catalyst Center device inventory (`catalyst_center.inventory.devices[]`). Use when writing explicitly CC-specific SSH tests.

- **Import:** `from nac_test_pyats_common.catc import CatalystCenterSSHTestBase`

**Example:**

```python
from nac_test_pyats_common.iosxe.test_base import IOSXETestBase
from pyats import aetest

class VerifyOSPF(IOSXETestBase):
    @aetest.test
    def test_ospf_neighbors(self, steps):
        self.run_async_verification_test(steps)
    
    async def verify_item(self, semaphore, client, context):
        async with semaphore:
            output = await self.execute_command("show ip ospf neighbor")
            # nac-test 2.0: sync — self.parse_output(...)
            # nac-test 2.1+: async — await self.parse_output(...)
            parsed = self.parse_output("show ip ospf neighbor", output=output)
            # ... verification logic
```

## ResultStatus Enum

```python
from nac_test.pyats_core.reporting.types import ResultStatus
```

| Value | When to Use |
|-------|-------------|
| `ResultStatus.PASSED` | Verification succeeded — actual matches expected |
| `ResultStatus.FAILED` | Verification found a mismatch or error condition |
| `ResultStatus.SKIPPED` | Item not applicable (feature not configured, empty response) |
| `ResultStatus.ERRORED` | Infrastructure error (API unreachable, timeout, exception) |
| `ResultStatus.INFO` | Informational result with no pass/fail judgment |

**Example:**

```python
if not neighbors:
    return self.format_verification_result(
        ResultStatus.SKIPPED,
        context,
        "No BGP neighbors configured"
    )

if neighbor_state == "established":
    return self.format_verification_result(
        ResultStatus.PASSED,
        context,
        "BGP neighbor is established"
    )
else:
    return self.format_verification_result(
        ResultStatus.FAILED,
        context,
        f"BGP neighbor state is {neighbor_state}, expected established"
    )
```

## TEST_CONFIG Keys

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `resource_type` | `str` | Yes | Resource name for HTML report grouping (e.g., "SD-WAN BGP Neighbors") |
| `api_endpoint` | `str` | Yes | REST API path or CLI command string |
| `identifier_format` | `str` | No | Python format string using context dict keys (e.g., `"Device {hostname} ({system_ip})"`) |
| `step_name_format` | `str` | No | Format string for PyATS step names |
| `expected_values` | `dict` | No | Static expected values for NRFU pattern |
| `log_fields` | `list[str]` | No | Context dict keys to include in log output |
| `attribute_names` | `dict[str, str]` | No | Maps API attribute keys to human-readable names for `format_mismatch()` |
| `schema_paths` | `dict` | No | Documents data model paths (for reporting metadata) |

**Example:**

```python
TEST_CONFIG = {
    "resource_type": "SD-WAN BGP Neighbors",
    "api_endpoint": "/dataservice/device/bgp/neighbors",
    "identifier_format": "Device {hostname} - Peer {peer_ip}",
    "step_name_format": "Verify BGP neighbor {peer_ip} on {hostname}",
    "log_fields": ["hostname", "peer_ip", "state"],
    "attribute_names": {
        "state": "BGP State",
        "peer_ip": "Peer IP Address",
        "as_number": "AS Number"
    },
    "schema_paths": {
        "devices": "devices",
        "bgp_neighbors": "devices[].bgp.neighbors"
    }
}
```

## Module-Level Constants

Strongly recommended — these generate descriptive HTML reports.

| Constant | Type | Purpose |
|----------|------|---------|
| `TITLE` | `str` | Short test title (appears in report headers) |
| `DESCRIPTION` | `str` | What this test verifies and why it matters |
| `SETUP` | `str` | Bullet list of prerequisites (markdown) |
| `PROCEDURE` | `str` | Bullet list of verification steps (markdown) |
| `PASS_FAIL_CRITERIA` | `str` | When the test passes, fails, or skips (markdown) |

**Example:**

```python
TITLE = "SD-WAN BGP Neighbor Verification"

DESCRIPTION = """
Verifies that all BGP neighbors defined in the data model are established
and operational on SD-WAN edge devices.
"""

SETUP = """
- SD-WAN Manager API must be reachable
- Edge devices must be online and reachable
- BGP must be configured per the data model
"""

PROCEDURE = """
1. Query SD-WAN Manager API for BGP neighbor status
2. For each neighbor in the data model:
   - Verify neighbor exists in API response
   - Verify neighbor state is "established"
   - Verify routes received > 0
"""

PASS_FAIL_CRITERIA = """
- **Pass:** All BGP neighbors are established and receiving routes
- **Fail:** Any neighbor is not established or not found
- **Skip:** Device has no BGP neighbors configured
"""
```

## Common Patterns

### NRFU Pattern (Static Expected Values)

Verify universal healthy states — expected values are hardcoded, not from the data model:

```python
def get_items_to_verify(self):
    devices = self.get_devices_from_data_model()
    return [{"hostname": d["hostname"], "system_ip": d["system_ip"]} for d in devices]

async def verify_item(self, semaphore, client, context):
    async with semaphore:
        hostname = context["hostname"]
        context["display_context"] = f"Control -> {hostname}"

        response = await client.get(f"/api/devices/{hostname}/status")
        status = response.json().get("reachability")

        if status != "reachable":
            return self.format_verification_result(
                ResultStatus.FAILED, context,
                f"Device is {status}, expected reachable"
            )

        return self.format_verification_result(
            ResultStatus.PASSED, context, "Device is reachable"
        )
```

### Data-Model-Driven Pattern (Expected Values from YAML)

Verify deployed configuration matches the data model — expected values extracted at runtime:

```python
import jmespath

def get_items_to_verify(self):
    tenants = jmespath.search("apic.tenants[?name]", self.data_model) or []
    items = []
    for tenant in tenants:
        for vlan in tenant.get("vlans", []):
            items.append({
                "tenant": tenant["name"],
                "vlan_id": vlan["id"],
                "expected_name": vlan["name"],
            })
    return items

async def verify_item(self, semaphore, client, context):
    async with semaphore:
        vlan_id = context["vlan_id"]
        expected_name = context["expected_name"]
        context["display_context"] = f"{context['tenant']} -> VLAN {vlan_id}"

        response = await client.get(f"/api/vlans/{vlan_id}")
        actual_name = response.json().get("name")

        if actual_name != expected_name:
            return self.format_mismatch("name", expected_name, actual_name, context)

        return self.format_verification_result(
            ResultStatus.PASSED, context, "VLAN name matches data model"
        )
```

### Batch API Pattern (verify_group)

Fetch multiple items in one API call:

```python
def get_items_to_verify(self):
    # Group devices by site
    sites = {}
    for device in self.data_model.get("devices", []):
        site_id = device["site_id"]
        sites.setdefault(site_id, []).append({"hostname": device["hostname"]})
    return sites

async def verify_group(self, semaphore, client, group_key, contexts):
    async with semaphore:
        # Single API call for all devices in the site
        response = await client.get(f"/api/sites/{group_key}/devices")
        devices = {d["hostname"]: d for d in response.json()}
        
        results = []
        for context in contexts:
            hostname = context["hostname"]
            if hostname not in devices:
                results.append(self.format_not_found("Device", hostname, context))
            else:
                device = devices[hostname]
                if device["status"] == "up":
                    results.append(self.format_verification_result(
                        ResultStatus.PASSED, context, "Device is up"
                    ))
                else:
                    results.append(self.format_verification_result(
                        ResultStatus.FAILED, context, f"Device status: {device['status']}"
                    ))
        return results
```
