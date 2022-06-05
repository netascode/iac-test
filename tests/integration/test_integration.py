# -*- coding: utf-8 -*-

# Copyright: (c) 2022, Daniel Schmidt <danischm@cisco.com>

import os

from click.testing import CliRunner
import pytest

import iac_test.cli.main

pytestmark = pytest.mark.integration


def test_iac_test(tmpdir):
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data/"
    templates_path = "tests/integration/fixtures/templates/"
    result = runner.invoke(
        iac_test.cli.main.main,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            tmpdir,
        ],
    )
    assert result.exit_code == 0


def test_iac_test_filter(tmpdir):
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data/"
    templates_path = "tests/integration/fixtures/templates_filter/"
    filters_path = "tests/integration/fixtures/filters/"
    result = runner.invoke(
        iac_test.cli.main.main,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-f",
            filters_path,
            "-o",
            tmpdir,
        ],
    )
    assert result.exit_code == 0


def test_iac_test_render(tmpdir):
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data/"
    templates_path = "tests/integration/fixtures/templates_fail/"
    result = runner.invoke(
        iac_test.cli.main.main,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            tmpdir,
            "--render-only",
        ],
    )
    assert result.exit_code == 0


def test_iac_test_list(tmpdir):
    runner = CliRunner()
    data_path = "tests/integration/fixtures/data/"
    templates_path = "tests/integration/fixtures/templates_list/"
    result = runner.invoke(
        iac_test.cli.main.main,
        [
            "-d",
            data_path,
            "-t",
            templates_path,
            "-o",
            tmpdir,
        ],
    )
    assert os.path.exists(os.path.join(tmpdir, "ABC"))
    assert os.path.exists(os.path.join(tmpdir, "DEF"))
    assert result.exit_code == 0
