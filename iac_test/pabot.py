# -*- coding: utf-8 -*-

# Copyright: (c) 2022, Daniel Schmidt <danischm@cisco.com>

from typing import List

import pabot.pabot


def run_pabot(
    path: str, include: List[str] = [], exclude: List[str] = [], dry_run: bool = False
) -> None:
    """Run pabot"""
    args = ["--pabotlib"]
    if dry_run:
        args.append("--dryrun")
    for i in include:
        args.extend(["--include", i])
    for e in exclude:
        args.extend(["--exclude", e])
    args.extend(
        ["-d", path, "--skiponfailure", "non-critical", "-x", "xunit.xml", path]
    )
    pabot.pabot.main(args)
