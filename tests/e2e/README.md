# E2E Tests for Combined Reporting

This directory contains end-to-end (E2E) tests that verify the complete nac-test workflow, including Robot Framework tests, PyATS API tests, PyATS D2D (device-to-device) tests, and the combined reporting dashboard. The test suite comprises **15 test classes** and **62 common tests** inherited by each scenario class, plus scenario-specific tests where needed.

> **Note:** E2E tests take approximately 1 minute per scenario to execute, as they run the full nac-test CLI pipeline. Use parallel execution (`-n auto`) to significantly reduce total runtime.

## Architecture

### Mock Infrastructure

The E2E tests use mock infrastructure to simulate real endpoints without requiring actual network devices or controllers:

1. **Mock API Server** (`mocks/mock_server.py`)
   - Flask-based HTTP server running on `localhost`
   - Uses OS-assigned ports (port 0) for parallel test execution compatibility
   - Simulates SD-WAN Manager, ACI, and Catalyst Center API endpoints
   - Configured via `mocks/mock_api_config.yaml` (default) and `mocks/mock_api_config_preflight_401.yaml` (auth failure scenarios)
   - Handles authentication, token generation, and API responses
   - Separate mocks documentation: `mocks/README.md`

2. **Mock SSH Devices** (`mocks/mock_unicon.py`)
   - Simulates IOS-XE devices for PyATS D2D tests
   - Referenced in a dynamically generated `testbed.yaml`
   - Supports common show commands with canned responses from `mocks/mock_data/iosxe/iosxe_mock_data.yaml`
   - Allows tests to verify device connectivity and command parsing

3. **Mock Server Controller** (`mocks/mock_server_ctl.py`)
   - Standalone control script for manual testing and profiling (Unix-only, uses `os.fork`)
   - Provides start/stop/status CLI for debugging
   - Not used by pytest fixtures (tests use `MockAPIServer` class directly)

### Test Organization — Base Class Inheritance Pattern

Tests use a **base class inheritance pattern** to minimize code duplication. The base class `E2ECombinedTestBase` contains 62 common tests that apply to all scenarios. Each scenario class inherits from the base and provides a scenario-specific fixture:

```
E2ECombinedTestBase (ABC)          # 62 common tests for all scenarios
    ├── TestE2ESuccess             # All tests pass (Robot+API+D2D)
    ├── TestE2EAllFail             # All tests fail (Robot+API+D2D)
    ├── TestE2EMixed               # Mixed pass/fail
    ├── TestE2EMixedRelativeOutput # Mixed with relative -o path
    ├── TestE2ERobotOnly           # Robot only, no PyATS (@windows)
    ├── TestE2EPyatsApiOnly        # PyATS API only (ACI), symlink test (#656)
    ├── TestE2EPyatsD2dOnly        # PyATS D2D only
    ├── TestE2EPyatsCc             # Catalyst Center (API+D2D)
    ├── TestE2EPyatsNxosD2d        # NX-OS D2D (SSH)
    ├── TestE2EVerbose             # --verbose flag (DEBUG level)
    ├── TestE2EVerboseWithInfo     # --verbose --loglevel INFO
    ├── TestE2EDryRun              # --dry-run Robot+PyATS
    ├── TestE2EDryRunPyatsOnly     # --dry-run PyATS only
    ├── TestE2EDryRunRobotFail     # --dry-run Robot validation failure
    ├── TestE2EWindowsPyatsSkip    # Windows: PyATS skipped (@windows, skipif)
    └── TestE2EPreflightAuthFailure # Pre-flight 401 auth failure
```

Each scenario:
- Runs the full nac-test CLI once (class-scoped fixture)
- Executes Robot Framework + PyATS API + PyATS D2D tests (as configured)
- Generates combined reporting dashboard
- Validates all outputs against expected results

## Directory Structure

```
tests/e2e/
├── README.md
├── __init__.py
├── config.py              # E2EScenario dataclass + 14 scenario definitions
├── conftest.py            # E2EResults dataclass + 16 class-scoped fixtures
├── html_helpers.py        # HTML parsing: SummaryStats, TestTypeStats, breadcrumbs, links
├── test_e2e_scenarios.py  # 15 test classes, 62 base tests + scenario-specific tests
├── mocks/
│   ├── __init__.py
│   ├── README.md
│   ├── mock_server.py         # Flask MockAPIServer class
│   ├── mock_server_ctl.py     # Standalone start/stop/status CLI (Unix-only)
│   ├── mock_unicon.py         # Mock Unicon device for D2D tests
│   ├── mock_api_config.yaml   # Default endpoint config (SDWAN, ACI, CC)
│   ├── mock_api_config_preflight_401.yaml  # Auth-failure endpoint config
│   └── mock_data/
│       ├── iosxe/
│       │   └── iosxe_mock_data.yaml  # Canned show command responses
│       └── nxos/
│           └── nxos_mock_data.yaml   # Canned show command responses for NX-OS
└── fixtures/
    ├── success/           # All tests pass
    ├── failure/           # All tests fail
    ├── mixed/             # Mixed pass/fail
    ├── robot_only/        # Robot only, no PyATS
    ├── pyats_api_only/    # PyATS API only (ACI), includes symlink
    ├── pyats_d2d_only/    # PyATS D2D only
    ├── pyats_cc/          # Catalyst Center (API+D2D)
    ├── pyats_nxos_d2d/    # NX-OS D2D (SSH)
    ├── verbose/           # --verbose / --loglevel tests
    ├── dry_run_robot_fail/ # Dry-run Robot validation failure
    ├── preflight_failure/  # Pre-flight 401 auth failure
    └── windows_pyats_skip/ # Windows PyATS skip behavior
```

## Running E2E Tests

### Local Execution

```bash
# Run all E2E tests in parallel (recommended)
pytest tests/e2e/ -n auto --dist loadscope

# Run all E2E tests sequentially (slower)
pytest tests/e2e/ -v

# Run a specific scenario
pytest tests/e2e/test_e2e_scenarios.py::TestE2ESuccess -v

# Run a specific test
pytest tests/e2e/test_e2e_scenarios.py::TestE2ESuccess::test_robot_statistics_correct -v

# Run tests matching a keyword
pytest tests/e2e/ -v -k "links_resolve"
```

### Parallel Execution

E2E tests support parallel execution via `pytest-xdist`. Use `--dist loadscope` to ensure all tests within a class run on the same worker (required for class-scoped fixtures):

```bash
# Auto-detect CPU count
pytest tests/e2e/ -n auto --dist loadscope

# Use specific number of workers
pytest tests/e2e/ -n 4 --dist loadscope
```

**Important:** Each test class shares a single scenario execution via class-scoped fixtures. The `--dist loadscope` flag ensures tests from the same class are not distributed across workers.

### CI/CD Execution

From `.github/workflows/test.yml`:

- **Linux**: `uv run pytest -n auto --dist loadscope tests/` — Python 3.10, 3.11, 3.12, 3.13 on ubuntu-latest
- **Windows**: `uv run pytest -m windows -n auto --dist loadscope tests/e2e tests/integration/...` — Python 3.10-3.13 on windows-latest (only `@pytest.mark.windows` tests)

## Test Markers

All E2E tests are marked with `@pytest.mark.e2e` via `pytestmark = pytest.mark.e2e` at the module level.

Additional markers:
- `@pytest.mark.windows` — applied to `TestE2ERobotOnly` and `TestE2EWindowsPyatsSkip`
- `@pytest.mark.skipif(not IS_WINDOWS)` — `TestE2EWindowsPyatsSkip` only runs on Windows

## Test Categories

The base class (`E2ECombinedTestBase`) contains **62 common tests** organized into 12 categories:

1. **CLI Behavior** (2 tests): Exit code matches expectation, no uncaught exceptions
2. **Directory Structure** (3 tests): Output directory created, combined_summary.html exists, root whitelist
3. **Robot Framework Outputs** (7 tests): output.xml, log.html, report.html, summary report, XML parsing, statistics
4. **Robot Backward Compatibility** (2 tests): Hard links / symlinks at root for legacy file locations
5. **Robot Summary Reports** (5 tests): Valid HTML, results table, breadcrumb navigation, statistics, view-details links
6. **PyATS Results Directory** (1 test): `pyats_results/` directory state
7. **PyATS API Outputs** (6 tests): Directory state, summary report, valid HTML, breadcrumb, statistics, links
8. **PyATS D2D Outputs** (6 tests): Directory state, summary report, valid HTML, breadcrumb, statistics, links
9. **Combined Dashboard** (7 tests): Valid HTML, Robot link, PyATS link, statistics, consistency, success rate, section links
10. **Hostname Display** (6 tests): Console output, summary tables, detail pages, filenames, API no-hostname behavior, sanitization
11. **Merged xunit.xml** (8 tests): Root file exists, valid XML, total tests count, failures count, testsuite names, subdirectory xunit files
12. **Stdout Output Validation** (7 tests): Summary header, statistics, no individual framework messages, no archive discovery messages, PyATS discovery consolidated, relative test names, visual spacing (ref #540)
13. **Security / Credential Exposure** (1 test): Scans all output artifacts including ZIP files for credential sentinel (ref #689)

## Test Scenarios

The test suite includes **14 scenarios** defined in `config.py` via the `E2EScenario` dataclass:

| Scenario | Architecture | Robot | PyATS API | PyATS D2D | Exit Code | Notes |
|---|---|---|---|---|---|---|
| SUCCESS | SDWAN | 1 pass | 1 pass | 1 pass | 0 | |
| ALL_FAIL | SDWAN | 1 fail | 1 fail | 1 fail | 3 | Graduated exit code |
| MIXED | SDWAN | 1p/1f | 0p/1f | 1p/0f | 2 | Graduated exit code |
| MIXED_RELATIVE_OUTPUT | SDWAN | 1p/1f | 0p/1f | 1p/0f | 2 | Same as MIXED, relative `-o` path |
| ROBOT_ONLY | SDWAN | 1 pass | — | — | 0 | No testbed |
| PYATS_API_ONLY | ACI | — | 1 pass | — | 0 | Symlink test (#656) |
| PYATS_D2D_ONLY | SDWAN | — | — | 1 pass | 0 | |
| PYATS_CC | CC | — | 1 pass | 2 pass | 0 | Catalyst Center, 2 devices |
| PYATS_NXOS_D2D | NXOS | — | — | 1 pass | 0 | NX-OS switch (SSH) |
| VERBOSE | ACI | 1 pass | 1 pass | — | 0 | --verbose flag |
| VERBOSE_WITH_INFO | ACI | 1 pass | 1 pass | — | 0 | --verbose --loglevel INFO |
| DRY_RUN | SDWAN | 2 valid | — | — | 0 | --dry-run, no execution |
| DRY_RUN_PYATS_ONLY | ACI | — | — | — | 0 | --dry-run, PyATS only |
| DRY_RUN_ROBOT_FAIL | SDWAN | 0p/1f | — | — | 1 | --dry-run, invalid keyword |
| WINDOWS_PYATS_SKIP | SDWAN | 1 pass | — | — | 0 | Windows-only |
| PREFLIGHT_AUTH_FAILURE | ACI | 1 pass | — | — | 1 | 401 auth, Robot still runs |

## Fixtures Reference

### Shared Fixtures (from `tests/conftest.py` — session scope)

- `clear_controller_credentials` (autouse): Removes `*_URL`, `*_USERNAME`, `*_PASSWORD` env vars before tests
- `bypass_proxy_for_localhost` (autouse): Adds 127.0.0.1 to no_proxy/NO_PROXY for mock server access
- `mock_api_server`: Flask mock server loaded from `mock_api_config.yaml` (default)
- `mock_api_server_preflight_401`: Isolated server returning 401 for auth endpoints (pre-flight failure scenarios)

### E2E-Specific Fixtures (from `tests/e2e/conftest.py`)

**Session scope:**
- `user_testbed`: Temporary testbed YAML with mock Unicon devices (sd-dc-c8kv-01, sd-dc-c8kv-02, nxos-switch-01)

**Class scope (one per scenario — 16 fixtures):**
- `e2e_success_results`: Success scenario execution results
- `e2e_failure_results`: All-fail scenario execution results
- `e2e_mixed_results`: Mixed pass/fail scenario execution results
- `e2e_mixed_relative_output_results`: Mixed scenario with relative output path
- `e2e_robot_only_results`: Robot-only scenario execution results
- `e2e_pyats_api_only_results`: PyATS API-only scenario execution results
- `e2e_pyats_d2d_only_results`: PyATS D2D-only scenario execution results
- `e2e_pyats_cc_results`: Catalyst Center scenario execution results
- `e2e_pyats_nxos_d2d_results`: PyATS NX-OS D2D scenario execution results
- `e2e_verbose_results`: Verbose flag scenario execution results
- `e2e_verbose_with_info_results`: Verbose with INFO loglevel execution results
- `e2e_dry_run_results`: Dry-run scenario execution results
- `e2e_dry_run_pyats_only_results`: Dry-run PyATS-only execution results
- `e2e_dry_run_robot_fail_results`: Dry-run Robot validation failure execution results
- `e2e_windows_pyats_skip_results`: Windows PyATS skip scenario execution results
- `e2e_preflight_auth_failure_results`: Pre-flight auth failure scenario execution results

**Dataclasses:**
- `E2EScenario`: Scenario configuration (from `config.py`)
- `E2EResults`: Execution results container (from `conftest.py`)

## Adding a New Scenario

Follow these steps to add a new E2E test scenario:

1. **Create fixture directory** under `tests/e2e/fixtures/<scenario_name>/`
   - Add `data.yaml` with test data
   - Add `templates/tests/` with Robot and/or PyATS test files

2. **Define scenario** in `tests/e2e/config.py`:
   ```python
   NEW_SCENARIO = E2EScenario(
       name="new_scenario",
       description="Description of what this tests",
       data_path=f"{_FIXTURE_BASE}/new_scenario/data.yaml",
       templates_path=f"{_FIXTURE_BASE}/new_scenario/templates",
       requires_testbed=True,  # True if D2D tests, False otherwise
       architecture="SDWAN",  # "SDWAN", "ACI", or "CC"
       expected_exit_code=0,
       expected_robot_passed=1,
       expected_robot_failed=0,
       expected_pyats_api_passed=1,
       expected_pyats_api_failed=0,
       expected_pyats_d2d_passed=1,
       expected_pyats_d2d_failed=0,
       expected_d2d_hostnames=["device-01"],  # Required if D2D tests
   )
   ```

3. **Add class-scoped fixture** in `tests/e2e/conftest.py`:
   ```python
   @pytest.fixture(scope="class")
   def e2e_new_scenario_results(
       mock_api_server: MockAPIServer,
       user_testbed: str,  # omit if no D2D tests
       tmp_path_factory: pytest.TempPathFactory,
   ) -> E2EResults:
       """Execute the new scenario once and cache results for the class."""
       from tests.e2e.config import NEW_SCENARIO

       return _run_e2e_scenario(
           NEW_SCENARIO,
           mock_api_server,
           user_testbed,  # or None if no D2D tests
           tmp_path_factory,
       )
   ```

4. **Create test class** in `tests/e2e/test_e2e_scenarios.py`:
   ```python
   class TestE2ENewScenario(E2ECombinedTestBase):
       """New scenario tests - description."""

       @pytest.fixture
       def results(self, e2e_new_scenario_results: E2EResults) -> E2EResults:
           """Provide scenario results to base class tests."""
           return e2e_new_scenario_results

       # Add scenario-specific tests if needed
       def test_scenario_specific_behavior(self, results: E2EResults) -> None:
           """Verify scenario-specific behavior."""
           # Custom assertions here
   ```

5. **If Windows-compatible**, add `@pytest.mark.windows` decorator to the test class

## Key Module Reference

- `config.py` — E2EScenario dataclass and 14 scenario definitions
- `conftest.py` — E2EResults dataclass, _run_e2e_scenario helper, 16 fixtures (1 session, 15 class-scoped)
- `html_helpers.py` — HTML parsing utilities: SummaryStats, TestTypeStats, load/verify HTML, breadcrumb/link validation
- `test_e2e_scenarios.py` — Base class with 62 common tests, 15 scenario test classes
- `mocks/mock_server.py` — Flask-based MockAPIServer class with YAML config loading
- `mocks/mock_server_ctl.py` — Standalone CLI for manual server start/stop/status (Unix-only)
- `mocks/mock_unicon.py` — Mock Unicon device class for PyATS D2D tests
- `mocks/mock_api_config.yaml` — Default endpoint configuration (SDWAN, ACI, CC)
- `mocks/mock_api_config_preflight_401.yaml` — Auth-failure endpoint configuration
- `mocks/mock_data/iosxe/iosxe_mock_data.yaml` — Canned show command responses for IOS-XE devices
