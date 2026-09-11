#!/usr/bin/env python
# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

import argparse
import os
from typing import Any

import yaml
from unicon.mock import mock_device


def extend_mock_data(mock_data: dict[str, Any], mock_data_dir: str) -> dict[str, Any]:
    for file in os.listdir(mock_data_dir):
        if file.endswith(".yaml"):
            # for file named: <OS>_mock_data_<state_name>.yaml, get the state name
            with open(os.path.join(mock_data_dir, file), encoding="utf-8") as f:
                states = yaml.safe_load(f) or []
            for state in states:
                if state in mock_data:
                    mock_data[state]["commands"] = {
                        **mock_data[state]["commands"],
                        **states[state]["commands"],
                    }
                    mock_data[state]["prompt"] = states[state].get(
                        "prompt", mock_data[state]["prompt"]
                    )
                else:
                    mock_data[state] = states[state]
    return mock_data


valid = {
    "ios": {"state": "exec"},
    "iosxe": {"state": "enable_isr"},
    "nxos": {"state": "exec"},
    "iosxr": {"state": "login"},
    "vos": {"state": "vos_connect"},
}

parser = argparse.ArgumentParser()
parser.add_argument("os", help="device os")
parser.add_argument("--hostname", help="device hostname", default="Router")
parser.add_argument("--state", help="device state")
args = parser.parse_args()

router_os = args.os
hostname = args.hostname
state = args.state


if not router_os:
    raise Exception(f"No OS provided, please use one of {', '.join(valid.keys())}")
elif router_os not in valid:
    raise Exception(
        f"'{router_os}' is not a valid OS, please use one of {', '.join(valid.keys())}"
    )
else:
    target = {
        "device_os": router_os,
        "hostname": hostname,
        "state": state if state else valid[router_os]["state"],
    }

md = mock_device.MockDevice(**target)
data_dir = os.path.dirname(__file__) + f"/mock_data/{router_os}/"
if os.path.exists(data_dir):
    md.mock_data = extend_mock_data(md.mock_data, data_dir)
md.run()
