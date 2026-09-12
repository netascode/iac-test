# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Combined orchestrator for sequential PyATS and Robot Framework test execution."""

import logging
import os
from pathlib import Path
from typing import Any

import typer

from nac_test.cli.ui import (
    display_auth_failure_banner,
    display_unreachable_banner,
)
from nac_test.core.constants import (
    COMBINED_SUMMARY_FILENAME,
    HTML_REPORTS_DIRNAME,
    LOG_HTML,
    OUTPUT_XML,
    PYATS_RESULTS_DIRNAME,
    PYATS_SUPPORTED,
    REPORT_HTML,
    ROBOT_RESULTS_DIRNAME,
    SUMMARY_REPORT_FILENAME,
    SUMMARY_SEPARATOR_WIDTH,
    XUNIT_XML,
)
from nac_test.core.controller import (
    ResolutionError,
    format_resolution_error,
    resolve_controller,
)
from nac_test.core.controller_auth import preflight_auth_check
from nac_test.core.error_classification import AuthOutcome
from nac_test.core.reporting.combined_generator import CombinedReportGenerator
from nac_test.core.types import (
    CombinedResults,
    ControllerContext,
    PreFlightFailure,
    PreFlightFailureType,
    TestResults,
    ValidatedRobotArgs,
)
from nac_test.pyats_core.discovery import TestDiscovery
from nac_test.pyats_core.orchestrator import PyATSOrchestrator
from nac_test.robot.orchestrator import RobotOrchestrator
from nac_test.utils.cleanup import cleanup_stale_test_artifacts
from nac_test.utils.logging import DEFAULT_LOGLEVEL, LogLevel
from nac_test.utils.platform import check_and_exit_if_unsupported_macos_python
from nac_test.utils.terminal import terminal
from nac_test.utils.xunit_merger import merge_xunit_results

logger = logging.getLogger(__name__)


class CombinedOrchestrator:
    """Lightweight coordinator for sequential PyATS and Robot Framework test execution.

    This class discovers test types and delegates execution to existing orchestrators,
    following DRY and SRP principles by reusing proven orchestration logic.

    Output structure:
        output_dir/
        ├── combined_summary.html     # Unified dashboard (all frameworks)
        ├── xunit.xml                 # Merged xunit from Robot + PyATS (for CI/CD)
        ├── robot_results/            # Robot Framework artifacts
        │   ├── <rendered templates>
        │   ├── output.xml, log.html, report.html, xunit.xml
        │   └── summary_report.html
        ├── output.xml, log.html...   # Symlinks to robot_results/ (backward compat)
        └── pyats_results/            # PyATS artifacts
            ├── api/html_reports/summary_report.html, xunit.xml
            └── d2d/<device>/xunit.xml, html_reports/summary_report.html
    """

    def __init__(
        self,
        data_paths: list[Path],
        templates_dir: Path,
        output_dir: Path,
        merged_data: dict[str, Any] | None = None,
        filters_path: Path | None = None,
        tests_path: Path | None = None,
        include_tags: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        render_only: bool = False,
        dry_run: bool = False,
        max_parallel_devices: int | None = None,
        minimal_reports: bool = False,
        custom_testbed_path: Path | None = None,
        loglevel: LogLevel = DEFAULT_LOGLEVEL,
        dev_pyats_only: bool = False,
        dev_robot_only: bool = False,
        processes: int | None = None,
        extra_args: ValidatedRobotArgs | None = None,
        verbose: bool = False,
        device_filters: list[str] | None = None,
    ):
        """Initialize the combined orchestrator.

        Args:
            data_paths: List of paths to data model YAML files
            templates_dir: Directory containing test templates and PyATS test files
            output_dir: Base directory for test output
            merged_data: Already-loaded merged data model dict (avoids re-reading from disk)
            filters_path: Path to Jinja filters (Robot only)
            tests_path: Path to Jinja tests (Robot only)
            include_tags: Tag patterns to include (Robot Framework syntax, applies to both)
            exclude_tags: Tag patterns to exclude (Robot Framework syntax, applies to both)
            render_only: Only render tests without executing (Robot only)
            dry_run: Dry run mode (skips actual test execution)
            processes: Number of parallel processes for Robot test execution (Robot only)
            extra_args: Additional Robot Framework arguments to pass to pabot (Robot only)
            max_parallel_devices: Max parallel devices for PyATS D2D tests
            minimal_reports: Only include command outputs for failed/errored tests (PyATS only)
            custom_testbed_path: Path to custom PyATS testbed YAML for device overrides (PyATS only)
            loglevel: Logging level
            dev_pyats_only: Development mode - run only PyATS tests (skip Robot)
            dev_robot_only: Development mode - run only Robot Framework tests (skip PyATS)
            verbose: Enable verbose mode - keeps archive files, enables verbose output
        """
        self.data_paths = data_paths
        self.templates_dir = Path(templates_dir)
        self.output_dir = Path(output_dir)
        self.merged_data: dict[str, Any] = (
            merged_data if merged_data is not None else {}
        )

        # Robot-specific parameters
        self.filters_path = filters_path
        self.tests_path = tests_path
        self.include_tags = include_tags or []
        self.exclude_tags = exclude_tags or []
        self.render_only = render_only
        self.dry_run = dry_run
        self.processes = processes
        self.extra_args = extra_args

        # PyATS-specific parameters
        self.max_parallel_devices = max_parallel_devices
        self.minimal_reports = minimal_reports
        self.custom_testbed_path = custom_testbed_path
        self.loglevel = loglevel
        self.device_filters = device_filters

        # Development modes
        self.dev_pyats_only = dev_pyats_only
        self.dev_robot_only = dev_robot_only
        self.verbose = verbose

        # Controller context — resolved lazily in run_tests() when PyATS tests are present
        self.controller_context: ControllerContext | None = None

    def run_tests(self) -> CombinedResults:
        """Main entry point for combined test execution.

        Handles development modes (PyATS only, Robot only) and production mode (combined).
        Dev mode flags act as filters - they restrict which test types run but use the
        same execution flow, dashboard generation, and summary output.

        Returns:
            CombinedResults with per-framework test results
        """
        # Note: Output directory and merged data file created by main.py

        # Print dev mode warnings if applicable (skip in render-only mode)
        if self.dev_pyats_only and not self.render_only:
            typer.secho(
                "\n\n⚠️  WARNING: --pyats flag is for development use only. "
                "Production runs should use combined execution.",
                fg=typer.colors.YELLOW,
            )
        if self.dev_robot_only:
            typer.secho(
                "\n\n⚠️  WARNING: --robot flag is for development use only. "
                "Production runs should use combined execution.",
                fg=typer.colors.YELLOW,
            )

        # Discover test types (simple existence checks)
        has_pyats, has_robot = self._discover_test_types()

        # Apply dev mode filters - these flags restrict which test types run
        if self.dev_pyats_only:
            has_robot = False
        if self.dev_robot_only:
            has_pyats = False

        # Skip PyATS on Windows (PyATS wheels are not available for this platform)
        if has_pyats and not PYATS_SUPPORTED:
            typer.secho(
                "\n⚠️  PyATS tests found but skipped — PyATS is not supported on Windows.",
                fg=typer.colors.YELLOW,
            )
            has_pyats = False

        # Handle empty scenarios
        if not has_pyats and not has_robot:
            typer.echo("No test files found (no *.py PyATS tests or *.robot templates)")
            return CombinedResults()

        # Build combined results from individual orchestrators
        combined_results = CombinedResults()
        mode_suffix = " (dry-run)" if self.dry_run else ""

        # Clean up stale JSONL temp directories from prior runs
        cleanup_stale_test_artifacts(self.output_dir)

        # Pre-flight: detect controller and validate credentials for PyATS path only.
        # Robot path stays generic — it may not need controller access at all.
        # Dry-run mode skips auth — it validates test structure, not execution.
        preflight_failed = False
        if has_pyats and not self.render_only and not self.dry_run:
            preflight_failed = self._run_pre_flight_checks(combined_results)

        if has_pyats and not self.render_only and not preflight_failed:
            typer.echo(f"\n🧪 Running PyATS tests{mode_suffix}...\n")
            self._check_python_version()

            pyats_orchestrator = PyATSOrchestrator(
                data_paths=self.data_paths,
                test_dir=self.templates_dir,
                output_dir=self.output_dir,
                minimal_reports=self.minimal_reports,
                custom_testbed_path=self.custom_testbed_path,
                controller_context=self.controller_context,
                dry_run=self.dry_run,
                verbose=self.verbose,
                loglevel=self.loglevel,
                include_tags=self.include_tags,
                exclude_tags=self.exclude_tags,
                device_filters=self.device_filters,
            )
            if self.max_parallel_devices is not None:
                pyats_orchestrator.max_parallel_devices = self.max_parallel_devices

            # PyATS returns PyATSResults with .api and .d2d attributes
            pyats_results = pyats_orchestrator.run_tests()
            combined_results.api = pyats_results.api
            combined_results.d2d = pyats_results.d2d
        elif self.device_filters and not has_pyats:
            # Robot-only run with --device-filter -> warning
            typer.secho(
                "\n⚠️  WARNING: --device-filter was specified but no PyATS tests were executed; filter has no effect on Robot Framework.",
                fg=typer.colors.YELLOW,
            )

        if has_robot:
            typer.echo(f"\n🤖 Running Robot Framework tests{mode_suffix}...\n")

            robot_orchestrator = RobotOrchestrator(
                templates_dir=self.templates_dir,
                output_dir=self.output_dir,
                merged_data=self.merged_data,
                filters_path=self.filters_path,
                tests_path=self.tests_path,
                include_tags=self.include_tags,
                exclude_tags=self.exclude_tags,
                render_only=self.render_only,
                dry_run=self.dry_run,
                processes=self.processes,
                extra_args=self.extra_args,
                loglevel=self.loglevel,
                verbose=self.verbose,
            )
            try:
                robot_results = robot_orchestrator.run_tests()
                combined_results.robot = robot_results
            except Exception as e:
                logger.error(f"Robot Framework execution failed: {e}", exc_info=True)
                combined_results.robot = TestResults.from_error(str(e))

        if not self.render_only:
            logger.info("Generating combined dashboard...")
            logger.info(f"Combined results: {combined_results}")
            logger.debug(
                f"Calling CombinedReportGenerator with results: {combined_results}"
            )

            combined_generator = CombinedReportGenerator(self.output_dir)
            combined_path = combined_generator.generate_combined_summary(
                combined_results
            )
            if combined_path:
                logger.info(f"Combined dashboard generated: {combined_path}")

            merged_xunit = None
            try:
                merged_xunit = merge_xunit_results(self.output_dir, combined_results)
            except Exception as e:
                logger.warning(f"Failed to merge xunit files: {e}")
            if merged_xunit:
                logger.info(f"Merged xunit.xml: {merged_xunit}")
            elif not combined_results.is_empty:
                logger.warning("No xunit files found to merge")

            self._print_execution_summary(combined_results, merged_xunit)

        return combined_results

    @staticmethod
    def _check_python_version() -> None:
        """Defense-in-depth for programmatic usage that bypasses the CLI."""
        check_and_exit_if_unsupported_macos_python()

    def _discover_test_types(self) -> tuple[bool, bool]:
        """Discover which test types are present in the templates directory.

        Returns:
            Tuple of (has_pyats, has_robot)
        """
        # Build list of directories to exclude from PyATS discovery
        exclude_paths: list[Path] = []
        if self.filters_path:
            exclude_paths.append(self.filters_path)
        if self.tests_path:
            exclude_paths.append(self.tests_path)

        # PyATS discovery - use has_pyats_tests() for efficient early exit
        has_pyats = False
        try:
            test_discovery = TestDiscovery(
                self.templates_dir, exclude_paths=exclude_paths
            )
            has_pyats = test_discovery.has_pyats_tests()
            if has_pyats:
                logger.debug("Found PyATS test files")
        except Exception as e:
            logger.debug(f"\nPyATS discovery failed (no PyATS tests found): {e}\n")

        # Robot discovery - simple existence check (RobotWriter handles the rest)
        # Local helper with early exit for efficiency - stops directory traversal on first match
        def has_robot_files() -> bool:
            robot_extensions = {".robot", ".resource", ".j2"}
            for _, _, filenames in os.walk(self.templates_dir):
                for f in filenames:
                    if os.path.splitext(f)[1] in robot_extensions:
                        return True
            return False

        has_robot = has_robot_files()
        if has_robot:
            logger.debug("Found Robot template files")

        return has_pyats, has_robot

    def _run_pre_flight_checks(self, combined_results: CombinedResults) -> bool:
        """Detect the controller type and validate credentials before PyATS runs.

        Populates ``combined_results.pre_flight_failure`` on failure and emits a
        user-facing banner.  Returns ``True`` when a failure was detected (caller
        should skip PyATS execution), ``False`` when all checks passed.
        """
        try:
            self.controller_context = resolve_controller()
            logger.info(
                "Controller type detected: %s", self.controller_context.controller_type
            )
        except ResolutionError as e:
            detail = format_resolution_error(e)
            typer.secho(
                f"\n❌ Controller detection failed:\n{detail}",
                fg=typer.colors.RED,
                err=True,
            )
            combined_results.pre_flight_failure = PreFlightFailure(
                failure_type=PreFlightFailureType.DETECTION,
                controller_type=None,
                controller_url=None,
                detail=detail,
            )
            return True

        auth_result = preflight_auth_check(self.controller_context)
        if not auth_result.success:
            typer.echo("")
            if auth_result.reason == AuthOutcome.UNREACHABLE:
                display_unreachable_banner(
                    controller_type=auth_result.controller_type,
                    controller_url=auth_result.controller_url,
                    detail=auth_result.detail,
                )
            else:
                display_auth_failure_banner(
                    controller_type=auth_result.controller_type,
                    controller_url=auth_result.controller_url,
                    detail=auth_result.detail,
                )
            typer.echo("")

            combined_results.pre_flight_failure = PreFlightFailure(
                failure_type=(
                    PreFlightFailureType.UNREACHABLE
                    if auth_result.reason == AuthOutcome.UNREACHABLE
                    else PreFlightFailureType.AUTH
                ),
                controller_type=auth_result.controller_type,
                controller_url=auth_result.controller_url,
                detail=auth_result.detail,
                status_code=auth_result.status_code,
            )
            return True

        return False

    def _print_execution_summary(
        self, results: CombinedResults, merged_xunit_path: Path | None = None
    ) -> None:
        """Print execution summary with statistics."""
        # typer.echo("\n") prints two newlines for visual separation
        typer.echo("\n")
        typer.echo("=" * SUMMARY_SEPARATOR_WIDTH)
        typer.echo("Combined Test Execution Summary")
        typer.echo("-" * SUMMARY_SEPARATOR_WIDTH)
        if results.has_any_results:
            typer.echo(terminal.format_test_summary(results))
            typer.echo("-" * SUMMARY_SEPARATOR_WIDTH)

        # print absolute filenames in our summary to align with robot/rebot output
        combined_dashboard = self.output_dir / COMBINED_SUMMARY_FILENAME
        if combined_dashboard.exists():
            typer.echo(f"Dashboard:  {combined_dashboard.resolve()}")
        if results.robot is not None and not results.robot.is_empty:
            robot_log = self.output_dir / ROBOT_RESULTS_DIRNAME / LOG_HTML
            if robot_log.exists():
                typer.echo(f"Robot:      {robot_log.resolve()}")
        if results.api is not None and not results.api.is_empty:
            api_summary = (
                self.output_dir
                / PYATS_RESULTS_DIRNAME
                / "api"
                / HTML_REPORTS_DIRNAME
                / SUMMARY_REPORT_FILENAME
            )
            if api_summary.exists():
                typer.echo(f"PyATS API:  {api_summary.resolve()}")
        if results.d2d is not None and not results.d2d.is_empty:
            d2d_summary = (
                self.output_dir
                / PYATS_RESULTS_DIRNAME
                / "d2d"
                / HTML_REPORTS_DIRNAME
                / SUMMARY_REPORT_FILENAME
            )
            if d2d_summary.exists():
                typer.echo(f"PyATS D2D:  {d2d_summary.resolve()}")
        if merged_xunit_path is not None:
            typer.echo(f"xUnit:      {merged_xunit_path.resolve()}")

        typer.echo("=" * SUMMARY_SEPARATOR_WIDTH)

        stale_files = self._detect_stale_artifacts(results, merged_xunit_path)
        if stale_files:
            self._warn_stale_artifacts(stale_files)

        typer.echo()

    def _detect_stale_artifacts(
        self, results: CombinedResults, merged_xunit_path: Path | None
    ) -> list[str]:
        """Return filenames of stale artifacts left over from a prior run.

        Checks root-level Robot Framework artifacts (log.html, output.xml,
        report.html) and the merged xunit.xml. PyATS artifacts under
        pyats_results/ are intentionally excluded because
        multi_archive_generator.py unconditionally recreates that directory
        each run.
        """
        stale_files = []
        stale_artifacts = [LOG_HTML, OUTPUT_XML, REPORT_HTML, XUNIT_XML]
        for artifact in stale_artifacts:
            artifact_path = self.output_dir / artifact
            if not artifact_path.exists():
                continue

            # XUNIT_XML at root is written exclusively by merge_xunit_results.
            # If merged_xunit_path is None (merge skipped or failed), any existing
            # root xunit.xml is a stale artifact from a prior run.
            if artifact == XUNIT_XML and merged_xunit_path is not None:
                continue
            if artifact in (LOG_HTML, OUTPUT_XML, REPORT_HTML):
                if results.robot is not None and not results.robot.is_empty:
                    continue

            stale_files.append(artifact)
        return stale_files

    def _warn_stale_artifacts(self, stale_files: list[str]) -> None:
        """Print a YELLOW warning listing stale artifacts from a prior run."""
        typer.secho(
            "\n\n⚠️  Note: Stale artifacts from a previous run detected, delete the\n"
            f"   output directory {self.output_dir} to clear them.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        typer.secho(
            f"    Files: {', '.join(stale_files)}",
            fg=typer.colors.YELLOW,
            err=True,
        )
