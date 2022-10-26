# -*- coding: utf-8 -*-

# Copyright: (c) 2022, Daniel Schmidt <danischm@cisco.com>

import pabot.pabot


def run_pabot(path: str, include: str = "", exclude: str = "") -> None:
    """Run pabot"""
    args = ["--pabotlib"]
    if include:
        args.extend(["--include", include])
    if exclude:
        args.extend(["--exclude", exclude])
    args.extend(
        ["-d", path, "--skiponfailure", "non-critical", "-x", "xunit.xml", path]
    )
    pabot.pabot.main(args)
