# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Unit tests for DataMerger.

Covers:
- merge_data_files: empty input edge case, ruamel type stripping contract
- write_merged_data_model: output filename, JSON roundtrip, YAML content parity
"""

import datetime
import json
from pathlib import Path

from ruamel.yaml import CommentedMap, CommentedSeq

from nac_test.data_merger import DataMerger
from nac_test.utils.yaml import safe_load


class TestMergeDataFiles:
    """Tests for DataMerger.merge_data_files()."""

    def test_merge_empty_list_returns_empty_dict(self) -> None:
        """An empty path list returns an empty dict rather than raising."""
        result = DataMerger.merge_data_files([])
        assert result == {}


class TestWriteMergedDataModel:
    """Tests for DataMerger.write_merged_data_model()."""

    def test_returns_path_to_written_file(self, tmp_path: Path) -> None:
        """write_merged_data_model returns the path of the file it created."""
        returned = DataMerger.write_merged_data_model({"key": "value"}, tmp_path)
        assert returned == DataMerger.merged_data_path(tmp_path)
        assert returned.exists()

    def test_writes_no_extra_files(self, tmp_path: Path) -> None:
        """Exactly one file is created in the output directory."""
        DataMerger.write_merged_data_model({"key": "value"}, tmp_path)
        assert len(list(tmp_path.iterdir())) == 1

    def test_roundtrip_preserves_content(self, tmp_path: Path) -> None:
        """Data written to JSON can be read back with the same structure."""
        original = {"host": "router1", "vlan": 100, "tags": ["a", "b"]}
        output_path = DataMerger.write_merged_data_model(original, tmp_path)
        with open(output_path, encoding="utf-8") as f:
            reloaded = json.load(f)
        assert reloaded["host"] == "router1"
        assert reloaded["vlan"] == 100
        assert list(reloaded["tags"]) == ["a", "b"]

    def test_yaml_content_matches_json_when_dumped(self, tmp_path: Path) -> None:
        """When dump_yaml=True, YAML content matches JSON content.

        This test verifies the content parity contract: the YAML and JSON files
        contain identical data when deserialized.
        """
        # Create test data
        test_data = {
            "host": "router1",
            "vlan": 100,
            "tags": ["a", "b"],
            "nested": {"key": "value"},
        }

        # Write the merged data model with YAML enabled
        DataMerger.write_merged_data_model(test_data, tmp_path, dump_yaml=True)

        # Verify both files exist
        json_path = tmp_path / "merged_data_model_test_variables.json"
        yaml_path = tmp_path / "merged_data_model_test_variables.yaml"

        assert json_path.exists(), "JSON file should be created"
        assert yaml_path.exists(), "YAML file should be created when dump_yaml=True"

        # Load both and verify content matches
        with open(json_path, encoding="utf-8") as f:
            json_data = json.load(f)
        yaml_data = safe_load(yaml_path.read_text(encoding="utf-8"))

        assert json_data == yaml_data, (
            f"YAML and JSON content should match.\nJSON: {json_data}\nYAML: {yaml_data}"
        )

    def test_non_json_native_values_do_not_crash(self, tmp_path: Path) -> None:
        """Values ruamel's safe loader yields that JSON cannot natively encode
        (e.g. datetime.date from an unquoted YAML date) are stringified rather
        than raising TypeError,         so a run is never aborted at merge time.
        """
        data = {"cert_valid_until": datetime.date(2025, 1, 15)}
        output_path = DataMerger.write_merged_data_model(data, tmp_path)
        with open(output_path, encoding="utf-8") as f:
            reloaded = json.load(f)
        assert reloaded["cert_valid_until"] == "2025-01-15"

    def test_int_mapping_keys_are_coerced_to_strings(self, tmp_path: Path) -> None:
        """JSON has no non-string keys: integer mapping keys (e.g. VLAN IDs used
        as keys) are coerced to strings on write. This documents the known,
        breaking-change behavior versus the previous YAML format.
        """
        data = {"vlans": {100: "prod", 200: "stg"}}
        output_path = DataMerger.write_merged_data_model(data, tmp_path)
        with open(output_path, encoding="utf-8") as f:
            reloaded = json.load(f)
        assert reloaded["vlans"] == {"100": "prod", "200": "stg"}


def _assert_no_ruamel_types(value: object, path: str = "root") -> None:
    """Recursively assert no CommentedMap/CommentedSeq anywhere in the tree."""
    assert not isinstance(value, CommentedMap), f"{path} is CommentedMap"
    assert not isinstance(value, CommentedSeq), f"{path} is CommentedSeq"
    if isinstance(value, dict):
        for k, v in value.items():
            _assert_no_ruamel_types(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _assert_no_ruamel_types(v, f"{path}[{i}]")


class TestMergeDataFilesContract:
    """Contract: merge_data_files never returns CommentedMap/CommentedSeq."""

    def test_no_ruamel_types_in_output(self, tmp_path: Path) -> None:
        """Data loaded from YAML must be stripped of ruamel metadata types."""
        yaml_file = tmp_path / "data.yaml"
        yaml_file.write_text(
            "host: router1\ntag: vlan100\nitems:\n  - name: item1\n    anchor: anc1\n"
        )
        result = DataMerger.merge_data_files([yaml_file])
        _assert_no_ruamel_types(result)
        assert result["host"] == "router1"
        assert result["tag"] == "vlan100"
        assert result["items"][0]["name"] == "item1"

    def test_nested_list_of_dicts_roundtrip(self, tmp_path: Path) -> None:
        """Nested list-of-list-of-dict YAML produces plain types and supports .get()."""
        yaml_file = tmp_path / "nested.yaml"
        yaml_file.write_text(
            "---\nroot:\n  feature_profiles:\n    - - name: profile1\n"
        )
        result = DataMerger.merge_data_files([yaml_file])
        _assert_no_ruamel_types(result)

        feature_profiles = result["root"]["feature_profiles"]
        assert type(feature_profiles) is list
        assert type(feature_profiles[0]) is list
        assert type(feature_profiles[0][0]) is dict
        assert feature_profiles[0][0] == {"name": "profile1"}
