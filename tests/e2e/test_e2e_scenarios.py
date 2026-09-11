# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""E2E Integration Tests - Combined Reporting.

This module contains end-to-end tests for the combined reporting workflow.
Tests are organized using a base class pattern:

- E2ECombinedTestBase: Contains all common tests that apply to every scenario
- TestE2ESuccess, TestE2EAllFail, TestE2EMixed: Inherit from base, provide
  scenario-specific fixture and any scenario-specific tests

This approach:
- Eliminates code duplication across scenarios
- Preserves class-scoped fixture caching (each scenario runs once)
- Makes it easy to add new scenarios (just create a new subclass)
- Allows scenario-specific tests where needed
"""

import logging
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from nac_test.core.constants import (
    COMBINED_SUMMARY_FILENAME,
    HTML_REPORTS_DIRNAME,
    IS_WINDOWS,
    LOG_HTML,
    MERGED_DATA_FILENAME,
    OUTPUT_XML,
    PRE_FLIGHT_FAILURE_FILENAME,
    PYATS_RESULTS_DIRNAME,
    REPORT_HTML,
    ROBOT_RESULTS_DIRNAME,
    SUMMARY_REPORT_FILENAME,
    SUMMARY_SEPARATOR_WIDTH,
    XUNIT_XML,
)
from nac_test.robot.reporting.robot_output_parser import RobotResultParser
from tests.conftest import assert_is_link_to
from tests.e2e.conftest import TEST_CREDENTIAL_SENTINEL, E2EResults
from tests.e2e.html_helpers import (
    assert_combined_stats,
    assert_report_stats,
    extract_summary_stats_from_combined,
    extract_test_type_sections,
    load_html_file,
    verify_breadcrumb_link,
    verify_html_structure,
    verify_table_structure,
    verify_view_details_links_resolve,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.e2e


# =============================================================================
# BASE TEST CLASS
#
# E2ECombinedTestBase holds all assertions that are valid regardless of which
# frameworks ran or which scenario was executed. Every scenario class — success,
# failure, Robot-only, PyATS-only, mixed, CC — inherits from it unchanged.
#
# The dry-run scenario classes are the one exception: dry-run does not execute
# tests, so many of the output-file and stats assertions don't apply and would
# need skip guards. Rather than conditionalising the base class, the dry-run
# classes inherit it as-is (guards already exist inside each method where
# needed) and add their own dry-run-specific assertions directly. See the
# DRY-RUN SCENARIO TESTS section below for the full design rationale.
# =============================================================================


class E2ECombinedTestBase:
    """Base class containing common E2E tests that apply to all scenarios.

    Subclasses must provide a `results` fixture that returns E2EResults
    for their specific scenario.

    Test categories:
    - CLI behavior (exit code matches expectation)
    - Directory structure (output dirs created)
    - Robot Framework outputs (files exist, parseable, stats correct)
    - PyATS API outputs (files exist, stats correct)
    - PyATS D2D outputs (files exist, stats correct)
    - Combined dashboard (files exist, stats correct, links work)
    """

    @pytest.fixture
    def results(self) -> E2EResults:
        """Override in subclass to provide scenario-specific results."""
        raise NotImplementedError("Subclass must provide results fixture")

    @pytest.fixture
    def parsed_xunit(self, results: E2EResults) -> ET.Element | None:
        """Parse merged xunit.xml once per scenario, return root element or None."""
        xunit_path = results.output_dir / XUNIT_XML
        if not xunit_path.is_file():
            return None
        tree = ET.parse(xunit_path)
        return tree.getroot()

    def test_executing_tests_and_generating_reports(self, results: E2EResults) -> None:
        """Just to indicate in pytest -v that the test execution is happening."""
        output_files = list(results.output_dir.rglob("*"))
        print("\n".join(str(f.relative_to(results.output_dir)) for f in output_files))
        pass

    # -------------------------------------------------------------------------
    # CLI Behavior Tests
    # -------------------------------------------------------------------------

    def test_cli_exit_code_matches_expectation(self, results: E2EResults) -> None:
        """Verify CLI exit code matches scenario expectation."""
        expected = results.scenario.expected_exit_code
        assert results.exit_code == expected, (
            f"Expected exit code {expected}, got {results.exit_code}\n"
            f"stdout: {results.stdout}"
        )

    def test_cli_has_no_exception(self, results: E2EResults) -> None:
        """Verify CLI execution completed without an unexpected Python exception.

        With subprocess execution, a module-level import error or unhandled crash
        during interpreter startup writes a traceback to stderr before any CLI
        exception handling runs. A clean exit — including a non-zero exit code from
        test failures — produces no traceback in stderr.
        """
        assert "Traceback (most recent call last)" not in results.stderr, (
            f"CLI crashed with an unexpected exception:\n{results.stderr}"
        )

    # -------------------------------------------------------------------------
    # Directory Structure Tests
    # -------------------------------------------------------------------------

    def test_output_directory_created(self, results: E2EResults) -> None:
        """Verify output directory was created."""
        assert results.output_dir.exists()
        assert results.output_dir.is_dir()

    def test_combined_summary_at_root(self, results: E2EResults) -> None:
        """Verify combined_summary.html exists at root level."""
        if results.scenario.expected_total_tests == 0:
            pytest.skip("No tests expected - combined summary not generated")
        combined = results.output_dir / COMBINED_SUMMARY_FILENAME
        assert combined.exists(), f"Missing {COMBINED_SUMMARY_FILENAME} at root"
        assert combined.is_file()

    def test_output_root_contains_only_expected_entries(
        self, results: E2EResults
    ) -> None:
        """Verify the output root contains only whitelisted entries."""
        expected_dirs = set()
        if results.robot_dir_exists:
            expected_dirs.add(ROBOT_RESULTS_DIRNAME)
        if results.has_pyats_results or results.scenario.expected_preflight_failure:
            expected_dirs.add(PYATS_RESULTS_DIRNAME)

        expected_files = {
            COMBINED_SUMMARY_FILENAME,
        }
        if results.has_robot_results or results.has_pyats_results:
            expected_files.add(XUNIT_XML)
        if results.has_robot_results:
            expected_files.update({OUTPUT_XML, LOG_HTML, REPORT_HTML})

        allowed = expected_dirs | expected_files
        actual = {path.name for path in results.output_dir.iterdir()}
        unexpected = actual - allowed

        assert not unexpected, (
            f"Unexpected entries in output root: {sorted(unexpected)}\n"
            f"Expected only: {sorted(allowed)}"
        )

    def test_merged_data_file_removed_after_run(self, results: E2EResults) -> None:
        """Merged data model YAML must not persist after a successful run.

        The file contains potentially sensitive variable data and is registered
        with CleanupManager for deletion on exit. Its absence confirms cleanup ran.
        """
        merged_data_file = results.output_dir / MERGED_DATA_FILENAME
        assert not merged_data_file.exists(), (
            f"{MERGED_DATA_FILENAME} was not cleaned up after run — "
            "it may contain sensitive data and should be deleted on exit"
        )

    # -------------------------------------------------------------------------
    # Robot Framework Output Tests
    # -------------------------------------------------------------------------

    def test_robot_results_directory_state(self, results: E2EResults) -> None:
        """Verify robot_results/ exists when expected, doesn't exist otherwise."""
        robot_dir = results.output_dir / ROBOT_RESULTS_DIRNAME
        if results.robot_dir_exists:
            assert robot_dir.exists(), f"Expected {ROBOT_RESULTS_DIRNAME}/ to exist"
            assert robot_dir.is_dir()
        else:
            assert not robot_dir.exists(), (
                f"Expected {ROBOT_RESULTS_DIRNAME}/ to NOT exist"
            )

    def test_robot_output_xml_exists(self, results: E2EResults) -> None:
        """Verify Robot output.xml exists."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        output_xml = results.output_dir / ROBOT_RESULTS_DIRNAME / OUTPUT_XML
        assert output_xml.exists(), f"Missing {ROBOT_RESULTS_DIRNAME}/{OUTPUT_XML}"

    def test_robot_log_html_exists(self, results: E2EResults) -> None:
        """Verify Robot log.html exists."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        log_html = results.output_dir / ROBOT_RESULTS_DIRNAME / LOG_HTML
        assert log_html.exists(), f"Missing {ROBOT_RESULTS_DIRNAME}/{LOG_HTML}"

    def test_robot_report_html_exists(self, results: E2EResults) -> None:
        """Verify Robot report.html exists."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        report_html = results.output_dir / ROBOT_RESULTS_DIRNAME / REPORT_HTML
        assert report_html.exists(), f"Missing {ROBOT_RESULTS_DIRNAME}/{REPORT_HTML}"

    def test_robot_summary_report_exists(self, results: E2EResults) -> None:
        """Verify Robot summary_report.html exists."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        summary = results.output_dir / ROBOT_RESULTS_DIRNAME / SUMMARY_REPORT_FILENAME
        assert summary.exists(), (
            f"Missing {ROBOT_RESULTS_DIRNAME}/{SUMMARY_REPORT_FILENAME}"
        )

    def test_robot_output_xml_parseable(self, results: E2EResults) -> None:
        """Verify Robot output.xml is valid XML."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        xml_path = results.output_dir / ROBOT_RESULTS_DIRNAME / OUTPUT_XML
        tree = ET.parse(xml_path)
        root = tree.getroot()
        assert root.tag == "robot", f"Expected root tag 'robot', got '{root.tag}'"

    def test_robot_statistics_correct(self, results: E2EResults) -> None:
        """Verify Robot test statistics match scenario expectations."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        xml_path = results.output_dir / ROBOT_RESULTS_DIRNAME / OUTPUT_XML
        parser = RobotResultParser(xml_path)
        data = parser.parse()
        stats = data["aggregated_stats"]
        scenario = results.scenario

        assert stats.passed == scenario.expected_robot_passed, (
            f"Robot passed: expected {scenario.expected_robot_passed}, "
            f"got {stats.passed}"
        )
        assert stats.failed == scenario.expected_robot_failed, (
            f"Robot failed: expected {scenario.expected_robot_failed}, "
            f"got {stats.failed}"
        )

    # -------------------------------------------------------------------------
    # Robot Backward Compatibility Tests (hard links preferred, symlinks as fallback)
    # -------------------------------------------------------------------------

    def test_robot_output_xml_link_exists(self, results: E2EResults) -> None:
        """Verify output.xml link exists at root."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        link = results.output_dir / OUTPUT_XML
        source = results.output_dir / ROBOT_RESULTS_DIRNAME / OUTPUT_XML
        assert link.exists(), "Missing output.xml link at root"
        assert_is_link_to(link, source)

    def test_robot_links_point_correctly(self, results: E2EResults) -> None:
        """Verify links correctly point to robot_results/ subdirectory."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        link = results.output_dir / OUTPUT_XML
        source = results.output_dir / ROBOT_RESULTS_DIRNAME / OUTPUT_XML
        assert_is_link_to(link, source)

    # -------------------------------------------------------------------------
    # Robot Summary Report Tests
    # -------------------------------------------------------------------------

    def test_robot_summary_has_valid_html(self, results: E2EResults) -> None:
        """Verify Robot summary report is valid HTML with UTF-8 charset."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        html_path = results.output_dir / ROBOT_RESULTS_DIRNAME / SUMMARY_REPORT_FILENAME
        html_content = load_html_file(html_path)
        verify_html_structure(html_content)

    def test_robot_summary_has_table(self, results: E2EResults) -> None:
        """Verify Robot summary has results table."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        html_path = results.output_dir / ROBOT_RESULTS_DIRNAME / SUMMARY_REPORT_FILENAME
        html_content = load_html_file(html_path)
        verify_table_structure(html_content)

    def test_robot_summary_has_breadcrumb(self, results: E2EResults) -> None:
        """Verify Robot summary has breadcrumb to combined dashboard."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        html_path = results.output_dir / ROBOT_RESULTS_DIRNAME / SUMMARY_REPORT_FILENAME
        html_content = load_html_file(html_path)
        verify_breadcrumb_link(html_content, COMBINED_SUMMARY_FILENAME)

    def test_robot_summary_stats_correct(self, results: E2EResults) -> None:
        """Verify Robot summary statistics are correct."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        html_path = results.output_dir / ROBOT_RESULTS_DIRNAME / SUMMARY_REPORT_FILENAME
        scenario = results.scenario
        assert_report_stats(
            html_path,
            expected_total=scenario.expected_robot_total,
            expected_passed=scenario.expected_robot_passed,
            expected_failed=scenario.expected_robot_failed,
            expected_skipped=scenario.expected_robot_skipped,
        )

    def test_robot_summary_view_details_links_resolve(
        self, results: E2EResults
    ) -> None:
        """Verify Robot summary View Details links point to existing files."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        html_path = results.output_dir / ROBOT_RESULTS_DIRNAME / SUMMARY_REPORT_FILENAME
        verified_links = verify_view_details_links_resolve(html_path)
        assert len(verified_links) > 0, "No View Details links found in Robot summary"

    # -------------------------------------------------------------------------
    # PyATS Results Directory Tests
    # -------------------------------------------------------------------------

    def test_pyats_results_directory_state(self, results: E2EResults) -> None:
        """Verify pyats_results/ exists when expected, doesn't exist otherwise."""
        pyats_dir = results.output_dir / PYATS_RESULTS_DIRNAME
        if results.has_pyats_results:
            assert pyats_dir.exists(), f"Expected {PYATS_RESULTS_DIRNAME}/ to exist"
            assert pyats_dir.is_dir()
        else:
            assert not pyats_dir.exists(), (
                f"Expected {PYATS_RESULTS_DIRNAME}/ to NOT exist"
            )

    # -------------------------------------------------------------------------
    # PyATS API Output Tests
    # -------------------------------------------------------------------------

    def test_pyats_api_results_directory_state(self, results: E2EResults) -> None:
        """Verify pyats_results/api/ exists when expected, doesn't exist otherwise."""
        api_dir = results.output_dir / PYATS_RESULTS_DIRNAME / "api"
        if results.has_pyats_api_results:
            assert api_dir.exists(), f"Expected {PYATS_RESULTS_DIRNAME}/api/ to exist"
        else:
            assert not api_dir.exists(), (
                f"Expected {PYATS_RESULTS_DIRNAME}/api/ to NOT exist"
            )

    def test_pyats_api_summary_report_exists(self, results: E2EResults) -> None:
        """Verify PyATS API summary report exists."""
        if not results.has_pyats_api_results:
            pytest.skip("No PyATS API results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "api"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        assert summary.exists(), f"Missing PyATS API {SUMMARY_REPORT_FILENAME}"

    def test_pyats_api_summary_has_valid_html(self, results: E2EResults) -> None:
        """Verify PyATS API summary is valid HTML with UTF-8 charset."""
        if not results.has_pyats_api_results:
            pytest.skip("No PyATS API results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "api"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        html_content = load_html_file(summary)
        verify_html_structure(html_content)

    def test_pyats_api_summary_has_breadcrumb(self, results: E2EResults) -> None:
        """Verify PyATS API summary has breadcrumb to combined dashboard."""
        if not results.has_pyats_api_results:
            pytest.skip("No PyATS API results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "api"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        html_content = load_html_file(summary)
        verify_breadcrumb_link(html_content, COMBINED_SUMMARY_FILENAME)

    def test_pyats_api_summary_stats_correct(self, results: E2EResults) -> None:
        """Verify PyATS API summary statistics are correct."""
        if not results.has_pyats_api_results:
            pytest.skip("No PyATS API results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "api"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        scenario = results.scenario
        assert_report_stats(
            summary,
            expected_total=scenario.expected_pyats_api_total,
            expected_passed=scenario.expected_pyats_api_passed,
            expected_failed=scenario.expected_pyats_api_failed,
            expected_skipped=scenario.expected_pyats_api_skipped,
        )

    def test_pyats_api_summary_view_details_links_resolve(
        self, results: E2EResults
    ) -> None:
        """Verify PyATS API summary View Details links point to existing files."""
        if not results.has_pyats_api_results:
            pytest.skip("No PyATS API results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "api"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        verified_links = verify_view_details_links_resolve(summary)
        assert len(verified_links) > 0, (
            "No View Details links found in PyATS API summary"
        )

    # -------------------------------------------------------------------------
    # PyATS D2D Output Tests
    # -------------------------------------------------------------------------

    def test_pyats_d2d_results_directory_state(self, results: E2EResults) -> None:
        """Verify pyats_results/d2d/ exists when expected, doesn't exist otherwise."""
        d2d_dir = results.output_dir / PYATS_RESULTS_DIRNAME / "d2d"
        if results.has_pyats_d2d_results:
            assert d2d_dir.exists(), f"Expected {PYATS_RESULTS_DIRNAME}/d2d/ to exist"
        else:
            assert not d2d_dir.exists(), (
                f"Expected {PYATS_RESULTS_DIRNAME}/d2d/ to NOT exist"
            )

    def test_pyats_d2d_summary_report_exists(self, results: E2EResults) -> None:
        """Verify PyATS D2D summary report exists."""
        if not results.has_pyats_d2d_results:
            pytest.skip("No PyATS D2D results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "d2d"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        assert summary.exists(), f"Missing PyATS D2D {SUMMARY_REPORT_FILENAME}"

    def test_pyats_d2d_summary_has_valid_html(self, results: E2EResults) -> None:
        """Verify PyATS D2D summary is valid HTML with UTF-8 charset."""
        if not results.has_pyats_d2d_results:
            pytest.skip("No PyATS D2D results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "d2d"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        html_content = load_html_file(summary)
        verify_html_structure(html_content)

    def test_pyats_d2d_summary_has_breadcrumb(self, results: E2EResults) -> None:
        """Verify PyATS D2D summary has breadcrumb to combined dashboard."""
        if not results.has_pyats_d2d_results:
            pytest.skip("No PyATS D2D results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "d2d"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        html_content = load_html_file(summary)
        verify_breadcrumb_link(html_content, COMBINED_SUMMARY_FILENAME)

    def test_pyats_d2d_summary_stats_correct(self, results: E2EResults) -> None:
        """Verify PyATS D2D summary statistics are correct."""
        if not results.has_pyats_d2d_results:
            pytest.skip("No PyATS D2D results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "d2d"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        scenario = results.scenario
        assert_report_stats(
            summary,
            expected_total=scenario.expected_pyats_d2d_total,
            expected_passed=scenario.expected_pyats_d2d_passed,
            expected_failed=scenario.expected_pyats_d2d_failed,
            expected_skipped=scenario.expected_pyats_d2d_skipped,
        )

    def test_pyats_d2d_summary_view_details_links_resolve(
        self, results: E2EResults
    ) -> None:
        """Verify PyATS D2D summary View Details links point to existing files."""
        if not results.has_pyats_d2d_results:
            pytest.skip("No PyATS D2D results in this scenario")
        summary = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "d2d"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )
        verified_links = verify_view_details_links_resolve(summary)
        assert len(verified_links) > 0, (
            "No View Details links found in PyATS D2D summary"
        )

    # -------------------------------------------------------------------------
    # Combined Dashboard Tests
    # -------------------------------------------------------------------------

    def test_combined_dashboard_has_valid_html(self, results: E2EResults) -> None:
        """Verify combined dashboard is valid HTML with UTF-8 charset."""
        if results.scenario.expected_total_tests == 0:
            pytest.skip("No tests expected - combined dashboard not generated")
        html_path = results.output_dir / COMBINED_SUMMARY_FILENAME
        html_content = load_html_file(html_path)
        verify_html_structure(html_content)

    def test_combined_dashboard_links_to_robot(self, results: E2EResults) -> None:
        """Verify combined dashboard links to Robot summary."""
        if not results.has_robot_results:
            pytest.skip("No Robot results in this scenario")
        html_path = results.output_dir / COMBINED_SUMMARY_FILENAME
        html_content = load_html_file(html_path)
        assert f"{ROBOT_RESULTS_DIRNAME}/{SUMMARY_REPORT_FILENAME}" in html_content, (
            "Missing link to Robot summary"
        )

    def test_combined_dashboard_links_to_pyats(self, results: E2EResults) -> None:
        """Verify combined dashboard links to PyATS results."""
        if not results.has_pyats_results:
            pytest.skip("No PyATS results in this scenario")
        html_path = results.output_dir / COMBINED_SUMMARY_FILENAME
        html_content = load_html_file(html_path)
        assert PYATS_RESULTS_DIRNAME in html_content, "Missing link to PyATS results"

    def test_combined_stats_correct(self, results: E2EResults) -> None:
        """Verify combined dashboard statistics are correct."""
        if results.scenario.expected_total_tests == 0:
            pytest.skip("No tests expected - combined dashboard not generated")
        html_path = results.output_dir / COMBINED_SUMMARY_FILENAME
        scenario = results.scenario
        assert_combined_stats(
            html_path,
            expected_total=scenario.expected_total_tests,
            expected_passed=scenario.expected_total_passed,
            expected_failed=scenario.expected_total_failed,
            expected_skipped=scenario.expected_total_skipped,
        )

    def test_combined_stats_internal_consistency(self, results: E2EResults) -> None:
        """Verify combined stats are internally consistent."""
        if results.scenario.expected_total_tests == 0:
            pytest.skip("No tests expected - combined dashboard not generated")
        html_path = results.output_dir / COMBINED_SUMMARY_FILENAME
        html_content = load_html_file(html_path)
        stats = extract_summary_stats_from_combined(html_content)

        # Total should equal passed + failed + skipped
        computed_total = stats.passed + stats.failed + stats.skipped
        assert stats.total == computed_total, (
            f"Stats inconsistency: passed({stats.passed}) + failed({stats.failed}) + "
            f"skipped({stats.skipped}) = {computed_total}, but total = {stats.total}"
        )

    def test_combined_success_rate_matches_expectation(
        self, results: E2EResults
    ) -> None:
        """Verify combined dashboard success rate matches expected value."""
        if results.scenario.expected_total_tests == 0:
            pytest.skip("No tests expected - combined dashboard not generated")
        html_path = results.output_dir / COMBINED_SUMMARY_FILENAME
        html_content = load_html_file(html_path)
        stats = extract_summary_stats_from_combined(html_content)
        scenario = results.scenario

        # Calculate expected rate
        if scenario.expected_total_tests > 0:
            expected_rate = (
                scenario.expected_total_passed / scenario.expected_total_tests
            ) * 100
        else:
            expected_rate = 0.0

        assert abs(stats.success_rate - expected_rate) < 0.1, (
            f"Expected {expected_rate:.1f}% success rate, got {stats.success_rate}%"
        )

    def test_combined_dashboard_section_links_resolve(
        self, results: E2EResults
    ) -> None:
        """Verify section report links are present iff the section has tests.

        Uses extract_test_type_sections() to parse per-framework sections and
        asserts two invariants for each section:
        - total_tests > 0  → report_path is set AND the file exists
        - total_tests == 0 → report_path is empty (no dead link rendered)

        The negative check (total==0 → no link) would have caught issue #644,
        where the "View Detailed Report →" link was rendered unconditionally
        even when a Robot run matched zero tests and no summary_report.html
        was generated.
        """
        html_path = results.output_dir / COMBINED_SUMMARY_FILENAME
        html_content = load_html_file(html_path)
        sections = extract_test_type_sections(html_content)

        for section in sections:
            if section.total_tests > 0:
                # Positive: link must exist and resolve to a real file
                assert section.report_path, (
                    f"Dashboard section '{section.test_type}' has {section.total_tests} "
                    f"tests but no report link"
                )
                resolved = (results.output_dir / section.report_path).resolve()
                assert resolved.exists(), (
                    f"Dashboard section '{section.test_type}' links to "
                    f"'{section.report_path}' which does not exist"
                )
            else:
                # Negative: no tests → no link (guards against #644 regression)
                assert not section.report_path, (
                    f"Dashboard section '{section.test_type}' has 0 tests but "
                    f"still renders a report link: '{section.report_path}'"
                )

    # -------------------------------------------------------------------------
    # Hostname Display Tests (for D2D scenarios)
    # -------------------------------------------------------------------------

    def test_hostnames_in_console_output(self, results: E2EResults) -> None:
        """Verify hostnames appear in console output with correct format."""
        if not results.scenario.has_pyats_d2d_tests:
            pytest.skip("No PyATS D2D tests in this scenario")

        from tests.e2e.html_helpers import verify_hostname_in_console_output

        # Guaranteed by E2EScenario.validate() when has_pyats_d2d_tests > 0
        assert results.scenario.expected_d2d_hostnames is not None
        found_hostnames = verify_hostname_in_console_output(
            results.stdout, results.scenario.expected_d2d_hostnames
        )
        assert len(found_hostnames) > 0, "No hostnames found in console output"

    def test_hostnames_in_d2d_summary_table(self, results: E2EResults) -> None:
        """Verify hostnames appear in PyATS D2D summary table."""
        if not results.scenario.has_pyats_d2d_tests:
            pytest.skip("No PyATS D2D tests in this scenario")

        from tests.e2e.html_helpers import assert_hostname_display_in_summary

        summary_path = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "d2d"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )

        # Guaranteed by E2EScenario.validate() when has_pyats_d2d_tests > 0
        assert results.scenario.expected_d2d_hostnames is not None
        found_hostnames = assert_hostname_display_in_summary(
            summary_path, results.scenario.expected_d2d_hostnames
        )
        assert len(found_hostnames) > 0, "No hostnames found in summary table"

    def test_hostnames_in_d2d_detail_pages(self, results: E2EResults) -> None:
        """Verify hostnames appear in PyATS D2D detail page headers."""
        if not results.scenario.has_pyats_d2d_tests:
            pytest.skip("No PyATS D2D tests in this scenario")

        from tests.e2e.html_helpers import assert_hostname_display_in_detail_pages

        # Find all D2D HTML detail files
        d2d_reports_dir = (
            results.output_dir / PYATS_RESULTS_DIRNAME / "d2d" / HTML_REPORTS_DIRNAME
        )
        detail_files = list(d2d_reports_dir.glob("*.html"))
        # Exclude summary report
        detail_files = [f for f in detail_files if f.name != SUMMARY_REPORT_FILENAME]

        assert len(detail_files) > 0, "No D2D detail HTML files found"

        # Guaranteed by E2EScenario.validate() when has_pyats_d2d_tests > 0
        assert results.scenario.expected_d2d_hostnames is not None
        found_hostnames = assert_hostname_display_in_detail_pages(
            detail_files, results.scenario.expected_d2d_hostnames
        )
        assert len(found_hostnames) > 0, "No hostnames found in detail pages"

    def test_hostnames_in_d2d_html_filenames(self, results: E2EResults) -> None:
        """Verify hostnames are included in D2D HTML filenames."""
        if not results.scenario.has_pyats_d2d_tests:
            pytest.skip("No PyATS D2D tests in this scenario")

        from nac_test.utils import sanitize_hostname
        from tests.e2e.html_helpers import assert_hostname_in_filenames

        # Guaranteed by E2EScenario.validate() when has_pyats_d2d_tests > 0
        assert results.scenario.expected_d2d_hostnames is not None
        sanitized_hostnames = [
            sanitize_hostname(hostname)
            for hostname in results.scenario.expected_d2d_hostnames
        ]

        # Find all D2D HTML detail files
        d2d_reports_dir = (
            results.output_dir / PYATS_RESULTS_DIRNAME / "d2d" / HTML_REPORTS_DIRNAME
        )
        detail_files = list(d2d_reports_dir.glob("*.html"))
        # Exclude summary report (doesn't have hostname in filename)
        detail_files = [f for f in detail_files if f.name != SUMMARY_REPORT_FILENAME]

        assert len(detail_files) > 0, "No D2D detail HTML files found"

        assert_hostname_in_filenames(detail_files, sanitized_hostnames)

    def test_api_tests_have_no_hostname(self, results: E2EResults) -> None:
        """Verify PyATS API tests do not show hostnames (API tests don't have devices)."""
        if not results.scenario.has_pyats_api_tests:
            pytest.skip("No PyATS API tests in this scenario")

        # Check API summary table - should not contain any hostnames with parentheses
        api_summary_path = (
            results.output_dir
            / PYATS_RESULTS_DIRNAME
            / "api"
            / HTML_REPORTS_DIRNAME
            / SUMMARY_REPORT_FILENAME
        )

        html_content = load_html_file(api_summary_path)

        # If we have D2D hostnames defined, make sure they don't appear in API reports
        if results.scenario.expected_d2d_hostnames:
            for hostname in results.scenario.expected_d2d_hostnames:
                assert f"({hostname})" not in html_content, (
                    f"Found hostname '{hostname}' in API summary, but API tests should not have hostnames"
                )

    def test_hostname_sanitization_in_filenames(self, results: E2EResults) -> None:
        """Verify special characters in hostnames are properly sanitized in filenames."""
        if not results.scenario.has_pyats_d2d_tests:
            pytest.skip("No PyATS D2D tests in this scenario")

        # Find all D2D HTML detail files
        d2d_reports_dir = (
            results.output_dir / PYATS_RESULTS_DIRNAME / "d2d" / HTML_REPORTS_DIRNAME
        )
        detail_files = list(d2d_reports_dir.glob("*.html"))
        detail_files = [f for f in detail_files if f.name != SUMMARY_REPORT_FILENAME]

        for file_path in detail_files:
            filename = file_path.name
            # Verify filename only contains safe characters (alphanumeric, underscore, dash, dot)
            unsafe_chars = re.findall(r"[^a-zA-Z0-9._-]", filename)
            assert len(unsafe_chars) == 0, (
                f"Filename contains unsafe characters: {filename} -> {unsafe_chars}"
            )

    # -------------------------------------------------------------------------
    # Merged xunit.xml Tests
    # -------------------------------------------------------------------------

    def test_merged_xunit_exists_at_root(self, results: E2EResults) -> None:
        """Verify merged xunit.xml exists at root and is not a symlink."""
        xunit_path = results.output_dir / XUNIT_XML
        if results.has_pyats_results or results.has_robot_results:
            assert xunit_path.exists(), "Missing merged xunit.xml at root"
            assert xunit_path.is_file(), "xunit.xml should be a file (not symlink)"
            assert not xunit_path.is_symlink(), "xunit.xml should not be a symlink"
        else:
            assert not xunit_path.exists(), (
                "Merged xunit.xml should not exist when no tests were run"
            )

    def test_merged_xunit_is_valid_xml(
        self, results: E2EResults, parsed_xunit: ET.Element | None
    ) -> None:
        """Verify merged xunit.xml is valid XML with testsuites root."""
        if not results.has_pyats_results and not results.has_robot_results:
            pytest.skip("No test runs in this scenario")
        assert parsed_xunit is not None, "xunit.xml missing or unparseable"
        assert parsed_xunit.tag == "testsuites", (
            f"Expected root 'testsuites', got '{parsed_xunit.tag}'"
        )

    def test_merged_xunit_has_correct_total_tests(
        self, results: E2EResults, parsed_xunit: ET.Element | None
    ) -> None:
        """Verify merged xunit.xml has correct total test count."""
        if not results.has_pyats_results and not results.has_robot_results:
            pytest.skip("No test runs in this scenario")
        assert parsed_xunit is not None, "xunit.xml missing or unparseable"
        expected_total = results.scenario.expected_total_tests
        actual_total = int(parsed_xunit.get("tests", 0))
        assert actual_total == expected_total, (
            f"xunit tests count mismatch: expected {expected_total}, got {actual_total}"
        )

    def test_merged_xunit_has_correct_failures(
        self, results: E2EResults, parsed_xunit: ET.Element | None
    ) -> None:
        """Verify merged xunit.xml has correct failure count."""
        if not results.has_pyats_results and not results.has_robot_results:
            pytest.skip("No test runs in this scenario")
        assert parsed_xunit is not None, "xunit.xml missing or unparseable"
        expected_failures = results.scenario.expected_total_failed
        actual_failures = int(parsed_xunit.get("failures", 0))
        assert actual_failures == expected_failures, (
            f"xunit failures mismatch: expected {expected_failures}, got {actual_failures}"
        )

    def test_merged_xunit_contains_expected_testsuites(
        self, results: E2EResults, parsed_xunit: ET.Element | None
    ) -> None:
        """Verify merged xunit.xml contains testsuites from all test sources."""
        if not results.has_pyats_results and not results.has_robot_results:
            pytest.skip("No test runs in this scenario")
        assert parsed_xunit is not None, "xunit.xml missing or unparseable"
        testsuites = parsed_xunit.findall("testsuite")
        testsuite_names = [ts.get("name", "") for ts in testsuites]

        if results.scenario.has_robot_tests:
            assert any("robot:" in name for name in testsuite_names), (
                f"Missing robot testsuite in merged xunit. Found: {testsuite_names}"
            )

        if results.scenario.has_pyats_api_tests:
            assert any("pyats_api:" in name for name in testsuite_names), (
                f"Missing pyats_api testsuite in merged xunit. Found: {testsuite_names}"
            )

        if results.scenario.has_pyats_d2d_tests:
            assert any("pyats_d2d/" in name for name in testsuite_names), (
                f"Missing pyats_d2d testsuite in merged xunit. Found: {testsuite_names}"
            )

    def test_robot_xunit_exists_in_subdirectory(self, results: E2EResults) -> None:
        """Verify Robot xunit.xml exists in robot_results/ subdirectory."""
        if not results.scenario.has_robot_tests:
            pytest.skip("No Robot tests in this scenario")
        robot_xunit = results.output_dir / ROBOT_RESULTS_DIRNAME / XUNIT_XML
        assert robot_xunit.exists(), f"Missing {ROBOT_RESULTS_DIRNAME}/{XUNIT_XML}"

    def test_pyats_api_xunit_exists_in_subdirectory(self, results: E2EResults) -> None:
        """Verify PyATS API xunit.xml exists in pyats_results/api/ subdirectory."""
        if not results.scenario.has_pyats_api_tests:
            pytest.skip("No PyATS API tests in this scenario")
        api_xunit = results.output_dir / PYATS_RESULTS_DIRNAME / "api" / XUNIT_XML
        assert api_xunit.exists(), f"Missing {PYATS_RESULTS_DIRNAME}/api/{XUNIT_XML}"

    def test_pyats_d2d_xunit_exists_in_subdirectory(self, results: E2EResults) -> None:
        if not results.scenario.has_pyats_d2d_tests:
            pytest.skip("No PyATS D2D tests in this scenario")

        assert results.scenario.expected_d2d_hostnames
        d2d_dir = results.output_dir / PYATS_RESULTS_DIRNAME / "d2d"
        xunit_files = list(d2d_dir.glob("*/xunit.xml"))

        assert len(xunit_files) == len(results.scenario.expected_d2d_hostnames), (
            f"Expected {len(results.scenario.expected_d2d_hostnames)} xunit.xml files "
            f"for hostnames {results.scenario.expected_d2d_hostnames}, "
            f"found {len(xunit_files)} in {d2d_dir}"
        )

    # -------------------------------------------------------------------------
    # Stdout Output Validation Tests (#540 - Streamlined Output)
    # -------------------------------------------------------------------------

    def test_stdout_contains_combined_summary_header(self, results: E2EResults) -> None:
        """Verify stdout contains Combined Test Execution Summary header."""
        assert "Combined Test Execution Summary" in results.filtered_stdout, (
            "Missing 'Combined Test Execution Summary' header in stdout"
        )

    def test_stdout_contains_stats_in_combined_summary(
        self, results: E2EResults
    ) -> None:
        """Verify stats line appears in Combined Summary section."""
        stdout = results.filtered_stdout
        summary_start = stdout.find("Combined Test Execution Summary")
        assert summary_start != -1, "Combined Summary section not found"

        summary_section = stdout[summary_start:]
        stats_pattern = r"\d+ tests, \d+ passed, \d+ failed, \d+ skipped\."
        match = re.search(stats_pattern, summary_section)
        assert match is not None, (
            f"Stats line not found in Combined Summary section.\n"
            f"Section content:\n{summary_section[:500]}"
        )

    def test_stdout_no_individual_framework_completion_messages(
        self, results: E2EResults
    ) -> None:
        """Verify individual frameworks don't print completion messages to stdout.

        The combined summary is the single source of completion status.
        Individual "completed" or "finished" messages from Robot/PyATS should not appear.
        """
        stdout = results.filtered_stdout.lower()

        completion_patterns = [
            r"robot.*completed",
            r"robot.*finished",
            r"pyats.*completed",
            r"pyats.*finished",
        ]
        for pattern in completion_patterns:
            match = re.search(pattern, stdout)
            assert match is None, (
                f"Found individual framework completion message matching '{pattern}'"
            )

    def test_stdout_no_archive_discovery_messages(self, results: E2EResults) -> None:
        """Verify archive discovery messages are not in stdout (moved to logger)."""
        if not results.scenario.has_pyats_tests:
            pytest.skip("No PyATS tests in this scenario")

        archive_patterns = ["Found API archive:", "Found D2D archive:"]
        for pattern in archive_patterns:
            assert pattern not in results.filtered_stdout, (
                f"Found '{pattern}' in stdout - should be logger.info only"
            )

    def test_stdout_pyats_discovery_consolidated(self, results: E2EResults) -> None:
        """Verify PyATS discovery output shows consolidated summary."""
        if not results.scenario.has_pyats_tests:
            pytest.skip("No PyATS tests in this scenario")
        if results.scenario.is_dry_run:
            pytest.skip("Dry-run prints summary instead of discovery message")

        discovery_pattern = r"Discovered \d+ PyATS test files"
        match = re.search(discovery_pattern, results.filtered_stdout)
        assert match is not None, (
            "Missing consolidated PyATS discovery message in stdout"
        )

    def test_stdout_pyats_test_names_are_relative(self, results: E2EResults) -> None:
        """Verify PyATS progress reporter shows relative test names, not absolute paths (#653)."""
        if not results.scenario.has_pyats_tests:
            pytest.skip("No PyATS tests in this scenario")
        if results.scenario.is_dry_run:
            pytest.skip("Dry-run doesn't execute tests")

        ansi_pattern = r"\x1b\[[0-9;]*m"
        stdout = re.sub(ansi_pattern, "", results.filtered_stdout)

        # Extract only the PyATS section (between markers) to avoid pabot output
        pyats_start = stdout.find("Running PyATS tests")
        pyats_end = stdout.find("Running Robot Framework tests")
        if pyats_start != -1:
            stdout = stdout[pyats_start : pyats_end if pyats_end != -1 else None]

        # Pattern matches ProgressReporterPlugin output:
        #   {timestamp} [PID:{pid}] [{worker_id}] [ID:{test_id}] {STATUS} {test_name}
        progress_pattern = r"\[PID:\d+\]\s+\[\d+\]\s+\[ID:\d+\]\s+(?:EXECUTING|PASSED|FAILED|ERROR|SKIPPED|ABORTED|BLOCKED)\s+(\S+)"
        matches = re.findall(progress_pattern, stdout)

        assert matches, (
            "No PyATS progress reporter output found in stdout. "
            "Expected lines matching '[PID:X] [Y] [ID:Z] STATUS test_name'"
        )

        for test_name in matches:
            assert re.fullmatch(r"\w[\w.-]*", test_name), (
                f"Expected a dot-notation test name, got: '{test_name}'\n"
                f"Test names should be relative (e.g., 'nrfu.verify_sdwan_sync')"
            )

    def test_stdout_combined_summary_has_visual_spacing(
        self, results: E2EResults
    ) -> None:
        """Verify Combined Summary block has blank lines before and after.

        The Combined Summary block should have visual breathing room:
        - Two blank lines before the opening '======' separator
        - Two blank lines after the closing '======' separator

        This prevents the summary from running into pabot output above
        and the "Total runtime" line below.
        """
        stdout = results.filtered_stdout
        summary_header = "Combined Test Execution Summary"
        separator = "=" * SUMMARY_SEPARATOR_WIDTH

        summary_pos = stdout.find(summary_header)
        assert summary_pos != -1, "Combined Summary section not found"

        opening_sep_pos = stdout.rfind(separator, 0, summary_pos)
        assert opening_sep_pos != -1, "Opening separator not found"

        closing_sep_pos = stdout.find(separator, summary_pos + len(summary_header))
        assert closing_sep_pos != -1, "Closing separator not found"

        before_opening = stdout[:opening_sep_pos]
        assert before_opening.endswith("\n\n\n"), (
            f"Missing two blank lines before Combined Summary block.\n"
            f"Content before separator ends with: {repr(before_opening[-20:])}"
        )

        after_closing = stdout[closing_sep_pos + len(separator) :]
        assert after_closing.startswith("\n\n\n"), (
            f"Missing two blank lines after Combined Summary block.\n"
            f"Content after separator starts with: {repr(after_closing[:20])}"
        )

    # -------------------------------------------------------------------------
    # Security Tests (#689 - Credential Exposure Prevention)
    # -------------------------------------------------------------------------

    def test_no_credentials_in_output_artifacts(self, results: E2EResults) -> None:
        """Verify that credentials don't appear in any output artifacts.

        Regression test for #689 - EnvironmentDebugPlugin was exposing env vars
        including passwords in env.txt files within PyATS archives.
        """
        offending_files: list[str] = []

        for file_path in results.output_dir.rglob("*"):
            if not file_path.is_file():
                continue

            if file_path.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(file_path, "r") as zf:
                        for name in zf.namelist():
                            content = zf.read(name).decode("utf-8", errors="ignore")
                            if TEST_CREDENTIAL_SENTINEL in content:
                                offending_files.append(f"{file_path}!{name}")
                except zipfile.BadZipFile:
                    pass  # Not a valid zip file, skip
            else:
                # Check all files as text - errors="ignore" safely handles binary
                # files by dropping undecodable bytes while preserving readable text.
                # No try/except needed: we control output_dir, files won't disappear.
                content = file_path.read_text(errors="ignore")
                if TEST_CREDENTIAL_SENTINEL in content:
                    offending_files.append(str(file_path))

        assert not offending_files, (
            "Credential sentinel found in output artifacts:\n"
            + "\n".join(f"  - {f}" for f in offending_files)
        )


# =============================================================================
# SUCCESS SCENARIO TESTS
# =============================================================================


class TestE2ESuccess(E2ECombinedTestBase):
    """E2E tests for the success scenario (all tests pass).

    Scenario: Robot (1 pass) + PyATS API (1 pass) + PyATS D2D (1 pass)
    Expected: CLI exits with code 0, 100% success rate
    """

    @pytest.fixture
    def results(self, e2e_success_results: E2EResults) -> E2EResults:
        """Provide success scenario results."""
        return e2e_success_results

    # -------------------------------------------------------------------------
    # Success-specific tests
    # -------------------------------------------------------------------------

    def test_expected_files_at_root(self, results: E2EResults) -> None:
        """Verify root contains expected files/directories including symlinks."""
        root_items = {item.name for item in results.output_dir.iterdir()}
        expected = {
            COMBINED_SUMMARY_FILENAME,
            ROBOT_RESULTS_DIRNAME,
            PYATS_RESULTS_DIRNAME,
            OUTPUT_XML,
            LOG_HTML,
            REPORT_HTML,
            XUNIT_XML,
        }
        missing = expected - root_items
        assert not missing, f"Missing expected items at root: {missing}"


# =============================================================================
# ALL FAIL SCENARIO TESTS
# =============================================================================


class TestE2EAllFail(E2ECombinedTestBase):
    """E2E tests for the all-fail scenario.

    Scenario: Robot (1 fail) + PyATS API (1 fail) + PyATS D2D (1 fail)
    Expected: CLI exits with code 1, 0% success rate
    """

    @pytest.fixture
    def results(self, e2e_failure_results: E2EResults) -> E2EResults:
        """Provide failure scenario results."""
        return e2e_failure_results


# =============================================================================
# MIXED SCENARIO TESTS
# =============================================================================


class TestE2EMixed(E2ECombinedTestBase):
    """E2E tests for the mixed pass/fail scenario.

    Scenario: Robot (1 pass, 1 fail) + PyATS API (0 pass, 1 fail) + PyATS D2D (1 pass)
    Expected: CLI exits with code 1 (any failure = non-zero)
    """

    @pytest.fixture
    def results(self, e2e_mixed_results: E2EResults) -> E2EResults:
        """Provide mixed scenario results."""
        return e2e_mixed_results

    # -------------------------------------------------------------------------
    # Mixed-specific tests
    # -------------------------------------------------------------------------

    def test_robot_summary_shows_both_pass_and_fail(self, results: E2EResults) -> None:
        """Verify Robot summary shows both passing and failing tests."""
        html_path = results.output_dir / ROBOT_RESULTS_DIRNAME / SUMMARY_REPORT_FILENAME
        html_content = load_html_file(html_path)

        assert "pass" in html_content.lower(), "Passing tests not shown"
        assert "fail" in html_content.lower(), "Failing tests not shown"

    def test_combined_dashboard_shows_both_pass_and_fail(
        self, results: E2EResults
    ) -> None:
        """Verify combined dashboard shows both passed and failed tests."""
        html_path = results.output_dir / COMBINED_SUMMARY_FILENAME
        html_content = load_html_file(html_path)

        assert "pass" in html_content.lower(), "Dashboard missing pass indicators"
        assert "fail" in html_content.lower(), "Dashboard missing fail indicators"


class TestE2EMixedRelativeOutput(E2ECombinedTestBase):
    """E2E tests for the mixed scenario using a relative output path.

    Scenario: Robot (1 pass, 1 fail) + PyATS API (0 pass, 1 fail) + PyATS D2D (1 pass)
    with `-o <relative-dir>`.
    """

    @pytest.fixture
    def results(self, e2e_mixed_relative_output_results: E2EResults) -> E2EResults:
        """Provide mixed relative-output scenario results."""
        return e2e_mixed_relative_output_results


# =============================================================================
# ROBOT-ONLY SCENARIO TESTS
# =============================================================================


@pytest.mark.windows
class TestE2ERobotOnly(E2ECombinedTestBase):
    """E2E tests for the robot-only scenario.

    Scenario: Robot (1 pass), no PyATS tests
    Expected: CLI exits with code 0, 100% success rate
    """

    @pytest.fixture
    def results(self, e2e_robot_only_results: E2EResults) -> E2EResults:
        """Provide robot-only scenario results."""
        return e2e_robot_only_results


# =============================================================================
# PYATS API-ONLY SCENARIO TESTS
# =============================================================================


class TestE2EPyatsApiOnly(E2ECombinedTestBase):
    """E2E tests for the PyATS API-only scenario.

    Scenario: PyATS API (1 pass), no Robot or D2D tests
    Expected: CLI exits with code 0, 100% success rate

    Note: the test file is a symlink pointing outside templates/ — this
    exercises the absolute() vs resolve() path in the progress plugin and
    archive inspector (see issue #656).
    """

    @pytest.fixture
    def results(self, e2e_pyats_api_only_results: E2EResults) -> E2EResults:
        """Provide PyATS API-only scenario results."""
        return e2e_pyats_api_only_results

    def test_stdout_symlinked_test_has_relative_name(self, results: E2EResults) -> None:
        """Verify symlinked test file appears with its relative name, not an absolute path.

        The fixture contains a test file that is a symlink pointing outside
        templates/. If Path.resolve() is used instead of Path.absolute() anywhere
        in the name-computation chain, relative_to() will fail and the name will
        degrade to a bare stem with no directory prefix (e.g. just
        'verify_aci_apic_appliance_operational_status' instead of
        'tests.verify_aci_apic_appliance_operational_status').
        """
        ansi_pattern = r"\x1b\[[0-9;]*m"
        stdout = re.sub(ansi_pattern, "", results.filtered_stdout)

        progress_pattern = r"\[PID:\d+\]\s+\[\d+\]\s+\[ID:\d+\]\s+(?:EXECUTING|PASSED|FAILED|ERROR|SKIPPED|ABORTED|BLOCKED)\s+(\S+)"
        test_names = re.findall(progress_pattern, stdout)

        assert test_names, "No PyATS progress reporter output found in stdout"

        for name in test_names:
            assert name.startswith("tests."), (
                f"PyATS test name '{name}' does not start with 'tests.' — "
                f"symlinked test files may have had their path resolved through "
                f"the symlink (resolve() instead of absolute())"
            )


# =============================================================================
# PYATS D2D-ONLY SCENARIO TESTS
# =============================================================================


class TestE2EPyatsD2dOnly(E2ECombinedTestBase):
    """E2E tests for the PyATS D2D-only scenario.

    Scenario: PyATS D2D (1 pass), no Robot or API tests
    Expected: CLI exits with code 0, 100% success rate
    """

    @pytest.fixture
    def results(self, e2e_pyats_d2d_only_results: E2EResults) -> E2EResults:
        """Provide PyATS D2D-only scenario results."""
        return e2e_pyats_d2d_only_results


# =============================================================================
# PYATS CATALYST CENTER (API + D2D) SCENARIO TESTS
# =============================================================================


class TestE2EPyatsCc(E2ECombinedTestBase):
    """E2E tests for the Catalyst Center scenario.

    Scenario: PyATS API (1 pass) + PyATS D2D (2 pass), no Robot tests
    Expected: CLI exits with code 0, 100% success rate
    """

    @pytest.fixture
    def results(self, e2e_pyats_cc_results: E2EResults) -> E2EResults:
        """Provide PyATS Catalyst Center scenario results."""
        return e2e_pyats_cc_results


# =============================================================================
# PYATS NX-OS D2D SCENARIO TESTS
# =============================================================================


class TestE2EPyatsNxosD2d(E2ECombinedTestBase):
    """E2E tests for the PyATS NX-OS direct-to-device SSH scenario.

    Scenario: PyATS NX-OS D2D, no Robot or API tests
    Expected: CLI exits with code 0, 100% success rate
    """

    @pytest.fixture
    def results(self, e2e_pyats_nxos_d2d_results: E2EResults) -> E2EResults:
        """Provide PyATS NX-OS D2D scenario results."""
        return e2e_pyats_nxos_d2d_results


# =============================================================================
# VERBOSE FLAG SCENARIO TESTS
# =============================================================================


class TestE2EVerbose(E2ECombinedTestBase):
    """E2E tests for the verbose flag scenario.

    Scenario: Robot (1 pass) + PyATS API (1 pass), verifies --verbose flag enables DEBUG log level
    Expected: CLI exits with code 0, verbose output visible for nac-test, Robot, and PyATS

    This scenario verifies (in addition to all other base class tests) that when --verbose flag is passed:
    1. Robot Framework is invoked with --loglevel DEBUG (the robot test checks this, we check the passed count)
    2. PyATS verbose output (%EASYPY-INFO and %EASYPY-DEBUG) IS enabled (--verbose implies DEBUG loglevel)
    """

    @pytest.fixture
    def results(self, e2e_verbose_results: E2EResults) -> E2EResults:
        return e2e_verbose_results

    def test_verbose_log_messages_in_stdout(self, results: E2EResults) -> None:
        """Verify DEBUG log messages appear (nac-test loglevel=DEBUG)."""
        assert "DEBUG - Found Robot template files" in results.stdout, (
            "Missing nac-test DEBUG log message in stdout."
        )

    def test_pabot_verbose_shows_test_result(self, results: E2EResults) -> None:
        """Verify pabot --verbose output shows test case result with PASS."""
        pattern = r"Verify Log Level\s+\| PASS \|"
        assert re.search(pattern, results.stdout), (
            "Missing pabot verbose output showing test result"
        )

    def test_easypy_info_in_stdout(self, results: E2EResults) -> None:
        """Verify %EASYPY-INFO appears (PyATS verbose output enabled)."""
        assert "%EASYPY-INFO" in results.stdout, "Missing %EASYPY-INFO in stdout"

    def test_easypy_debug_in_stdout(self, results: E2EResults) -> None:
        """Verify %EASYPY-DEBUG appears (PyATS verbose output enabled)."""
        assert "%EASYPY-DEBUG" in results.stdout, "Missing %EASYPY-DEBUG in stdout"


# =============================================================================
# VERBOSE WITH INFO LOGLEVEL SCENARIO TESTS
# =============================================================================


class TestE2EVerboseWithInfo(E2ECombinedTestBase):
    """E2E tests for --verbose --loglevel INFO combination.

    Scenario: Robot (1 pass) + PyATS API (1 pass), verifies --loglevel INFO filters PyATS DEBUG
    Expected: CLI exits with code 0, %EASYPY-INFO visible but %EASYPY-DEBUG filtered out, pabot verbose output is shown
    """

    @pytest.fixture
    def results(self, e2e_verbose_with_info_results: E2EResults) -> E2EResults:
        return e2e_verbose_with_info_results

    # The following two tests are duplicated from TestE2EVerbose. An intermediate
    # E2EVerboseTestBase class was considered but rejected: the overhead of an extra
    # base class outweighs the benefit for just two methods shared by two classes.
    def test_pabot_verbose_shows_test_result(self, results: E2EResults) -> None:
        """Verify pabot --verbose output shows test case result with PASS."""
        pattern = r"Verify Log Level\s+\| PASS \|"
        assert re.search(pattern, results.stdout), (
            "Missing pabot verbose output showing test result"
        )

    def test_easypy_debug_not_in_stdout(self, results: E2EResults) -> None:
        """Verify %EASYPY-DEBUG is filtered out when --loglevel INFO."""
        assert "%EASYPY-DEBUG" not in results.stdout, (
            "%EASYPY-DEBUG should be filtered with --loglevel INFO"
        )

    def test_easypy_info_in_stdout(self, results: E2EResults) -> None:
        """Verify %EASYPY-INFO still appears with --loglevel INFO."""
        assert "%EASYPY-INFO" in results.stdout, (
            "%EASYPY-INFO should be visible with --loglevel INFO"
        )


# =============================================================================
# DRY-RUN SCENARIO TESTS
#
# Three classes cover dry-run mode, each testing a distinct scenario:
#   TestE2EDryRun          — mixed Robot + PyATS (both frameworks active)
#   TestE2EDryRunPyatsOnly — PyATS API only (no Robot tests)
#   TestE2EDryRunRobotFail — Robot only, with a test that fails dry-run validation
#
# Unlike the earlier scenario classes (which each add no tests beyond the
# `results` fixture), these classes host their own dry-run-specific assertions.
# No intermediate DryRunBase was introduced because those assertions diverge
# quickly: PyATS-only has no Robot startup message, RobotFail has no PyATS at
# all. Only two assertions are shared between TestE2EDryRun and
# TestE2EDryRunPyatsOnly (PyATS header + startup message). An intermediate base
# class would add an abstraction layer to eliminate just those two 3-line
# methods — the small duplication is the lesser trade-off.
# =============================================================================


class TestE2EDryRun(E2ECombinedTestBase):
    """E2E tests for dry-run mode with mixed Robot + PyATS tests.

    Dry-run mode validates test structure without executing tests:
    - Robot: Uses Robot's --dryrun flag (validates syntax, reports as passed)
    - PyATS: Discovers and categorizes tests, prints what would run, exits early

    Expected: CLI exits with code 0, no tests actually executed
    """

    @pytest.fixture
    def results(self, e2e_dry_run_results: E2EResults) -> E2EResults:
        """Provide dry-run scenario results."""
        return e2e_dry_run_results

    # -------------------------------------------------------------------------
    # Dry-run specific tests (stdout validation)
    # -------------------------------------------------------------------------

    def test_pyats_dry_run_header_in_output(self, results: E2EResults) -> None:
        """Verify PyATS dry-run mode header is printed."""
        assert "DRY-RUN MODE" in results.stdout, (
            "Expected 'DRY-RUN MODE' header in stdout for PyATS dry-run"
        )

    def test_pyats_api_tests_listed(self, results: E2EResults) -> None:
        """Verify PyATS API tests are listed in dry-run output."""
        assert "API Tests" in results.stdout, (
            "Expected 'API Tests' section in dry-run output"
        )
        assert "verify_sdwan_sync_fail.py" in results.stdout, (
            "Expected API test file to be listed in dry-run output"
        )

    def test_pyats_d2d_tests_listed(self, results: E2EResults) -> None:
        """Verify PyATS D2D tests are listed in dry-run output."""
        assert "D2D/SSH Tests" in results.stdout, (
            "Expected 'D2D/SSH Tests' section in dry-run output"
        )
        assert "verify_iosxe_control.py" in results.stdout, (
            "Expected D2D test file to be listed in dry-run output"
        )

    def test_pyats_dry_run_complete_message(self, results: E2EResults) -> None:
        """Verify PyATS dry-run completion message is printed."""
        assert "PyATS dry-run complete" in results.stdout, (
            "Expected 'PyATS dry-run complete' message in stdout"
        )
        assert "no tests executed" in results.stdout, (
            "Expected 'no tests executed' message in stdout"
        )

    def test_dry_run_indicator_in_pyats_message(self, results: E2EResults) -> None:
        """Verify dry-run indicator appears in PyATS startup message."""
        assert re.search(
            r"(running|executing).*pyats.*dry-run", results.stdout, re.I
        ), "Expected dry-run indicator in PyATS startup message"

    def test_dry_run_indicator_in_robot_message(self, results: E2EResults) -> None:
        """Verify dry-run indicator appears in Robot Framework startup message."""
        assert re.search(
            r"(running|executing).*robot.*dry-run", results.stdout, re.I
        ), "Expected dry-run indicator in Robot Framework startup message"


class TestE2EDryRunPyatsOnly(E2ECombinedTestBase):
    """E2E tests for dry-run mode with PyATS-only (no Robot tests).

    This specifically tests the fix for the exit code bug where --dry-run
    on a PyATS-only repo would return exit code 1 because stats.is_empty
    becomes True (PyATS returns not_run with total=0, and no Robot tests
    contribute passed tests).

    Expected: CLI exits with code 0, no tests actually executed.
    """

    @pytest.fixture
    def results(self, e2e_dry_run_pyats_only_results: E2EResults) -> E2EResults:
        """Provide dry-run PyATS-only scenario results."""
        return e2e_dry_run_pyats_only_results

    # -------------------------------------------------------------------------
    # PyATS-only dry-run specific tests
    # -------------------------------------------------------------------------

    def test_pyats_dry_run_header_in_output(self, results: E2EResults) -> None:
        """Verify PyATS dry-run mode header is printed."""
        assert "DRY-RUN MODE" in results.stdout, (
            "Expected 'DRY-RUN MODE' header in stdout for PyATS dry-run"
        )

    def test_pyats_api_tests_listed(self, results: E2EResults) -> None:
        """Verify PyATS API tests are listed in dry-run output."""
        assert "API Tests" in results.stdout, (
            "Expected 'API Tests' section in dry-run output"
        )
        assert "verify_aci_apic_appliance_operational_status.py" in results.stdout, (
            "Expected ACI API test file to be listed in dry-run output"
        )

    def test_pyats_dry_run_complete_message(self, results: E2EResults) -> None:
        """Verify PyATS dry-run completion message is printed."""
        assert "PyATS dry-run complete" in results.stdout, (
            "Expected 'PyATS dry-run complete' message in stdout"
        )

    def test_dry_run_indicator_in_pyats_message(self, results: E2EResults) -> None:
        """Verify dry-run indicator appears in PyATS startup message.

        See section comment above for why this is duplicated rather than in a shared base.
        """
        assert re.search(
            r"(running|executing).*pyats.*dry-run", results.stdout, re.I
        ), "Expected dry-run indicator in PyATS startup message"


class TestE2EDryRunRobotFail(E2ECombinedTestBase):
    """E2E tests for dry-run mode with Robot test that fails validation.

    Tests that Robot dry-run correctly fails when a test uses a non-existent
    keyword. The expected exit code is 1 (one failing test).
    """

    @pytest.fixture
    def results(self, e2e_dry_run_robot_fail_results: E2EResults) -> E2EResults:
        return e2e_dry_run_robot_fail_results


# =============================================================================
# WINDOWS PYATS SKIP SCENARIO TESTS
# =============================================================================


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only test scenario")
@pytest.mark.windows
class TestE2EWindowsPyatsSkip(E2ECombinedTestBase):
    """E2E tests for the Windows PyATS skip scenario.

    Scenario: Windows platform with PyATS tests in templates but PyATS not supported.
    Robot (1 pass), PyATS tests discovered but skipped.
    Expected: CLI exits with code 0, warning message in stdout.

    This test class validates that on Windows:
    - Robot Framework tests execute normally
    - PyATS tests are discovered but skipped (not executed)
    - A warning is displayed about PyATS being unsupported on Windows
    """

    @pytest.fixture
    def results(self, e2e_windows_pyats_skip_results: E2EResults) -> E2EResults:
        return e2e_windows_pyats_skip_results

    def test_pyats_skip_warning_in_stdout(self, results: E2EResults) -> None:
        """Verify the PyATS skip warning message appears in stdout."""
        expected_warning = (
            "PyATS tests found but skipped — PyATS is not supported on Windows"
        )
        assert expected_warning in results.stdout, (
            f"Missing Windows PyATS skip warning in stdout.\n"
            f"Expected: '{expected_warning}'\n"
            f"Stdout: {results.stdout[:500]}"
        )


# =============================================================================
# PRE-FLIGHT AUTH FAILURE SCENARIO TESTS
# =============================================================================


class TestE2EPreflightAuthFailure(E2ECombinedTestBase):
    """E2E: pre-flight auth failure (401) does not abort Robot execution.

    Core behavioral contract of PR #636: a pre-flight failure must NOT prevent
    Robot Framework tests from running. The mock ACI /api/aaaLogin.json endpoint
    is overridden to return 401, triggering an AUTH pre-flight failure. PyATS is
    skipped but Robot tests still run to completion.

    Because Robot results are present, combined_summary.html uses the normal
    combined dashboard template (with summary-item stats and a Robot link), so
    most base class assertions apply unchanged.

    Two base class tests need overrides because the pre-flight failure causes the
    pyats_results/ directory to be created (for pre_flight_failure.html) even
    though no PyATS tests ran:
    - test_pyats_results_directory_state: pyats_results/ exists but has no api/ or d2d/

    Three combined stats tests are overridden because the template suppresses the
    success rate (shows '--') when pre_flight_failure is set, so the normal
    extract_summary_stats_from_combined() regex finds no percentage to parse:
    - test_combined_stats_correct
    - test_combined_stats_internal_consistency
    - test_combined_success_rate_matches_expectation
    """

    @pytest.fixture
    def results(self, e2e_preflight_auth_failure_results: E2EResults) -> E2EResults:
        return e2e_preflight_auth_failure_results

    # -------------------------------------------------------------------------
    # Overrides for tests affected by pyats_results/ being created for the
    # pre-flight failure report (pyats_results/pre_flight_failure.html)
    # -------------------------------------------------------------------------

    def test_pyats_results_directory_state(self, results: E2EResults) -> None:
        """pyats_results/ is created for pre_flight_failure.html even though no PyATS ran."""
        pyats_dir = results.output_dir / PYATS_RESULTS_DIRNAME
        assert pyats_dir.exists(), (
            f"Expected {PYATS_RESULTS_DIRNAME}/ to exist (for pre_flight_failure.html)"
        )
        assert pyats_dir.is_dir()

    def test_combined_stats_correct(self, results: E2EResults) -> None:
        """Pre-flight failure replaces the success rate with '--'; verify Robot counts instead."""
        html = load_html_file(results.output_dir / COMBINED_SUMMARY_FILENAME)
        verify_html_structure(html)
        assert 'class="summary-item rate"' in html
        # Rate shows '--' (not a percentage) when pre_flight_failure is set in the template
        assert "<strong>--</strong>" in html

    def test_combined_stats_internal_consistency(self, results: E2EResults) -> None:
        """Pre-flight failure suppresses success rate; HTML structure is still valid."""
        html = load_html_file(results.output_dir / COMBINED_SUMMARY_FILENAME)
        verify_html_structure(html)

    def test_combined_success_rate_matches_expectation(
        self, results: E2EResults
    ) -> None:
        """Pre-flight failure replaces the success rate with '--' in the combined dashboard."""
        html = load_html_file(results.output_dir / COMBINED_SUMMARY_FILENAME)
        assert "<strong>--</strong>" in html, (
            "Expected '--' placeholder for success rate in pre-flight failure combined_summary"
        )

    # -------------------------------------------------------------------------
    # Pre-flight failure specific tests
    # -------------------------------------------------------------------------

    def test_combined_dashboard_shows_preflight_failure_banner(
        self, results: E2EResults
    ) -> None:
        """Combined dashboard shows a pre-flight failure banner alongside Robot results."""
        html = load_html_file(results.output_dir / COMBINED_SUMMARY_FILENAME)
        assert "preflight-failure" in html, (
            "Expected pre-flight failure banner CSS class in combined_summary.html"
        )
        assert "Pre-flight failure" in html, (
            "Expected 'Pre-flight failure' text in combined_summary.html"
        )

    def test_preflight_failure_report_exists(self, results: E2EResults) -> None:
        """Pre-flight failure detail report exists under pyats_results/."""
        failure_report = (
            results.output_dir / PYATS_RESULTS_DIRNAME / PRE_FLIGHT_FAILURE_FILENAME
        )
        assert failure_report.exists(), (
            f"Expected pre-flight failure report at "
            f"{PYATS_RESULTS_DIRNAME}/{PRE_FLIGHT_FAILURE_FILENAME}"
        )


# =============================================================================
# TAG FILTERING SCENARIO TESTS
# =============================================================================


def _get_robot_test_names(results: E2EResults) -> list[str]:
    """Extract Robot test names from output.xml."""
    xml_path = results.output_dir / ROBOT_RESULTS_DIRNAME / "output.xml"
    return [t.get("name", "") for t in ET.parse(xml_path).getroot().findall(".//test")]


class TestE2ETagFilterInclude(E2ECombinedTestBase):
    """E2E tests for tag filtering with --include bgp.

    Scenario: --include bgp
    Expected: 1 Robot test (bgp-tagged), 1 PyATS test (verify_bgp_api.py)
    """

    @pytest.fixture
    def results(self, e2e_tag_filter_include_results: E2EResults) -> E2EResults:
        """Provide tag filter include scenario results."""
        return e2e_tag_filter_include_results

    # -------------------------------------------------------------------------
    # Tag filtering specific tests
    # -------------------------------------------------------------------------

    def test_only_bgp_robot_test_in_output_xml(self, results: E2EResults) -> None:
        """Verify output.xml contains only the bgp-tagged Robot test."""
        test_names = _get_robot_test_names(results)

        assert len(test_names) == 1, (
            f"Expected 1 Robot test, got {len(test_names)}: {test_names}"
        )
        assert "bgp" in test_names[0].lower(), (
            f"Expected bgp-related test, got: {test_names[0]}"
        )

    def test_only_bgp_pyats_test_executed(self, results: E2EResults) -> None:
        """Verify only the bgp-tagged PyATS test was discovered and executed."""
        assert "verify_bgp_api" in results.filtered_stdout, (
            "Expected verify_bgp_api in output"
        )
        assert "verify_ospf_api" not in results.filtered_stdout, (
            "Did not expect verify_ospf_api (should be filtered out by --include bgp)"
        )


class TestE2ETagFilterExclude(E2ECombinedTestBase):
    """E2E tests for tag filtering with --exclude osp* (wildcard pattern).

    Scenario: --exclude osp*
    Expected: 1 Robot test (bgp-tagged, ospf excluded), 1 PyATS test (verify_bgp_api.py)
    """

    @pytest.fixture
    def results(self, e2e_tag_filter_exclude_results: E2EResults) -> E2EResults:
        """Provide tag filter exclude scenario results."""
        return e2e_tag_filter_exclude_results

    # -------------------------------------------------------------------------
    # Tag filtering specific tests
    # -------------------------------------------------------------------------

    def test_ospf_robot_test_excluded_from_output_xml(
        self, results: E2EResults
    ) -> None:
        """Verify output.xml does not contain the ospf-tagged Robot test."""
        test_names = _get_robot_test_names(results)

        assert len(test_names) == 1, (
            f"Expected 1 Robot test, got {len(test_names)}: {test_names}"
        )
        assert "ospf" not in test_names[0].lower(), (
            f"Expected ospf test to be excluded, got: {test_names[0]}"
        )

    def test_ospf_pyats_test_excluded(self, results: E2EResults) -> None:
        """Verify the ospf-tagged PyATS test was filtered out."""
        assert "verify_bgp_api" in results.filtered_stdout, (
            "Expected verify_bgp_api in output"
        )
        assert "verify_ospf_api" not in results.filtered_stdout, (
            "Did not expect verify_ospf_api (should be filtered out by --exclude osp*)"
        )


class TestE2ETagFilterCombined(E2ECombinedTestBase):
    """E2E tests for tag filtering with --include api-only.

    Scenario: --include api-only
    Expected: 0 Robot tests (no api-only tag), 1 PyATS test (verify_ospf_api.py has api-only)
    """

    @pytest.fixture
    def results(self, e2e_tag_filter_combined_results: E2EResults) -> E2EResults:
        """Provide tag filter combined scenario results."""
        return e2e_tag_filter_combined_results

    # -------------------------------------------------------------------------
    # Tag filtering specific tests
    # -------------------------------------------------------------------------

    def test_only_ospf_api_pyats_test_executed(self, results: E2EResults) -> None:
        """Verify only verify_ospf_api.py was discovered (has api-only tag)."""
        assert "verify_ospf_api" in results.filtered_stdout, (
            "Expected verify_ospf_api in output (has api-only tag)"
        )
        assert "verify_bgp_api" not in results.filtered_stdout, (
            "Did not expect verify_bgp_api (does not have api-only tag)"
        )


class TestE2ETagFilterNoMatch(E2ECombinedTestBase):
    """E2E tests for tag filtering with --exclude bgpORospf (no tests match).

    Scenario: --exclude bgpORospf
    Expected: 0 Robot tests, 0 PyATS tests, exit code 252 (no tests found)
    """

    @pytest.fixture
    def results(self, e2e_tag_filter_no_match_results: E2EResults) -> E2EResults:
        """Provide tag filter no match scenario results."""
        return e2e_tag_filter_no_match_results

    # -------------------------------------------------------------------------
    # Tag filtering specific tests
    # -------------------------------------------------------------------------

    def test_no_pyats_tests_in_stdout(self, results: E2EResults) -> None:
        """Verify no PyATS tests appear in stdout when all filtered."""
        assert "verify_bgp_api.py" not in results.filtered_stdout, (
            "Did not expect verify_bgp_api.py (excluded by bgpORospf)"
        )
        assert "verify_ospf_api.py" not in results.filtered_stdout, (
            "Did not expect verify_ospf_api.py (excluded by bgpORospf)"
        )
        assert (
            "No pyATS tests matching tag filter (exclude: 'bgp OR ospf')"
            in results.filtered_stdout
        ), "Expected descriptive filter message in stdout"
