# Unreleased

## Performance

- pyats broker: removed the per-command SSH liveness probe from the connection broker; dead sessions are now recovered by reconnecting and retrying the command once. Removes ~0.77s of event-loop blocking per test and the fleet-wide slowdown it caused.
- pyats broker: command rejections (SubCommandFailure) no longer trigger a full SSH disconnect and cache wipe; only transport-level failures do. Eliminates a 4.7x per-failure penalty on devices with unsupported commands.

## Bug Fixes

- pyats broker: unified per-device locking to prevent a stale caller from tearing down a successor's connection during the reconnect-and-retry window. The execute and disconnect paths previously used separate locks over two halves of one critical section.
- pyats: register `FTDTestBase` in `BASE_CLASS_MAPPING` so that `--include`/`--exclude` tag filtering works for FTD tests. Previously, FTD tests silently fell through to directory-based detection which drops group tags.

# 2.1.0b1

## Features

- robot rendering: added support for dicts as parent_key in `iterate_list_chunked` 
- add support for SDWAN token authentication for pyATS test cases via SDWAN_USERNAME & SDWAN_API_TOKEN

## Performance

- Replace batched device barriers with flat semaphore for D2D execution, eliminating idle time when devices finish at different speeds (#901)

## Bug Fixes

- Genie parsers that fire supplementary commands (e.g. VRF resolution) now work in broker mode

## Breaking Changes

- SSHTestBase.parse_output() is now async — test cases must use await self.parse_output(...)

# 2.0.0

## Major Features

- **pyATS Integration** (EXPERIMENTAL): Full support for pyATS operational test cases
  - Initial support for ACI, SD-WAN, and Catalyst Center architectures
  - IOS-XE Direct-to-Device (D2D) SSH tests for SD-WAN and Catalyst Center
  - Experimental `--pyats` and `--robot` flags to limit test execution to one framework
  - **Windows**: Robot tests only — PyATS libraries are not available on Windows. For PyATS tests, use WSL2 as an alternative.
  - **macOS requires Python 3.12+**: Earlier Python versions have known fork/SSL incompatibilities causing crashes during pyATS execution.
- **Combined Dashboard**: New `combined_summary.html` report linking Robot and pyATS summaries
- **Merged xUnit Output**: Robot and pyATS results merged into single `xunit.xml` for CI/CD integration
- **Diagnostic Collection**: `--diagnostic` flag wraps execution to collect troubleshooting info (env, packages, logs)
- **Fail-Fast Authentication**: Controller authentication validated before pyATS test execution starts

## Breaking Changes

- **Robot artifacts moved**: Output files (`log.html`, `report.html`, etc.) now created in `robot_results/` subdirectory; links in root directory for backward compatibility
- **Robot suite names changed**: Top-level Robot suite names now start with `Robot Results` instead of the output directory name. Use Robot's `--name MyProject` argument to override the top-level suite name when needed.
- **RESTinstance removed from core dependencies**: The `RESTinstance` Robot library has been removed. Users who still need RESTinstance can install it separately alongside nac-test.
- **Robot Framework arguments require `--` separator**: Additional Robot Framework arguments must now be passed after the `--` separator (e.g., `nac-test ... -- --variable X:Y`). Arguments passed directly without the separator are no longer accepted. Certain Robot options (like `--include`, `--exclude`, `--outputdir`) are controlled by nac-test and cannot be passed via `--`; use the corresponding nac-test options instead (e.g., `-i`/`--include`, `-e`/`--exclude`, `-o`/`--output`).
- **Removed legacy `iac-test` entrypoint**: Use `nac-test` instead.
- **nac-yaml v2.0**: YAML file merge behavior has changed (can affect data model merging results):
  - Within-file duplicates now disable merging for that entire list: if any input file contains duplicate dict items in a list, that list is concatenated (no merging) to preserve within-file duplicates. This can result in more items than before.
  - Relaxed matching logic: list dict items now merge when all shared primitive keys match; unique primitive keys present on only one side no longer prevent merging. Items that previously stayed separate may now be combined.
  - Details and examples: https://github.com/netascode/nac-yaml/pull/34

## CLI Changes

- **Added `--verbose`**: Enables verbose output for nac-test, Robot Framework, and pyATS execution
- **Added `--loglevel`** (replaces `--verbosity`): Control log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- **Deprecated `--verbosity`**: Hidden alias for `--loglevel`, will be removed in future version

## Environment Variable Changes

- **Renamed**: `NO_TESTLEVELSPLIT` → `NAC_TEST_DISABLE_TESTLEVELSPLIT` (now requires explicit `true`/`yes`/`1`)
- **Removed**: `NAC_VALIDATE_VERBOSITY` (was incorrectly named; use `NAC_TEST_LOGLEVEL`)

## Other Improvements

- Upgraded pabot to 5.2.2
- Streamlined CLI output with cleaner progress reporting
- `--minimal-reports` reduces pyATS HTML report size by 80-95% (only failed tests include full details)
- Consolidated rendered Robot templates, pabot intermediate files, and Robot result files under `robot_results/` to avoid output-root namespace collisions

# 1.2.3

- Exclude jsonpath-ng 1.8.0 due to upstream regression (#603)

# 1.2.2

- Add NETCONF related packages

# 1.2.1

- Fix path handling on Windows platform

# 1.2.0

- Add `--processes` CLI argument to configure the number of parallel processes for test execution
- Increase robot loglevel to `DEBUG` when verbosity of `nac-tool` is set to `DEBUG`
- Add `robotframework-jmespath` as a direct dependency
- Add support for test case parallelization for capable suites
- Return Robot/Pabot exit codes and improve error messages
- Add support for passing extra Robot Framework options to `nac-test`
- EXPERIMENTAL: Add support for chunking templates using the `iterate_list_chunked` directive

# 1.1.0

- Enhance error handling and logging
- Migrate to `uv` package manager
- Update license references

# 1.0.0

- BREAKING CHANGE: Rename tool from `iac-test` to `nac-test`
- Modernize CLI interface using [typer](https://github.com/fastapi/typer)
- BREAKING CHANGE: Do not deduplicate items in a list of primitive values, for example a list of strings
- BREAKING CHANGE: Deduplicate items in a list of dictionaries consistently, regardless of whether they are in the same file or not
- Randomize pabot port to avoid conflicts with multiple instances of `nac-test` running in parallel
- Be more strict about undefined variables in Jinja templates

# 0.2.6

- Fix issue with directly nested lists in YAML files

# 0.2.5

- Add dry-run option
- Make Robot include and exclude tags available in render process (`robot_include_tags` and `robot_exclude_tags` variables)

# 0.2.4

- Also copy non-robot files to temporary directory used for executing the tests

# 0.2.3

- Handle file errors gracefully
- Allow empty YAML files

# 0.2.2

- Do not merge YAML dictionary list items, where each list item has unique attributes with primitive values

# 0.2.1

- Fix issue with YAML attributes named `tag`
- Fix multiple instances of `include` and `exclude` CLI arguments

# 0.2.0

- Add support for ansible-vault encrypted values
- Add `!env` tag to read values from environment variables

# 0.1.5

- Fix bug related to nested template directories

# 0.1.4

- Upgrade to Robot Framework 6.x
- Add option to provide CLI arguments as environment variables

# 0.1.3

- Add xUnit output (`xunit.xml`)

# 0.1.2

- Add custom Jinja tests
- Add iterate_list_folder rendering directive

# 0.1.1

- Add CLI options to select test cases by tag
- Add Requests and JSONlibrary

# 0.1.0

- Initial release
