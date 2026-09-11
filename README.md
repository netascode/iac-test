[![Tests](https://github.com/netascode/nac-test/actions/workflows/test.yml/badge.svg)](https://github.com/netascode/nac-test/actions/workflows/test.yml)
![Python Support](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-informational "Python Support: 3.10, 3.11, 3.12, 3.13, 3.14")

# nac-test

A CLI tool to render and execute [Robot Framework](https://robotframework.org/) and [PyATS](https://developer.cisco.com/pyats/) tests using [Jinja](https://jinja.palletsprojects.com/) templating. The framework supports two test execution engines:

- **Robot Framework**: Language-agnostic syntax with Jinja templating for dynamically rendered test suites
- **PyATS**: Cisco's Python-based test automation framework for network infrastructure validation

Both test types can be executed together (default) or independently using development flags.

```
$ nac-test --help

 Usage: nac-test [OPTIONS]

 A CLI tool to render and execute Robot Framework and PyATS tests using Jinja
 templating.

 Additional Robot Framework options can be passed at the end of the command to
 further control test execution (e.g., --variable, --listener, --loglevel).
 These are appended to the pabot invocation. Pabot-specific options and test
 files/directories are not supported and will result in an error.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ *  --data              -d   PATH        Path to data YAML files.             │
│                                         [env var: NAC_TEST_DATA] [required]  │
│ *  --templates         -t   DIRECTORY   Path to test templates.              │
│                                         [env var: NAC_TEST_TEMPLATES]        │
│                                         [required]                           │
│ *  --output            -o   DIRECTORY   Path to output directory.            │
│                                         [env var: NAC_TEST_OUTPUT] [required]│
│    --filters           -f   DIRECTORY   Path to Jinja filters.               │
│                                         [env var: NAC_TEST_FILTERS]          │
│    --tests                  DIRECTORY   Path to Jinja tests.                 │
│                                         [env var: NAC_TEST_TESTS]            │
│    --include           -i   TEXT        Selects test cases by tag (include). │
│                                         [env var: NAC_TEST_INCLUDE]          │
│    --exclude           -e   TEXT        Selects test cases by tag (exclude). │
│                                         [env var: NAC_TEST_EXCLUDE]          │
│    --processes              INTEGER     Number of parallel processes.        │
│                                         [env var: NAC_TEST_PROCESSES]        │
│    --render-only                        Only render tests without executing. │
│                                         [env var: NAC_TEST_RENDER_ONLY]      │
│    --dry-run                            Dry run flag (robot dry run mode).   │
│                                         [env var: NAC_TEST_DRY_RUN]          │
│    --pyats                              [DEV] Run only PyATS tests.          │
│                                         [env var: NAC_TEST_PYATS]            │
│    --robot                              [DEV] Run only Robot Framework tests.│
│                                         [env var: NAC_TEST_ROBOT]            │
│    --max-parallel-devices   INTEGER     Max devices for parallel SSH/D2D.    │
│                                         [env var: NAC_TEST_MAX_PARALLEL...]  │
│    --minimal-reports                    Reduce HTML report size (80-95%).    │
│                                         [env var: NAC_TEST_MINIMAL_REPORTS]  │
│    --diagnostic                         Wrap execution with diagnostic       │
│                                         collection script for troubleshooting│
│    --verbose                            Enables verbose output for nac-test, │
│                                         Robot and PyATS execution.           |
│                                         [env var: NAC_TEST_VERBOSE]          │
│    --loglevel          -l   [DEBUG|...] Log level. [default: WARNING]        │
│    --version                            Display version number.              │
│    --help                               Show this message and exit.          │
╰──────────────────────────────────────────────────────────────────────────────╯
```

**Breaking changes in nac-test 2.0:**
- See the [2.0.0 Breaking Changes](CHANGELOG.md#breaking-changes) in the changelog.
- The legacy `iac-test` CLI entrypoint has been removed. Use `nac-test` instead.

## How It Works

1. **Data Merging**: All YAML files from `--data` paths are merged into a single data model
2. **Test Discovery**: The framework discovers both Robot templates (`.robot`, `.j2`) and PyATS tests (`.py`) in the `--templates` directory
3. **Robot Rendering**: Jinja templates are rendered using the merged data model
4. **Test Execution**: Both Robot Framework and PyATS tests execute in parallel
5. **Report Generation**: HTML reports and artifacts are generated in the `--output` directory

For Robot Framework tests, [Pabot](https://pabot.org/) executes test suites in parallel. The `--skiponfailure non-critical` argument is used by default, meaning failed tests with a `non-critical` tag show up as "skipped" in the final report.

## Installation

**Platform Requirements:**

- **Linux**: Python 3.10–3.14
- **macOS**: Python 3.12–3.14 (earlier versions have known incompatibilities)
- **Windows**: Python 3.10–3.14, Robot tests only

Don't have the right Python version? See [Python 3 Installation & Setup Guide](https://realpython.com/installing-python/), or install using:
- `brew install python@3.12`
- `uv python install 3.12`
- `pyenv install 3.12`

`nac-test` can be installed in a virtual environment using `pip` or `uv`:

```bash
# Using pip
pip install nac-test

# Using uv (recommended, also for install performance reasons)
uv tool install nac-test
```

The following Robot libraries are included with `nac-test`:

- [robotframework-requests](https://github.com/MarketSquare/robotframework-requests)
- [robotframework-jmespath](https://github.com/netascode/robotframework-jmespath)
- [robotframework-jsonlibrary](https://github.com/robotframework-thailand/robotframework-jsonlibrary)
- [robotframework-pabot](https://pabot.org/) for parallel test execution

Any other libraries can of course be added via `pip` or `uv`.

## Development Installation (Feature Branch / Pre-Release)

When working with feature branches or pre-release versions that aren't yet published to PyPI, you must install packages in **editable mode** from local source. This is required because `pip install nac-test` only works for released versions on PyPI.

### Prerequisites

- Python 3.10–3.14
- `uv` installed ([Installation Guide](https://docs.astral.sh/uv/getting-started/installation/))
- Local clones of the required repositories

### Required Packages for PyATS Testing

For PyATS-based testing, you need **both** packages:

| Package | Purpose |
|---------|---------|
| `nac-test` | Core test orchestration framework |
| `nac-test-pyats-common` | Architecture-specific adapters (ACI, SD-WAN, Catalyst Center) - **required** for PyATS tests |

### Quick Start: Install Both Packages

From a workspace containing both repositories:

```bash
cd /path/to/testing-for-nac  # or your workspace root

# Install both packages in editable mode (order matters - nac-test first)
uv pip install -e ./nac-test -e ./nac-test-pyats-common
```

Or install them separately:

```bash
# 1. Install nac-test (core framework) first
cd /path/to/nac-test
uv pip install -e .

# 2. Then install nac-test-pyats-common (depends on nac-test)
cd /path/to/nac-test-pyats-common
uv pip install -e .
```

### Install with Development Dependencies

To include testing, linting, and type-checking tools:

```bash
cd /path/to/nac-test
uv pip install -e ".[dev]"

cd /path/to/nac-test-pyats-common
uv pip install -e ".[dev]"
```

The `[dev]` extra includes `pytest`, `ruff`, `mypy`, `bandit`, and test coverage tools.

### Install from Architecture Repository

If you're working in an architecture-specific repository (e.g., `nac-sdwan-terraform`, `nac-catalystcenter-terraform`):

```bash
cd /path/to/nac-sdwan-terraform  # or nac-catalystcenter-terraform

# Install both frameworks from relative paths
uv pip install -e ../nac-test -e ../nac-test-pyats-common
```

### Key Points

- **Editable mode** (`-e` flag): Code changes take effect immediately without reinstalling
- **Installation order matters**: Always install `nac-test` before `nac-test-pyats-common`
- **Both packages required**: PyATS tests import from both `nac_test` and `nac_test_pyats_common`
- **Feature branches**: Use editable installs since unreleased versions aren't on PyPI

### Verifying Installation

```bash
uv pip list | grep nac-test
# Should show both packages with local file paths:
# nac-test                 X.Y.Z    /path/to/nac-test
# nac-test-pyats-common    X.Y.Z    /path/to/nac-test-pyats-common
```

The file paths confirm editable installations from local source.

## Ansible Vault Support

Values in YAML files can be encrypted using [Ansible Vault](https://docs.ansible.com/ansible/latest/user_guide/vault.html). This requires Ansible (`ansible-vault` command) to be installed and the following two environment variables to be defined:

```
export ANSIBLE_VAULT_ID=dev
export ANSIBLE_VAULT_PASSWORD=Password123
```

`ANSIBLE_VAULT_ID` is optional, and if not defined will be omitted.

## Additional Tags

### Reading Environment Variables

The `!env` YAML tag can be used to read values from environment variables.

```yaml
root:
  name: !env VAR_NAME
```

## Example

`data.yaml` located in `./data` folder:

```yaml
---
root:
  children:
    - name: ABC
      param: value
    - name: DEF
      param: value
```

`test1.robot` located in `./templates` folder:

```
*** Settings ***
Documentation   Test1

*** Test Cases ***
{% for child in root.children | default([]) %}

Test {{ child.name }}
    Should Be Equal   {{ child.param }}   value
{% endfor %}
```

After running `nac-test` with the following parameters:

```shell
nac-test --data ./data --templates ./templates --output ./tests
```

The following rendered Robot test suite can be found in the `./tests` folder:

```
*** Settings ***
Documentation   Test1

*** Test Cases ***

Test ABC
    Should Be Equal   value   value

Test DEF
    Should Be Equal   value   value
```

As well as the test results and reports:

```shell
$ tree -L 2 tests
tests
├── combined_summary.html
├── robot_results/
│   ├── log.html
│   ├── output.xml
│   ├── report.html
│   ├── summary_report.html
│   └── xunit.xml
├── log.html -> robot_results/log.html
├── output.xml -> robot_results/output.xml
├── report.html -> robot_results/report.html
├── xunit.xml -> robot_results/xunit.xml
├── pabot_results/
└── test1.robot
```

Note: Root-level `log.html`, `output.xml`, `report.html`, and `xunit.xml` are links to the corresponding files in `robot_results/` for backward compatibility.

## PyATS Testing

In addition to Robot Framework, `nac-test` supports PyATS-based tests for network infrastructure validation. PyATS tests are Python files that inherit from architecture-specific base classes and validate network state against the data model.

### Supported Architectures

PyATS tests support multiple Cisco architectures, each requiring specific environment variables:

| Architecture | Controller | Credential Sets |
|-------------|------------|----------------|
| ACI | APIC | `ACI_URL`, `ACI_USERNAME`, `ACI_PASSWORD` |
| SD-WAN | SD-WAN Manager | **Token (20.18+):** `SDWAN_URL`, `SDWAN_API_TOKEN` <br> **Or Session:** `SDWAN_URL`, `SDWAN_USERNAME`, `SDWAN_PASSWORD` |
| Catalyst Center | Catalyst Center | `CC_URL`, `CC_USERNAME`, `CC_PASSWORD` |

> **Note:** SD-WAN supports two credential methods. When both are present, token authentication takes priority. The framework remembers which credential set was matched and exposes the `auth_method` attribute for downstream adapters.

For D2D (Direct-to-Device) SSH tests, IOS-XE device credentials are also required:

| Test Type | Environment Variables |
|-----------|----------------------|
| SD-WAN D2D | `IOSXE_USERNAME`, `IOSXE_PASSWORD` (in addition to SD-WAN Manager credentials) |
| Catalyst Center D2D | `IOSXE_USERNAME`, `IOSXE_PASSWORD` (in addition to Catalyst Center credentials) |

### Test Types

PyATS tests are organized into two categories:

| Type | Location | Description |
|------|----------|-------------|
| **API Tests** | `tests/` (not under `d2d/`) | Tests against controllers via REST API |
| **D2D Tests** | `tests/d2d/` | Direct-to-Device SSH tests against network devices |

### Running PyATS Tests

```bash
# Set environment variables for your architecture (SD-WAN example)
export SDWAN_URL=https://sdwan-manager.example.com

# Option 1: API Token authentication (SD-WAN 20.18+)
export SDWAN_API_TOKEN=your-api-token

# Option 2: Username/Password authentication
export SDWAN_USERNAME=admin
export SDWAN_PASSWORD=yourpassword

# For D2D/SSH tests, also set IOS-XE device credentials
export IOSXE_USERNAME=admin
export IOSXE_PASSWORD=devicepassword

# Run all tests (Robot + PyATS combined)
nac-test -d ./data -t ./tests -o ./output

# Run only PyATS tests (development mode)
nac-test -d ./data -t ./tests -o ./output --pyats

# Run only Robot Framework tests (development mode)
nac-test -d ./data -t ./tests -o ./output --robot
```

**Note**: the --pyats and --robot are experimental development arguments which
might be removed in later versions. 

### PyATS Output

PyATS tests generate:
- **HTML Reports**: Detailed test results with pass/fail status per verification item
- **JSON Results**: Machine-readable results for CI/CD integration
- **Archive Files**: Compressed test artifacts (`.zip`)

Example output structure:

```shell
$ tree -L 3 results
results
├── combined_summary.html
├── robot_results/
└── pyats_results/
    ├── api/
    │   ├── html_reports/
    │   └── results.json
    └── d2d/
        ├── html_reports/
        └── results.json
```

## Merged Data Model

Before test execution, `nac-test` merges all YAML data files into a single data model. This merged file serves as the single source of truth for both Robot Framework templating and PyATS test validation.

### How It Works

1. All files from `--data` paths are recursively loaded
2. YAML structures are deep-merged (later files override earlier ones)
3. The merged result is written to the output directory as `merged_data_model_test_variables.json` and will be read by pyATS test cases
4. Both Robot and PyATS tests reference this merged data

### Accessing the Merged Data

The merged data model is available to:
- **Robot templates**: Via Jinja templating during render phase
- **PyATS tests**: Via the `MERGED_DATA_MODEL_TEST_VARIABLES_FILEPATH` environment variable

## Development Flags

For faster development cycles, you can run only one test framework at a time:

### `--pyats` Flag

Run only PyATS tests, skipping Robot Framework:

```bash
nac-test -d ./data -t ./tests -o ./output --pyats
```

This is useful when:
- Developing or debugging PyATS test files
- You don't have Robot templates in your test directory
- You want faster iteration on API/D2D tests

### `--robot` Flag

Run only Robot Framework tests, skipping PyATS:

```bash
nac-test -d ./data -t ./tests -o ./output --robot
```

This is useful when:
- Developing or debugging Robot templates
- You don't have PyATS tests in your test directory
- You want faster iteration on Robot test suites

**Note:** Using both `--pyats` and `--robot` simultaneously is not allowed and will result in an error.

## Report Size Optimization

For CI/CD pipelines with artifact size constraints, use the `--minimal-reports` flag:

```bash
nac-test -d ./data -t ./tests -o ./output --minimal-reports
```

This reduces HTML report size by **80-95%** by only including detailed command outputs for failed or errored tests. Passed tests show summary information without full API response bodies.

## SSH/D2D Test Parallelization

For Direct-to-Device (D2D) tests that connect to network devices via SSH, you can control parallelization:

```bash
# Automatically calculate based on system resources (default)
nac-test -d ./data -t ./tests -o ./output --pyats

# Limit to specific number of parallel device connections
nac-test -d ./data -t ./tests -o ./output --pyats --max-parallel-devices 10
```

The `--max-parallel-devices` option sets an upper limit on concurrent SSH connections to prevent overwhelming network devices or exhausting system resources.

## Custom Jinja Filters

Custom Jinja filters can be used by providing a set of Python classes where each filter is implemented as a separate `Filter` class in a `.py` file located in the `--filters` path. The class must have a single attribute named `name`, the filter name, and a `classmethod()` named `filter` which has one or more arguments. A sample filter can be found below.

```python
class Filter:
    name = "filter1"

    @classmethod
    def filter(cls, data):
        return str(data) + "_filtered"
```

## Custom Jinja Tests

Custom Jinja tests can be used by providing a set of Python classes where each test is implemented as a separate `Test` class in a `.py` file located in the `--tests` path. The class must have a single attribute named `name`, the test name, and a `classmethod()` named `test` which has one or more arguments. A sample test can be found below.

```python
class Test:
    name = "test1"

    @classmethod
    def test(cls, data1, data2):
        return data1 == data2
```

## Rendering Directives

Special rendering directives exist to render a single test suite per (YAML) list item. The directive can be added to the Robot template as a Jinja comment following this syntax:

```
{# iterate_list <YAML_PATH_TO_LIST> <LIST_ITEM_ID> <JINJA_VARIABLE_NAME> #}
```

After running `nac-test` with the data from the previous [example](#example) and the following template:

```
{# iterate_list root.children name child_name #}
*** Settings ***
Documentation   Test1

*** Test Cases ***
{% for child in root.children | default([]) %}
{% if child.name == child_name %}

Test {{ child.name }}
    Should Be Equal   {{ child.param }}   value
{% endif %}
{% endfor %}
```

The following test suites will be rendered:

```shell
$ tree -L 2 tests
tests
├── ABC
│   └── test1.robot
└── DEF
    └── test1.robot
```

A similar directive exists to put the test suites in a common folder though with a unique filename.

```
{# iterate_list_folder <YAML_PATH_TO_LIST> <LIST_ITEM_ID> <JINJA_VARIABLE_NAME> #}
```

The following test suites will be rendered:

```shell
$ tree -L 2 tests
tests
└── test1
    ├── ABC.robot
    └── DEF.robot
```

An additional directive exists to render a single test suite per (YAML) list item in chunks, which is useful for handling large datasets by splitting them across multiple template files. This is a variant of `iterate_list` that would still create separate folders.

> **Note:** This directive is experimental and may change in future versions. It is not subject to semantic versioning guarantees.

```
{# iterate_list_chunked <YAML_PATH_TO_LIST> <LIST_ITEM_ID> <JINJA_VARIABLE_NAME> <OBJECT_PATH> <CHUNK_SIZE> #}
```

All objects under the OBJECT_PATH will be counted and if their number is greater than the specified chunk size, the list will be split into multiple test suites with suffix `_2`, `_3`, etc.

Consider the following example:

```yaml
---
root:
  children:
    - name: ABC
      param: value
      nested_children:
        - name: Child1
          param: value
        - name: Child2
          param: value
        - name: Child3
          param: value
    - name: DEF
      param: value
      nested_children:
        - name: Child1
          param: value
```

After running `nac-test` with this data from the previous and the following template:

```
{# iterate_list_chunked root.children name child_name nested_children 2 #}
*** Settings ***
Documentation   Test1

*** Test Cases ***
{% for child in root.children | default([]) %}
{% if child.name == child_name %}

Test {{ child.name }}
    Should Be Equal   {{ child.param }}   value

{% for nested_child in child.nested_children | default([]) %}

Test {{ child.name }} Child {{ nested_child.name }}
    Should Be Equal   {{ nested_child.param }}   value
{% endfor %}

{% endif %}
{% endfor %}
```

Objects from the `nested_children` path will be counted and if their number is greater than the specified chunk size (`2`), the list will be split into multiple test suites with suffix `_002`, `_003`, etc. The following test suites will be rendered:

```shell
$ tree -L 2 tests
tests
├── ABC
│   ├── test1_001.robot
│   └── test1_002.robot
└── DEF
    └── test1_001.robot
```

The OBJECT_PATH may be either a single-level path pointing directly at a list, or a two-level dotted path (`<PARENT>.<CHILD>`) where the parent is a list or a dictionary and the child is the list to be chunked. When the parent is a dictionary, only the child list is split by CHUNK_SIZE while sibling keys under the same parent are preserved untouched in every chunk.

When the parent is a list, the child objects are counted across all parent items and chunked globally: each chunk holds up to CHUNK_SIZE child objects, carrying along only the parent items those children belong to (with the child list sliced accordingly). A parent item spanning a chunk boundary appears in both chunks, each time with just its portion of the child list.

For example, given the following input with an OBJECT_PATH of `services.endpoints` and a CHUNK_SIZE of `2`:

```yaml
services:
  - name: s1
    endpoints:
      - name: e1
      - name: e2
      - name: e3
  - name: s2
    endpoints:
      - name: e4
  - name: s3
    endpoints:
      - name: e5
```

three chunks are produced:

```yaml
# chunk 1
services:
  - name: s1
    endpoints:
      - name: e1
      - name: e2

# chunk 2
services:
  - name: s1
    endpoints:
      - name: e3
  - name: s2
    endpoints:
      - name: e4

# chunk 3
services:
  - name: s3
    endpoints:
      - name: e5
```


## Select Test Cases By Tag

The `--include` and `--exclude` CLI options filter test cases by tag for both Robot Framework and PyATS tests.

**Robot Framework**: Tags are applied to test cases using Robot's standard tagging mechanism. See [Robot Framework documentation](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#by-tag-names) for details.

**PyATS**: Tests are filtered based on their `groups` class attribute:

```python
class VerifyBgpNeighbors(SDWANTestBase):
    groups = ["bgp", "routing"]  # Matches --include bgp or --include routing
```

Both frameworks use Robot Framework's TagPatterns for consistent pattern matching:
- Simple tags: `bgp`, `health`, `ospf`
- Wildcards: `bgp*`, `?est`
- Boolean: `bgpANDrouting`, `bgpORospf`, `bgpNOTnrfu`
- Case-insensitive, underscores ignored

**Examples:**

```bash
# Run only tests tagged with 'bgp'
nac-test -d data/ -t templates/ -o output/ --include bgp

# Exclude tests tagged with 'nrfu'
nac-test -d data/ -t templates/ -o output/ --exclude nrfu

# Boolean patterns
nac-test -d data/ -t templates/ -o output/ --include "bgpORospf"
nac-test -d data/ -t templates/ -o output/ --exclude "bgpANDnrfu"
```


## Parallel Execution Control

The number of parallel processes used by pabot can be controlled via the `--processes` option:

```bash
nac-test -d data/ -t templates/ -o output/ --processes 4
```

If not specified, pabot uses `max(2, cpu_count)` as the default number of processes. You can also set this via the `NAC_TEST_PROCESSES` environment variable.

This option applies to both suite-level and test-level parallelization (see next section).


## Test Case Parallelization

### Suite-Level Parallelization (Default)

By default, `nac-test` (via pabot) executes test **suites** (i.e., each robot file) in parallel. Each suite runs in its own process, and the `--processes` option controls how many suites can run simultaneously.

### Test-Level Parallelization

Suite-level parallelization may be inefficient for test suites containing multiple long-running test cases (e.g., >10 seconds each). If your test cases are independent and can run concurrently, you can enable **test-level parallelization** by adding the following metadata to the suite's settings:

```robot
*** Settings ***
Metadata        Test Concurrency     True
```

**Note:** This approach benefits only long-running tests. For short tests, the scheduling overhead and log collection may offset any performance gains.

**Tip:** The _Test Concurrency_ metadata is case-insensitive (_test concurrency_, _TEST CONCURRENCY_, etc.).

**Implementation:** `nac-test` checks the rendered robot files for the `Metadata` setting and instructs pabot to run each test within the respective suite in parallel (using pabot's `--testlevelsplit --ordering ordering.txt` arguments). You can inspect the `ordering.txt` file in the output directory.

**Disabling test-level parallelization:** Set the environment variable `NAC_TEST_DISABLE_TESTLEVELSPLIT=true` to disable this feature.


## Advanced Robot Framework Options

You can pass additional Robot Framework options to `nac-test` using the `--` separator. These options are forwarded to the pabot/Robot Framework execution. This enables advanced use cases like custom variables and listeners:

```bash
# Pass custom variables (note the -- separator before Robot options)
nac-test -d data/ -t templates/ -o output/ -- --variable MY_VAR:value

# Multiple variables
nac-test -d data/ -t templates/ -o output/ -- --variable VAR1:value1 --variable VAR2:value2

# Add a listener
nac-test -d data/ -t templates/ -o output/ -- --listener MyListener.py

# Combine multiple options
nac-test -d data/ -t templates/ -o output/ -- --variable ENV:prod --listener MyListener

# Override the default --skiponfailure behavior
nac-test -d data/ -t templates/ -o output/ -- --skiponfailure critical
```

**Important:**
- The `--` separator is **required** before any Robot Framework options
- Some Robot Framework options are controlled by nac-test and cannot be passed via `--`:
  - `--include`/`-i` → use nac-test's `-i`/`--include`
  - `--exclude`/`-e` → use nac-test's `-e`/`--exclude`
  - `--outputdir`/`-d` → use nac-test's `-o`/`--output`
  - `--output`/`-o`, `--log`/`-l`, `--report`/`-r`, `--xunit`/`-x` → controlled internally
  - `--dryrun` → use nac-test's `--dry-run`
- Pabot-specific options (like `--testlevelsplit`, `--pabotlib`, etc.) and test file paths are not allowed and will result in an error with exit code 252

See the [Robot Framework User Guide](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#command-line-options) for all available options.

### Robot Framework Log Level

**Breaking change in nac-test 2.0:** The `--loglevel` argument is now a nac-test option that controls the overall logging verbosity, not a pass-through Robot Framework argument. Robot Framework's log level is automatically set to `DEBUG` when nac-test's `--loglevel` is set to `DEBUG`; otherwise, Robot uses its default log level.

If you need fine-grained control over Robot Framework's log level independently from nac-test's log level, use the `--` separator to pass Robot's `--loglevel` option directly:

```bash
# nac-test at INFO, Robot Framework at TRACE
nac-test -d data/ -t templates/ -o output/ --loglevel INFO -- --loglevel TRACE

# nac-test at WARNING (default), Robot Framework at DEBUG
nac-test -d data/ -t templates/ -o output/ -- --loglevel DEBUG
```

## Exit Codes

nac-test _mostly_ follows Robot Framework exit code conventions to provide meaningful feedback for CI/CD pipelines:

| Exit Code | Meaning | Description |
|-----------|---------|-------------|
| **0** | Success | All tests passed, no errors |
| **1-250** | Test failures | Number of failed tests (capped at 250) |
| **2** | Invalid nac-test arguments | Invalid or conflicting nac-test CLI arguments (aligns with POSIX/Typer convention) |
| **252** | Invalid Robot Framework arguments or no tests found | Robot Framework invalid arguments or no tests executed |
| **253** | Execution interrupted | Test execution was interrupted (Ctrl+C, etc.) |
| **255** | Execution error | Framework crash or infrastructure error |

(we only follow _mostly_ as we deviate in using `2` for invalid nac-test arguments, and don't use `251`).

## Verbose Mode

The `--verbose` flag enables verbose mode for troubleshooting test execution:

```bash
nac-test -d ./data -t ./tests -o ./output --verbose
```

When enabled, verbose mode:
- Sets nac-test log level to DEBUG (can be overridden by setting `--loglevel`)
- Enables verbose output for `pabot` execution (shows the Robot console output) 
- Sets Robot Framework loglevel to DEBUG for additional debug information in the
  execution (unless overridden by `--loglevel`)
- Shows additional progress information and console output during PyATS test execution. 
  The pyATS loglevel will follow the `--loglevel` setting, so you can reduce the output
  for example via `--verbose --loglevel ERROR` which limits pyATS debugging output
  to ERROR information

## Advanced Environment Variables

In addition to CLI options, `nac-test` supports several environment variables for advanced tuning:

### PyATS Execution Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `NAC_TEST_PYATS_PROCESSES` | Auto (CPU-based) | Number of parallel PyATS worker processes |
| `NAC_TEST_PYATS_MAX_CONNECTIONS` | Auto (resource-based) | Maximum concurrent API connections |
| `NAC_TEST_PYATS_API_CONCURRENCY` | 10 | Concurrent API requests per worker |
| `NAC_TEST_PYATS_SSH_CONCURRENCY` | 5 | Concurrent SSH connections per worker |
| `NAC_TEST_PYATS_OUTPUT_BUFFER_LIMIT` | 10485760 | Output buffer size in bytes (10MB) |
| `NAC_TEST_PYATS_KEEP_REPORT_DATA` | unset | Keep intermediate JSONL/archive files |
| `NAC_TEST_PYATS_OVERFLOW_DIR` | /tmp/nac_test_overflow | Directory for overflow files when memory limits exceeded |

### Robot Framework Execution

| Variable | Default | Description |
|----------|---------|-------------|
| `NAC_TEST_DISABLE_TESTLEVELSPLIT` | unset | Disable test-level parallelization for Robot |

### Verbose and Development

| Variable | Default | Description |
|----------|---------|-------------|
| `NAC_TEST_VERBOSE` | unset | Enable verbose mode: verbose output and retain intermediate files (see `NAC_TEST_PYATS_KEEP_REPORT_DATA`) |
| `NAC_TEST_DUMP_YAML_DATA_MODEL` | unset | Write merged data model as YAML for debugging. File is not auto-cleaned up; you must remove it manually. WARNING: May contain sensitive values. |

## Troubleshooting

If you're experiencing issues with nac-test (crashes, unexpected errors, test failures), use the `--diagnostic` flag to collect comprehensive diagnostic information.

**[Diagnostic Collection Guide](nac_test/support/README.md)**

The diagnostic flag:
- Collects system information, Python environment, and package versions
- Captures error logs and crash reports (especially useful for macOS issues)
- Automatically masks credentials before generating output
- Produces a single `.tar.gz` file you can safely attach to GitHub issues

### Quick Start

Simply add `--diagnostic` to your existing nac-test command:

```bash
# 1. Activate your virtual environment
source .venv/bin/activate

# 2. Set your environment variables (as you normally would for nac-test)
# Example for SD-WAN (token auth):
export SDWAN_URL=https://your-sdwan-manager.example.com
export SDWAN_API_TOKEN=your-api-token
# Or username/password auth:
# export SDWAN_USERNAME=admin
# export SDWAN_PASSWORD=your-password

# 3. Run nac-test with the --diagnostic flag
nac-test -d ./data -t ./tests -o ./output --pyats --diagnostic
```

The diagnostic flag will wrap your nac-test execution and generate a `nac-test-diagnostics-XXXXXX.tar.gz` file containing all diagnostic information with sensitive data automatically masked.
