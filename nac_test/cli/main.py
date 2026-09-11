# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""CLI entry point for nac-test."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from robot.errors import DataError

import nac_test
from nac_test.cli.diagnostic import run_diagnostic
from nac_test.cli.ui import display_aci_defaults_banner
from nac_test.cli.validators import validate_aci_defaults, validate_extra_args
from nac_test.combined_orchestrator import CombinedOrchestrator
from nac_test.core.constants import (
    DEBUG_MODE,
    DUMP_YAML_DATA_MODEL,
    EXIT_DATA_ERROR,
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_INVALID_ARGS,
)
from nac_test.data_merger import DataMerger
from nac_test.utils.cleanup import get_cleanup_manager
from nac_test.utils.formatting import format_duration
from nac_test.utils.logging import (
    DEFAULT_LOGLEVEL,
    LogLevel,
    configure_logging,
)
from nac_test.utils.platform import check_and_exit_if_unsupported_macos_python

# Pretty exceptions are verbose but helpful for debugging.
# Enable them when NAC_TEST_DEBUG=true, disable for cleaner output otherwise.
app = typer.Typer(add_completion=False, pretty_exceptions_enable=DEBUG_MODE)


logger = logging.getLogger(__name__)


def _print_cli_error(message: str) -> None:
    """Print a CLI argument error panel matching typer's own error style.

    Args:
        message: The error message to display inside the panel.
    """
    Console(stderr=True).print(
        Panel(message, title="Error", border_style="red", title_align="left")
    )


def version_callback(value: bool) -> None:
    """Print version and exit when --version is passed."""
    if value:
        typer.echo(f"nac-test, version {nac_test.__version__}")
        raise typer.Exit()


# Named "LoglevelOption" (not "Loglevel") to avoid confusion with the LogLevel enum type
LoglevelOption = Annotated[
    LogLevel | None,
    typer.Option(
        "--loglevel",
        "-l",
        help=f"Log level. Default: {DEFAULT_LOGLEVEL.value} (or DEBUG if --verbose is set).",
        envvar="NAC_TEST_LOGLEVEL",
        is_eager=True,
    ),
]

DeprecatedVerbosity = Annotated[
    LogLevel | None,
    typer.Option(
        "--verbosity",
        "-v",
        hidden=True,
        is_eager=True,
    ),
]


Data = Annotated[
    list[Path],
    typer.Option(
        "-d",
        "--data",
        exists=True,
        dir_okay=True,
        file_okay=True,
        help="Path to data YAML files.",
        envvar="NAC_TEST_DATA",
    ),
]


Templates = Annotated[
    Path,
    typer.Option(
        "-t",
        "--templates",
        exists=True,
        dir_okay=True,
        file_okay=False,
        help="Path to test templates.",
        envvar="NAC_TEST_TEMPLATES",
    ),
]


Filters = Annotated[
    Path | None,
    typer.Option(
        "-f",
        "--filters",
        exists=True,
        dir_okay=True,
        file_okay=False,
        help="Path to Jinja filters.",
        envvar="NAC_TEST_FILTERS",
    ),
]


Tests = Annotated[
    Path | None,
    typer.Option(
        "--tests",
        exists=True,
        dir_okay=True,
        file_okay=False,
        help="Path to Jinja tests.",
        envvar="NAC_TEST_TESTS",
    ),
]


Output = Annotated[
    Path,
    typer.Option(
        "-o",
        "--output",
        exists=False,
        dir_okay=True,
        file_okay=False,
        help="Path to output directory.",
        envvar="NAC_TEST_OUTPUT",
    ),
]


Include = Annotated[
    list[str] | None,
    typer.Option(
        "-i",
        "--include",
        help="Selects the test cases by tag (include).",
        envvar="NAC_TEST_INCLUDE",
    ),
]


Exclude = Annotated[
    list[str] | None,
    typer.Option(
        "-e",
        "--exclude",
        help="Selects the test cases by tag (exclude).",
        envvar="NAC_TEST_EXCLUDE",
    ),
]


RenderOnly = Annotated[
    bool,
    typer.Option(
        "--render-only",
        help="Only render tests without executing them.",
        envvar="NAC_TEST_RENDER_ONLY",
    ),
]


DryRun = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        help="Dry run mode: validates test structure without execution.",
        envvar="NAC_TEST_DRY_RUN",
    ),
]


Processes = Annotated[
    int | None,
    typer.Option(
        "--processes",
        help="Number of parallel processes for test execution (pabot --processes option), default is max(2, cpu count).",
        envvar="NAC_TEST_PROCESSES",
    ),
]


PyATS = Annotated[
    bool,
    typer.Option(
        "--pyats",
        help="[DEV ONLY] Run only PyATS tests (skips Robot Framework). Use for faster development cycles.",
        envvar="NAC_TEST_PYATS",
    ),
]


Robot = Annotated[
    bool,
    typer.Option(
        "--robot",
        help="[DEV ONLY] Run only Robot Framework tests (skips PyATS). Use for faster development cycles.",
        envvar="NAC_TEST_ROBOT",
    ),
]


MaxParallelDevices = Annotated[
    int,
    typer.Option(
        "--max-parallel-devices",
        help="Maximum number of devices to test in parallel for SSH/D2D tests. If not specified, automatically calculated based on system resources. Use this to set a lower limit if needed.",
        envvar="NAC_TEST_MAX_PARALLEL_DEVICES",
        min=1,
        max=500,
    ),
]


MinimalReports = Annotated[
    bool,
    typer.Option(
        "--minimal-reports",
        help="Only include detailed command outputs for failed/errored tests in HTML reports (80-95%% artifact size reduction).",
        envvar="NAC_TEST_MINIMAL_REPORTS",
    ),
]


Version = Annotated[
    bool,
    typer.Option(
        "--version",
        callback=version_callback,
        help="Display version number.",
        is_eager=True,
    ),
]


Diagnostic = Annotated[
    bool,
    typer.Option(
        "--diagnostic",
        help="Wrap execution with diagnostic collection (Linux/macOS only). Produces a zip with system info, logs, and artifacts.",
    ),
]


Verbose = Annotated[
    bool,
    typer.Option(
        "--verbose",
        help="Enable verbose mode: enables verbose output for nac-test, Robot and PyATS execution.",
        envvar="NAC_TEST_VERBOSE",
    ),
]


Testbed = Annotated[
    Path | None,
    typer.Option(
        "--testbed",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Path to custom PyATS testbed YAML. Devices in this file will override auto-discovered device connections and can include additional helper devices (e.g., jump hosts).",
        envvar="NAC_TEST_TESTBED",
    ),
]


@app.command(context_settings={"allow_extra_args": True})
def main(
    ctx: typer.Context,
    data: Data,
    templates: Templates,
    output: Output,
    filters: Filters = None,
    tests: Tests = None,
    include: Include = None,
    exclude: Exclude = None,
    render_only: RenderOnly = False,
    dry_run: DryRun = False,
    processes: Processes = None,
    pyats: PyATS = False,
    robot: Robot = False,
    max_parallel_devices: MaxParallelDevices | None = None,
    minimal_reports: MinimalReports = False,
    testbed: Testbed = None,
    loglevel: LoglevelOption = None,
    verbosity: DeprecatedVerbosity = None,
    version: Version = False,
    diagnostic: Diagnostic = False,
    verbose: Verbose = False,
) -> None:
    """A CLI tool to render and execute Robot Framework and PyATS tests using Jinja templating.

    Additional Robot Framework options can be passed after the -- separator to
    further control test execution (e.g., -- --variable X:Y, -- --listener MyListener).
    These are appended to the pabot invocation. Pabot-specific options, test
    files/directories, and options controlled by nac-test (like --include, --exclude)
    are not supported and will result in an error.
    """
    # Default child processes to UTF-8 regardless of OS locale (#630)
    os.environ.setdefault("PYTHONUTF8", "1")

    if diagnostic:
        run_diagnostic(output, argv=sys.argv)

    # Handle deprecated --verbosity option
    if verbosity is not None:
        typer.echo(
            typer.style(
                "Warning: --verbosity is deprecated, use --loglevel instead.",
                fg=typer.colors.YELLOW,
            ),
            err=True,
        )
        if loglevel is None:
            loglevel = verbosity

    # Resolve loglevel: explicit > verbose-implied > default
    if loglevel is not None:
        effective_loglevel = loglevel
    elif verbose:
        effective_loglevel = LogLevel.DEBUG
    else:
        effective_loglevel = DEFAULT_LOGLEVEL
    configure_logging(effective_loglevel)

    check_and_exit_if_unsupported_macos_python()

    # Validate development flag combinations
    if pyats and robot:
        _print_cli_error(
            "Cannot use both --pyats and --robot flags simultaneously.\n"
            "Use one development flag at a time, or neither for combined execution."
        )
        raise typer.Exit(EXIT_INVALID_ARGS)

    # Validate extra Robot Framework arguments early (fail fast before expensive operations)
    validated_robot_args = None
    if ctx.args:
        try:
            validated_robot_args = validate_extra_args(ctx.args)
        except ValueError as e:
            # CLI misuse: controlled option, pabot option, or datasource in extra args
            _print_cli_error(str(e))
            raise typer.Exit(EXIT_INVALID_ARGS) from None
        except DataError as e:
            _print_cli_error(f"Invalid Robot Framework argument: {e}")
            raise typer.Exit(EXIT_DATA_ERROR) from None

    # Create output directory and shared merged data file (SOT)
    output.mkdir(parents=True, exist_ok=True)

    # Validate ACI defaults before expensive merge operation
    # This catches the common mistake of forgetting -d ./defaults/
    if not validate_aci_defaults(data):
        typer.echo("")
        display_aci_defaults_banner()
        typer.echo("")
        raise typer.Exit(1)

    # Merge data files with timing
    start_time = datetime.now()
    typer.echo("\n\n📄 Merging data model files...")

    merged_data = DataMerger.merge_data_files(data)
    merged_data_path = DataMerger.write_merged_data_model(
        merged_data, output, dump_yaml=DUMP_YAML_DATA_MODEL
    )

    # Register merged data file for cleanup — always delete, even in debug mode,
    # because it may contain credentials resolved from !env references.
    cleanup_manager = get_cleanup_manager()
    cleanup_manager.register(merged_data_path)

    duration = (datetime.now() - start_time).total_seconds()
    typer.echo(f"✅ Data model merging completed ({format_duration(duration)})")

    # CombinedOrchestrator - handles both dev and production modes (uses pre-created merged data)
    orchestrator = CombinedOrchestrator(
        data_paths=data,
        templates_dir=templates,
        output_dir=output,
        merged_data=merged_data,
        custom_testbed_path=testbed,
        filters_path=filters,
        tests_path=tests,
        include_tags=include,
        exclude_tags=exclude,
        render_only=render_only,
        dry_run=dry_run,
        processes=processes,
        extra_args=validated_robot_args,
        max_parallel_devices=max_parallel_devices,
        minimal_reports=minimal_reports,
        loglevel=effective_loglevel,
        dev_pyats_only=pyats,
        dev_robot_only=robot,
        verbose=verbose,
    )

    # Track total runtime for benchmarking
    runtime_start = datetime.now()

    try:
        stats = orchestrator.run_tests()
    except KeyboardInterrupt:
        # Handle Ctrl+C interruption gracefully
        typer.echo(
            typer.style(
                "\n⚠️  Test execution was interrupted by user (Ctrl+C)",
                fg=typer.colors.YELLOW,
            )
        )
        # Exit with code 253 following Robot Framework convention
        raise typer.Exit(EXIT_INTERRUPTED) from None
    except Exception as e:
        # Infrastructure errors (template rendering, controller detection, etc.)
        logger.exception("Unexpected error during execution")
        typer.echo(f"Error during execution: {e}")
        # Pretty exception display is controlled by pretty_exceptions_enable=DEBUG_MODE
        # on the Typer app — no need for a separate DEBUG_MODE branch here.
        raise typer.Exit(EXIT_ERROR) from e

    # Display total runtime before exit
    runtime_end = datetime.now()
    total_runtime = (runtime_end - runtime_start).total_seconds()

    typer.echo(f"\nTotal runtime: {format_duration(total_runtime)}")

    if render_only:
        if stats.has_errors:
            reason = stats.robot.reason if stats.robot and stats.robot.reason else None
            if reason:
                typer.echo(f"\n❌ Template rendering failed: {reason}", err=True)
            else:
                typer.echo("\n❌ Template rendering failed", err=True)
            raise typer.Exit(stats.exit_code)
        typer.echo("\n✅ Templates rendered successfully (render-only mode)")
        raise typer.Exit(0)

    if stats.pre_flight_failure is not None:
        pf = stats.pre_flight_failure
        typer.echo(
            f"\n❌ Pre-flight failure ({pf.failure_type.display_name})",
            err=True,
        )
        raise typer.Exit(stats.exit_code)

    if stats.has_errors:
        error_list = "; ".join(stats.errors)
        typer.echo(f"\n❌ Execution errors: {error_list}", err=True)
        raise typer.Exit(stats.exit_code)

    if stats.has_failures:
        typer.echo(
            f"\n❌ Tests failed: {stats.failed} out of {stats.total} tests", err=True
        )
        raise typer.Exit(stats.exit_code)

    if stats.is_empty:
        typer.echo("\n⚠️  No tests were executed", err=True)
        raise typer.Exit(stats.exit_code)

    typer.echo(f"\n✅ All tests passed: {stats.passed} out of {stats.total} tests")
    raise typer.Exit(0)
