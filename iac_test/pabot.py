# -*- coding: utf-8 -*-

# Copyright: (c) 2022, Daniel Schmidt <danischm@cisco.com>

import pabot.pabot


def run_pabot(path: str) -> None:
    """Run pabot"""
    pabot.pabot.main(
        ["--pabotlib", "-d", path, "--skiponfailure", "non-critical", path]
    )
