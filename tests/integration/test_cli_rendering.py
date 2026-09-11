# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Template rendering tests for nac-test CLI.

This module contains integration tests that verify template rendering
functionality including render-only mode, list rendering variations,
chunked rendering, and merged data model output.
"""

import filecmp
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import nac_test.cli.main
from nac_test.core.constants import (
    EXIT_ERROR,
    MERGED_DATA_FILENAME,
    ROBOT_RESULTS_DIRNAME,
)

pytestmark = [pytest.mark.integration, pytest.mark.windows]


def verify_file_content(expected_yaml_path: Path, output_dir: Path) -> None:
    """Verify that files in output_dir match the expected content from YAML.

    Args:
        expected_yaml_path: Path to YAML file with structure {filename: content}.
        output_dir: Base directory where the files should exist.

    Raises:
        AssertionError: If any file content doesn't match expected content.
    """
    with open(expected_yaml_path) as f:
        expected_files = yaml.safe_load(f)

    for filename, expected_content in expected_files.items():
        file_path = output_dir / filename
        assert file_path.exists(), f"Expected file does not exist: {file_path}"

        actual_content = file_path.read_text()
        assert actual_content.strip() == expected_content.strip(), (
            f"Content mismatch in {filename}:\n"
            f"Expected:\n{expected_content}\n"
            f"Actual:\n{actual_content}"
        )


def test_render_only_mode_succeeds_with_valid_templates(tmp_path: Path) -> None:
    """Test that render-only mode succeeds with valid template files.

    Verifies that the --render-only flag causes nac-test to render
    templates without executing any tests.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
    """
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data/"
    templates_path = "tests/integration/fixtures/templates_fail/"
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
            "--render-only",
        ],
    )
    assert result.exit_code == 0, (
        f"Render-only mode should succeed with valid templates, got exit code "
        f"{result.exit_code}: {result.output}"
    )


def test_render_only_mode_fails_with_missing_template_variables(
    tmp_path: Path,
) -> None:
    """Test that render-only mode fails when template variables are missing.

    Verifies that the CLI properly reports an error when templates
    reference variables not present in the data files.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
    """
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data/"
    templates_path = "tests/integration/fixtures/templates_missing/"
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
            "--render-only",
        ],
    )
    assert result.exit_code == EXIT_ERROR, (
        f"Render-only mode should fail with missing variables, got exit code "
        f"{result.exit_code}: {result.output}"
    )


def test_render_only_mode_succeeds_with_default_filter_for_missing_variables(
    tmp_path: Path,
) -> None:
    """Test that render-only mode succeeds when missing variables have defaults.

    Verifies that templates using Jinja default filters for missing
    variables render successfully without errors.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
    """
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data/"
    templates_path = "tests/integration/fixtures/templates_missing_default/"
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
            "--render-only",
        ],
    )
    assert result.exit_code == 0, (
        f"Render-only mode should succeed with default filter, got exit code "
        f"{result.exit_code}: {result.output}"
    )


def test_list_rendering_creates_device_folders(tmp_path: Path) -> None:
    """Test that list rendering creates separate folders per device.

    Verifies that when rendering templates over a list of items,
    the CLI creates a separate folder for each item containing
    the rendered template file.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
    """
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data_list/"
    templates_path = "tests/integration/fixtures/templates_list/"
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
            "--render-only",
        ],
    )
    robot_results_dir = tmp_path / ROBOT_RESULTS_DIRNAME
    assert (robot_results_dir / "ABC" / "test1.robot").exists(), (
        "Expected device folder ABC/test1.robot to be created"
    )
    assert (robot_results_dir / "DEF" / "test1.robot").exists(), (
        "Expected device folder DEF/test1.robot to be created"
    )
    assert (robot_results_dir / "_abC" / "test1.robot").exists(), (
        "Expected device folder _abC/test1.robot to be created"
    )
    assert result.exit_code == 0, (
        f"List rendering should succeed, got exit code {result.exit_code}: "
        f"{result.output}"
    )


def test_list_rendering_creates_device_files_in_shared_folder(tmp_path: Path) -> None:
    """Test that list rendering can create separate files per device in one folder.

    Verifies that when rendering templates over a list with folder mode,
    the CLI creates a single folder containing a separate file for each item.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
    """
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data_list/"
    templates_path = "tests/integration/fixtures/templates_list_folder/"
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
            "--render-only",
        ],
    )
    robot_results_dir = tmp_path / ROBOT_RESULTS_DIRNAME
    assert (robot_results_dir / "test1" / "ABC.robot").exists(), (
        "Expected device file test1/ABC.robot to be created"
    )
    assert (robot_results_dir / "test1" / "DEF.robot").exists(), (
        "Expected device file test1/DEF.robot to be created"
    )
    assert (robot_results_dir / "test1" / "_abC.robot").exists(), (
        "Expected device file test1/_abC.robot to be created"
    )
    assert result.exit_code == 0, (
        f"List rendering with folder mode should succeed, got exit code "
        f"{result.exit_code}: {result.output}"
    )


def test_chunked_list_rendering_produces_expected_content(tmp_path: Path) -> None:
    """Test that chunked list rendering produces correctly chunked output files.

    Verifies that when rendering templates with chunked iteration,
    the CLI creates output files with content split into the expected
    chunks matching the expected_content.yaml specification.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
    """
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data_list_chunked/"
    templates_path = "tests/integration/fixtures/templates_list_chunked/"
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
            "--render-only",
        ],
    )
    assert result.exit_code == 0, (
        f"Chunked list rendering should succeed, got exit code {result.exit_code}: "
        f"{result.output}"
    )
    robot_results_dir = tmp_path / ROBOT_RESULTS_DIRNAME
    assert not (robot_results_dir / "ABC" / "test1.robot").exists(), (
        "Chunked rendering should not create individual device folders"
    )
    assert not (robot_results_dir / "DEF" / "test1.robot").exists(), (
        "Chunked rendering should not create individual device folders"
    )
    # Verify files and their content match expected content
    verify_file_content(
        Path(templates_path) / "expected_content.yaml", robot_results_dir
    )


def test_chunked_list_rendering_produces_expected_content_with_dict_parent(
    tmp_path: Path,
) -> None:
    """Test chunked rendering for a 2-level object_path whose parent is a dict.

    Verifies that when rendering templates with chunked iteration,
    with a 2-level object_path whose parent is a dict,
    the CLI creates output files with content split into the expected
    chunks matching the expected_content.yaml specification.
    Test uses object_path = "objects.hosts", where "objects" is a dict and "hosts" is a list.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.
    """
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data_list_chunked_dict/"
    templates_path = "tests/integration/fixtures/templates_list_chunked_dict/"
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
            "--render-only",
        ],
    )
    assert result.exit_code == 0, (
        f"Chunked dict-parent rendering should succeed, got exit code "
        f"{result.exit_code}: {result.output}"
    )
    robot_results_dir = tmp_path / ROBOT_RESULTS_DIRNAME
    # Verify files and their content match expected content
    verify_file_content(
        Path(templates_path) / "expected_content.yaml", robot_results_dir
    )


def test_merged_data_model_creates_default_filename(tmp_path: Path) -> None:
    """Test that the merged data model is written with the expected filename and content."""
    runner = CliRunner()
    templates_path = "tests/integration/fixtures/templates/"
    output_model_path = tmp_path / MERGED_DATA_FILENAME
    data_dir = Path("tests/integration/fixtures/data_merge")
    expected_model_path = data_dir / "result.json"

    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            str(data_dir / "file1.yaml"),
            "-d",
            str(data_dir / "file2.yaml"),
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
            "--render-only",
        ],
    )
    assert result.exit_code == 0, (
        f"Merged data model creation should succeed, got exit code "
        f"{result.exit_code}: {result.output}"
    )
    assert output_model_path.exists(), (
        f"Merged data model file should exist at {output_model_path}"
    )
    assert filecmp.cmp(output_model_path, expected_model_path, shallow=False), (
        f"Merged data model content should match expected content from "
        f"{expected_model_path}"
    )
    # By default (NAC_TEST_DUMP_YAML_DATA_MODEL unset) no YAML companion is written.
    # The env-var-enabled case is covered in test_yaml_data_model_dump.py.
    assert not output_model_path.with_suffix(".yaml").exists(), (
        "YAML data model should not be created unless NAC_TEST_DUMP_YAML_DATA_MODEL is set"
    )


def test_render_only_without_controller_credentials(tmp_path: Path) -> None:
    """Render-only mode works without controller environment variables.
    All other tests in this module implicitly also test this, but this
    is important enough that it warrants an explicit test.
    """

    data_file = tmp_path / "data.yaml"
    data_file.write_text("device: Router1\nip: 192.168.1.1")

    template = tmp_path / "templates" / "test.robot"
    template.parent.mkdir(parents=True)
    template.write_text(
        "*** Test Cases ***\nVerify {{ device }}\n    Log    IP: {{ ip }}"
    )

    runner = CliRunner()
    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            str(data_file),
            "-t",
            str(template.parent),
            "-o",
            str(tmp_path / "output"),
            "--render-only",
        ],
    )

    assert result.exit_code == 0
    output = (tmp_path / "output" / ROBOT_RESULTS_DIRNAME / "test.robot").read_text()
    assert "Verify Router1" in output
    assert "{{" not in output


def test_dict_key_attribute_collision_renders_key_values(tmp_path: Path) -> None:
    """Comprehensive test for key-attribute collision handling.

    Tests three categories with a single data dir + template dir:
    1. test.robot — collision keys (items, keys) via bracket notation; non-collision (tag) via dot access
    2. test_methods.robot — .items()/.get()/.keys()/.values() method calls on clean mappings
    3. test_selectattr.robot — selectattr/rejectattr/map(attribute=) with collision key 'tag'
    """
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data_attr_collision"
    templates_path = "tests/integration/fixtures/templates_attr_collision"

    result = runner.invoke(
        nac_test.cli.main.app,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"CLI rendering failed:\n{result.output}"

    output_dir = tmp_path / ROBOT_RESULTS_DIRNAME

    # --- test.robot: collision keys via bracket notation ---
    output_collision = (output_dir / "test.robot").read_text()
    assert re.search(r"Should Be Equal\s+100\s+100\s+msg=tag_abc", output_collision), (
        "tag key dot-access did not render '100' for child abc"
    )
    assert re.search(
        r"Should Be Equal\s+foo\s+foo\s+msg=items_key_abc", output_collision
    ), "items collision key did not render 'foo' via bracket notation"
    assert re.search(
        r"Should Be Equal\s+bar\s+bar\s+msg=keys_key_abc", output_collision
    ), "keys collision key did not render 'bar' via bracket notation"
    assert "{{" not in output_collision, "Unresolved Jinja2 expression in test.robot"

    # --- test_selectattr.robot: selectattr/rejectattr/map ---
    output_select = (output_dir / "test_selectattr.robot").read_text()
    assert re.search(
        r"Should Be Equal\s+Ethernet1/1\s+Ethernet1/1\s+msg=selectattr_name",
        output_select,
    ), "selectattr didn't find trunk port Ethernet1/1"
    assert re.search(
        r"Should Be Equal\s+Ethernet1/2\s+Ethernet1/2\s+msg=rejectattr_name",
        output_select,
    ), "rejectattr didn't find non-trunk port Ethernet1/2"
    assert re.search(
        r"Should Be Equal\s+trunk,access\s+trunk,access\s+msg=map_tags",
        output_select,
    ), "map(attribute='tag') didn't produce 'trunk,access'"
    assert "{{" not in output_select, (
        "Unresolved Jinja2 expression in test_selectattr.robot"
    )
