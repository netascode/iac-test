# -*- coding: utf-8 -*-

# Copyright: (c) 2022, Daniel Schmidt <danischm@cisco.com>

import importlib.util
import logging
import os
import pathlib
import re
import sys
from typing import Any, Dict, List

from jinja2 import ChainableUndefined, Environment, FileSystemLoader  # type: ignore

from iac_test import yaml

logger = logging.getLogger(__name__)


class RobotWriter:
    def __init__(
        self, data_paths: List[str], filters_path: str, tests_path: str
    ) -> None:
        logger.info("Loading yaml files from %s", data_paths)
        self.data = yaml.load_yaml_files(data_paths)
        self.filters: Dict[str, Any] = {}
        if filters_path:
            logger.info("Loading filters")
            for filename in os.listdir(filters_path):
                if filename.endswith(".py"):
                    file_path = os.path.join(filters_path, filename)
                    spec = importlib.util.spec_from_file_location(
                        "iac_test.filters", file_path
                    )
                    if spec is not None:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules["iac_test.filters"] = mod
                        if spec.loader is not None:
                            spec.loader.exec_module(mod)
                            self.filters[mod.Filter.name] = mod.Filter
        self.tests: Dict[str, Any] = {}
        if tests_path:
            logger.info("Loading tests")
            for filename in os.listdir(tests_path):
                if filename.endswith(".py"):
                    file_path = os.path.join(tests_path, filename)
                    spec = importlib.util.spec_from_file_location(
                        "iac_test.tests", file_path
                    )
                    if spec is not None:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules["iac_test.tests"] = mod
                        if spec.loader is not None:
                            spec.loader.exec_module(mod)
                            self.tests[mod.Test.name] = mod.Test

    def render_template(
        self,
        template_path: str,
        output_path: str,
        env: Environment,
        **kwargs: Dict[str, Any]
    ) -> None:
        """Render single robot jinja template"""
        logger.info("Render robot template: %s", template_path)
        # create output directory if it does not exist yet
        pathlib.Path(os.path.dirname(output_path)).mkdir(parents=True, exist_ok=True)

        template = env.get_template(template_path)
        result = template.render(self.data, **kwargs)

        # remove extra empty lines
        lines = result.splitlines()
        cleaned_lines = []
        for index, line in enumerate(lines):
            if len(line.strip()):
                cleaned_lines.append(line)
            else:
                if index + 1 < len(lines):
                    next_line = lines[index + 1]
                    if len(next_line) and not next_line[0].isspace():
                        cleaned_lines.append(line)
        result = os.linesep.join(cleaned_lines)

        with open(output_path, "w") as file:
            file.write(result)

    def _fix_duplicate_path(self, *paths: str) -> str:
        """Helper function to detect existing paths with non-matching case. Returns a unique path to work with case-insensitve filesystems."""
        directory = os.path.join(*paths[:-1])
        if os.path.exists(directory):
            entries = os.listdir(directory)
            lower_case_entries = [path.lower() for path in entries]
            if paths[-1].lower() in lower_case_entries and paths[-1] not in entries:
                return os.path.join(*paths[:-1], "_" + paths[-1])
        return os.path.join(*paths)

    def write(self, templates_path: str, output_path: str) -> None:
        """Render Robot test suites."""
        env = Environment(
            loader=FileSystemLoader(templates_path),
            undefined=ChainableUndefined,
            lstrip_blocks=True,
            trim_blocks=True,
        )
        for name, filter in self.filters.items():
            env.filters[name] = filter.filter
        for name, test in self.tests.items():
            env.tests[name] = test.test

        for dir, _, files in os.walk(templates_path):
            for filename in files:
                rel = os.path.relpath(dir, templates_path)
                t_path = os.path.join(rel, filename)

                # search for directives
                pattern = re.compile("{#(.+?)#}")
                content = ""
                next_template = False
                with open(os.path.join(dir, filename), "r") as file:
                    content = file.read()
                for match in re.finditer(pattern, content):
                    params = match.group().split(" ")
                    if len(params) == 6 and params[1] in [
                        "iterate_list",
                        "iterate_list_folder",
                    ]:
                        next_template = True
                        path = params[2].split(".")
                        attr = params[3]
                        elem = self.data
                        for p in path:
                            elem = elem.get(p, {})
                        if not isinstance(elem, list):
                            continue
                        for item in elem:
                            value = str(item.get(attr))
                            if value is None:
                                continue
                            extra = {}
                            if "[" in params[4]:
                                index = params[4].split("[")[1].split("]")[0]
                                extra_list = [None] * (int(index) + 1)
                                extra_list[int(index)] = value
                                extra = {params[4].split("[")[0]: extra_list}
                            else:
                                extra = {params[4]: value}
                            if params[1] == "iterate_list":
                                o_dir = self._fix_duplicate_path(
                                    output_path, rel, value
                                )
                                o_path = os.path.join(o_dir, filename)
                            else:
                                foldername = os.path.splitext(filename)[0]
                                new_filename = (
                                    value + "." + os.path.splitext(filename)[1][1:]
                                )
                                o_path = self._fix_duplicate_path(
                                    output_path, rel, foldername, new_filename
                                )
                            self.render_template(t_path, o_path, env, **extra)
                if next_template:
                    continue

                o_path = os.path.join(output_path, rel, filename)
                self.render_template(t_path, o_path, env)
